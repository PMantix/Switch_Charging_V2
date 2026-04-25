"""
Switching Circuit V2 — RP2040-Zero Firmware

Receives line-delimited commands over USB serial from the Raspberry Pi
and drives the H-bridge gate signals via UCC5304 drivers.

Serial protocol (USB CDC, line-delimited):
  Commands (Pi → RP2040):
    S <P1> <P2> <N1> <N2>   Set FET states directly (auto-halts switching)
    Q                        Query current FET states
    I                        Read INA226 sensors (one-shot)
    T <hz>                   Start streaming sensors at <hz> (0 = stop)
    A <avg>                  INA226 averaging samples (1/4/16/64/128/256/512/1024)
    V <every>                Bus-voltage decimation: read bus once per <every>
                             shunt sweeps (1 = every sample, 0 = never)
    M                        Query max stream rate for current AVG/V settings
    Z [n]                    Profile emit_stream_line() — times n=50 loops by
                             default, updates _measured_emit_us and therefore
                             _max_stream_hz to reflect real firmware throughput
    L <R> <G> <B>            Set NeoPixel LED color (0-255 each)
    P                        Ping (heartbeat) — reply includes time.ticks_us()
                             snapshot for Pi-side clock-offset estimation
    R                        Re-scan INA226 sensors
    U <vsh_us> <vbus_us>     Set INA226 conversion times in µs (must be one
                             of 140/204/332/588/1100/2116/4156/8244 each).
                             Re-programs sensors and recomputes max_hz.
    E                        Report gc.mem_free() — for leak diagnostics
    X                        Soft reset
    C <n> <s1> ... <sn>      Program switching Cycle: n packed states
                             (each 0-15, bits P1<<3|P2<<2|N1<<1|N2)
    F <period_us>            Step period in microseconds (preserves index)
    G                        Go — start periodic switching
    H                        Halt — stop switching, all FETs off
    K                        tick once (advance one step, for debug)

  Responses (RP2040 → Pi):
    OK S <P1> <P2> <N1> <N2>     FET state confirmation
    OK Q <P1> <P2> <N1> <N2>     FET state query response
    OK I <json>                   INA226 readings (one-shot)
    OK T <hz>                     Streaming started/stopped (hz = actual, may be capped)
    OK A <avg> <max_hz>           Averaging applied; max_hz is computed cap
    OK V <every> <max_hz>         Decimation applied; max_hz is computed cap
    OK M <avg> <every> <max_hz>   Current sensor profile snapshot
    OK Z <n> <avg_us> <max_hz>    Emit profile: n loops averaged to avg_us per
                                  emit; max_hz recomputed from that measurement
    D <t_us> <P1v> <P1i> <P2v> <P2i> <N1v> <N1i> <N2v> <N2i>   Stream data.
                                  t_us is the firmware time.ticks_us() captured
                                  at the moment the INA226 sweep began (i.e.
                                  the sample-capture timestamp). The Pi
                                  converts it to its own monotonic clock via
                                  the offset learned from P, so each D row
                                  can be anchored in the firmware clock and
                                  the engine can compute the step that was
                                  actually live when the sample was captured
                                  (eliminates a one-step label lag at high
                                  switching frequencies caused by ~4.5 ms
                                  emit latency between sweep and stdout).
    OK L                          LED set
    OK P <t_us>                   Pong; t_us is firmware time.ticks_us() at
                                   reply-build time. Pi pairs it with its own
                                   monotonic() before send and after reply to
                                   compute "firmware_seconds - pi_seconds"
                                   offset so future G ticks_us values can be
                                   converted to Pi monotonic() directly.
    OK C <n>                      Sequence programmed (n states)
    OK F <us>                     Period set
    OK G <us> <n> <t_us>          Switching started; t_us is the firmware
                                   time.ticks_us() at the moment state 0 was
                                   applied. Pi uses this to anchor its step
                                   estimate to the firmware clock rather than
                                   its own monotonic() at reply-receipt time
                                   (removes the serial round-trip drift).
    OK H                          Switching halted
    OK K <idx>                    Stepped to idx
    ERR <message>                 Error
"""

import sys
import uselect
import gc
import json
from machine import Pin, I2C, Timer
import neopixel
import time

# ---------------------------------------------------------------------------
# Pin assignments (must match config.py)
# ---------------------------------------------------------------------------
PIN_P1 = 2    # GP2 → UCC5304 U1 → P1 high-side
PIN_P2 = 3    # GP3 → UCC5304 U2 → P2 high-side
PIN_N1 = 4    # GP4 → UCC5304 U3 → N1 low-side
PIN_N2 = 5    # GP5 → UCC5304 U4 → N2 low-side

PIN_SDA = 6   # GP6 → INA226 I2C SDA
PIN_SCL = 7   # GP7 → INA226 I2C SCL

PIN_NEOPIXEL = 16  # GP16 → onboard WS2812

# INA226 I2C addresses
INA226_ADDRS = {
    "P1": 0x40,
    "P2": 0x41,
    "N1": 0x43,
    "N2": 0x45,
}

# Sensor order for streaming (fixed order for compact D lines)
SENSOR_ORDER = ["P1", "P2", "N1", "N2"]

# INA226 register addresses
INA226_REG_CONFIG = 0x00
INA226_REG_SHUNT_V = 0x01
INA226_REG_BUS_V = 0x02
INA226_REG_DIEID = 0xFF

# INA226 constants
INA226_BUS_V_LSB = 1.25e-3       # 1.25 mV per bit
INA226_SHUNT_V_LSB = 2.5e-6      # 2.5 uV per bit

# Shunt resistor value
SHUNT_RESISTOR = 0.1  # 100 mOhm

# INA226 CONFIG register field layout:
#   bits 11-9  AVG       (8 codes → 1, 4, 16, 64, 128, 256, 512, 1024)
#   bits 8-6   VBUSCT    (8 codes → 140, 204, 332, 588, 1100, 2116, 4156, 8244 us)
#   bits 5-3   VSHCT     (same codes)
#   bits 2-0   MODE      (111 = continuous shunt+bus)
_AVG_VALUES = (1, 4, 16, 64, 128, 256, 512, 1024)
# Conversion-time codes — index = register bits, value = µs. Both VSHCT and
# VBUSCT use the same encoding. Runtime-tunable via the U command.
_CT_VALUES = (140, 204, 332, 588, 1100, 2116, 4156, 8244)
_VSHCT_CODE = 0b010       # 332 us — decent SNR/speed balance
_VBUSCT_CODE = 0b010      # 332 us
_VSHCT_US = 332
_VBUSCT_US = 332
_MODE_CONT_BOTH = 0b111
# Per-register I2C read cost. Starts at a conservative theoretical
# estimate (at 1 MHz: ~60 µs per 2-byte transaction ignoring MicroPython
# overhead); replaced with a measured value after the first Z command
# runs on the device. The initial estimate tends to be optimistic
# because MicroPython's Python-to-C bridging per transaction adds
# 100-300 µs that the bus speed calc doesn't capture.
_I2C_READ_US = 200
# Extra per-emit overhead (stdout.write to USB CDC, %-format of 8 floats,
# main-loop polling). Initial estimate conservative; calibrated by Z.
_EMIT_OVERHEAD_US = 500
# Populated by the Z command once measurement runs. When non-zero, this
# overrides the theoretical calc in _max_stream_hz.
_measured_emit_us = 0

# Live sensor profile. AVG and bus decimation can be re-programmed over
# serial with A/V commands; the INA226 config register and the _max_stream_hz
# cap are recomputed from these.
_ina226_avg = 4           # sample count (must be in _AVG_VALUES)
# bus_every=5 default (was 1) — 2026-04-24 sweep showed sustained rate is
# pinned to a ~4 ms format/USB floor (not conversion or I²C), so cutting
# bus reads to 1-in-5 is effectively free SNR-wise and recovers ~20% of
# the per-emit cost (4882 → 4093 µs). Bus voltage is the supply rail and
# changes on a timescale of seconds — decimation is invisible in practice.
_bus_every = 5            # read bus voltage every Nth shunt sweep (0 = never)
_bus_counter = 0          # running count into the decimation cycle
_last_bus_v = [0.0, 0.0, 0.0, 0.0]  # cache so skipped sweeps still emit a value


# ---------------------------------------------------------------------------
# Hardware setup
# ---------------------------------------------------------------------------
fets = [
    Pin(PIN_P1, Pin.OUT, value=0),
    Pin(PIN_P2, Pin.OUT, value=0),
    Pin(PIN_N1, Pin.OUT, value=0),
    Pin(PIN_N2, Pin.OUT, value=0),
]

np = neopixel.NeoPixel(Pin(PIN_NEOPIXEL), 1)
# I2C at 1 MHz (Fm+). INA226 spec allows up to 2.94 MHz; 1 MHz gives
# us ~4× the sweep throughput vs the prior 400 kHz setting, which was
# a significant fraction of the ~4.5 ms per-emit cost observed in the
# 2026-04-24 DOE. Pull-ups on the breadboard are internal (~50 kΩ) —
# if SDA/SCL ever start looking glitchy on a scope, drop to 400 kHz
# here and add external 2.2 kΩ pull-ups instead.
i2c = I2C(1, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=1_000_000)
ina226_present = {}

# Streaming state
stream_hz = 0       # 0 = off
stream_interval = 0  # seconds between stream readings

# Burst recording state
burst_buffer = None   # list of (timestamp_us, readings) or None
burst_target = 0      # target sample count
burst_active = False

# Switching timer state. A machine.Timer drives the tick callback in IRQ
# context so switching timing is independent of main-loop overhead (stdin
# polling, sensor reads, GC pauses). Stack: keep the Python tick callback
# allocation-free — no string formatting, no list/dict creation, just
# pin writes and integer arithmetic.
_seq = bytearray()     # packed states, each byte 0-15
_seq_idx = 0           # current position; preserved across F/G/H
_period_us = 0         # 0 until F is received
_running = False
_timer = None          # machine.Timer instance while running


# ---------------------------------------------------------------------------
# NeoPixel helpers
# ---------------------------------------------------------------------------
def set_led(r, g, b):
    np[0] = (r, g, b)
    np.write()


def led_startup():
    for color in [(10, 0, 0), (0, 10, 0), (0, 0, 10), (0, 0, 0)]:
        set_led(*color)
        time.sleep_ms(100)


# ---------------------------------------------------------------------------
# FET control
# ---------------------------------------------------------------------------
def set_fets(p1, p2, n1, n2):
    fets[0].value(p1)
    fets[1].value(p2)
    fets[2].value(n1)
    fets[3].value(n2)


def get_fets():
    return [f.value() for f in fets]


def all_off():
    set_fets(0, 0, 0, 0)


def _apply_packed(b):
    """Apply a 4-bit packed FET state. Bit order: P1<<3 | P2<<2 | N1<<1 | N2.
    Safe to call from IRQ context — no allocation."""
    fets[0].value((b >> 3) & 1)
    fets[1].value((b >> 2) & 1)
    fets[2].value((b >> 1) & 1)
    fets[3].value(b & 1)


def _tick(t):
    """Timer IRQ callback. Caches _seq locally so a concurrent reassign
    from the main thread (C command) can't land us with a stale index
    into a shorter sequence mid-tick."""
    global _seq_idx
    seq = _seq
    n = len(seq)
    if n == 0:
        return
    _seq_idx = (_seq_idx + 1) % n
    _apply_packed(seq[_seq_idx])


_last_start_ticks_us = 0  # firmware time at which state 0 was applied by G


def _switching_start():
    global _running, _timer, _last_start_ticks_us
    if len(_seq) == 0 or _period_us <= 0:
        return False
    _apply_packed(_seq[_seq_idx])
    # Stamp the "zero time" AS CLOSE AS POSSIBLE to when state 0 actually
    # reached the FET pins — before any timer setup, so serial reply
    # latency doesn't pollute the number.
    _last_start_ticks_us = time.ticks_us()
    if _timer is not None:
        _timer.deinit()
    _timer = Timer(-1)
    _timer.init(
        freq=1_000_000.0 / _period_us,
        mode=Timer.PERIODIC,
        callback=_tick,
    )
    _running = True
    return True


def _switching_halt():
    global _running, _timer
    if _timer is not None:
        _timer.deinit()
        _timer = None
    _running = False
    all_off()


# ---------------------------------------------------------------------------
# INA226 helpers
# ---------------------------------------------------------------------------
def ina226_read_reg(addr, reg):
    i2c.writeto(addr, bytes([reg]))
    data = i2c.readfrom(addr, 2)
    return (data[0] << 8) | data[1]


def ina226_write_reg(addr, reg, value):
    i2c.writeto(addr, bytes([reg, (value >> 8) & 0xFF, value & 0xFF]))


def _build_ina226_config(avg_value):
    """Encode the INA226 CONFIG word for the requested AVG count.
    AVG/VSHCT/VBUSCT are runtime-tunable (A and U commands); MODE is fixed."""
    avg_code = _AVG_VALUES.index(avg_value)
    return ((avg_code & 0x07) << 9) | ((_VBUSCT_CODE & 0x07) << 6) \
        | ((_VSHCT_CODE & 0x07) << 3) | (_MODE_CONT_BOTH & 0x07)


def ina226_init(addr):
    """Write the current (_ina226_avg) config word to one sensor."""
    config = _build_ina226_config(_ina226_avg)
    ina226_write_reg(addr, INA226_REG_CONFIG, config)


def ina226_apply_all():
    """Re-program every present sensor with the current config. Called
    after A changes the averaging setting at runtime."""
    for addr in ina226_present.values():
        try:
            ina226_init(addr)
        except OSError:
            pass


def _max_stream_hz():
    """Ceiling on the streaming rate given the current AVG and bus
    decimation. If Z has been run, uses the measured per-emit time
    (authoritative). Otherwise estimates from INA226 conversion time
    and I2C sweep time plus fixed overhead — theoretical ceiling."""
    if _measured_emit_us > 0:
        return 1_000_000.0 / _measured_emit_us
    conv_us = _ina226_avg * (_VSHCT_US + _VBUSCT_US)
    # Shunt read on every sweep; bus read amortized over _bus_every sweeps.
    i2c_us = 4 * _I2C_READ_US
    if _bus_every > 0:
        i2c_us += (4 * _I2C_READ_US) / _bus_every
    period_us = max(conv_us, i2c_us) + _EMIT_OVERHEAD_US
    return 1_000_000.0 / period_us


def ina226_scan():
    global ina226_present
    ina226_present = {}
    found = i2c.scan()
    for name, addr in INA226_ADDRS.items():
        if addr in found:
            try:
                ina226_read_reg(addr, INA226_REG_DIEID)
                ina226_init(addr)
                ina226_present[name] = addr
            except OSError:
                pass
    return ina226_present


def ina226_read_fast(addr):
    """Read bus voltage and shunt voltage. Returns (bus_raw, shunt_raw).
    Used by one-shot I and burst paths where we always want both."""
    i2c.writeto(addr, bytes([INA226_REG_BUS_V]))
    bv = i2c.readfrom(addr, 2)
    i2c.writeto(addr, bytes([INA226_REG_SHUNT_V]))
    sv = i2c.readfrom(addr, 2)
    return (bv[0] << 8) | bv[1], (sv[0] << 8) | sv[1]


def _ina226_read_shunt_only(addr):
    """Shunt-only fast path for decimated streaming. ~half the I2C time
    of reading both registers — worth it when _bus_every is large."""
    i2c.writeto(addr, bytes([INA226_REG_SHUNT_V]))
    sv = i2c.readfrom(addr, 2)
    return (sv[0] << 8) | sv[1]


def ina226_read_all_fast():
    """Read all sensors (always both). Returns list of (bus_v, current_a)
    in SENSOR_ORDER. Missing sensors return (0.0, 0.0)."""
    results = []
    for name in SENSOR_ORDER:
        addr = ina226_present.get(name)
        if addr is None:
            results.append((0.0, 0.0))
            continue
        try:
            raw_bus, raw_shunt = ina226_read_fast(addr)
            if raw_shunt > 32767:
                raw_shunt -= 65536
            bus_v = raw_bus * INA226_BUS_V_LSB
            current_a = (raw_shunt * INA226_SHUNT_V_LSB) / SHUNT_RESISTOR
            results.append((bus_v, current_a))
        except OSError:
            results.append((0.0, 0.0))
    return results


def ina226_read_all_streaming(read_bus):
    """Stream-path reader. Always reads shunt; reads bus only when
    read_bus is True, otherwise reuses the cached value. Bus voltage
    changes slowly (supply rail) so decimation barely affects utility."""
    results = []
    for i in range(len(SENSOR_ORDER)):
        name = SENSOR_ORDER[i]
        addr = ina226_present.get(name)
        if addr is None:
            results.append((0.0, 0.0))
            continue
        try:
            raw_shunt = _ina226_read_shunt_only(addr)
            if raw_shunt > 32767:
                raw_shunt -= 65536
            current_a = (raw_shunt * INA226_SHUNT_V_LSB) / SHUNT_RESISTOR
            if read_bus:
                i2c.writeto(addr, bytes([INA226_REG_BUS_V]))
                bv = i2c.readfrom(addr, 2)
                bus_v = ((bv[0] << 8) | bv[1]) * INA226_BUS_V_LSB
                _last_bus_v[i] = bus_v
            else:
                bus_v = _last_bus_v[i]
            results.append((bus_v, current_a))
        except OSError:
            results.append((0.0, 0.0))
    return results


def ina226_read_all_json():
    """Read all sensors and return JSON dict."""
    results = {}
    for name in SENSOR_ORDER:
        addr = ina226_present.get(name)
        if addr is None:
            continue
        try:
            raw_bus, raw_shunt = ina226_read_fast(addr)
            if raw_shunt > 32767:
                raw_shunt -= 65536
            bus_v = round(raw_bus * INA226_BUS_V_LSB, 4)
            current_a = round((raw_shunt * INA226_SHUNT_V_LSB) / SHUNT_RESISTOR, 6)
            results[name] = {"voltage": bus_v, "current": current_a}
        except OSError:
            results[name] = {"error": "read_failed"}
    return results


_STREAM_FMT = "D %d %.4f %.6f %.4f %.6f %.4f %.6f %.4f %.6f\n"


def emit_stream_line():
    """Read all sensors and print a compact D line.
    One %-format call = one string allocation instead of the 9 that a
    list-of-f-strings + join produces. Matters at >1 kHz sensor rate where
    the list-build path triggers GC ~1×/sec and visibly stutters switching.

    Bus voltage is decimated per _bus_every — always read shunt (the fast
    signal), read bus only every Nth sweep. Cached bus value fills the
    slot otherwise so the D line schema stays fixed for downstream code."""
    global _bus_counter
    if _bus_every <= 0:
        read_bus = False
    else:
        _bus_counter += 1
        if _bus_counter >= _bus_every:
            _bus_counter = 0
            read_bus = True
        else:
            read_bus = False
    # Stamp the line with ticks_us BEFORE the I2C sweep starts. This is the
    # sample-capture timestamp the Pi anchors the recorded row to. Stamping
    # AFTER the sweep would fold the I2C read time into the row's apparent
    # capture moment and re-introduce a fraction of the lag we're fixing.
    t_us = time.ticks_us()
    r = ina226_read_all_streaming(read_bus)
    sys.stdout.write(_STREAM_FMT % (
        t_us,
        r[0][0], r[0][1],
        r[1][0], r[1][1],
        r[2][0], r[2][1],
        r[3][0], r[3][1],
    ))


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------
def handle_command(line):
    global stream_hz, stream_interval, burst_buffer, burst_target, burst_active
    global _seq, _seq_idx, _period_us, _running, _timer
    global _ina226_avg, _bus_every, _bus_counter, _measured_emit_us
    global _VSHCT_CODE, _VBUSCT_CODE, _VSHCT_US, _VBUSCT_US
    line = line.strip()
    if not line:
        return None

    parts = line.split()
    cmd = parts[0].upper()

    try:
        if cmd == "S":
            if len(parts) != 5:
                return "ERR S requires 4 args: S <P1> <P2> <N1> <N2>"
            # Direct FET control and periodic switching must not fight, so
            # auto-halt the timer whenever S lands.
            if _running:
                _switching_halt()
            vals = [int(x) & 1 for x in parts[1:5]]
            set_fets(*vals)
            if any(vals):
                set_led(0, 4, 0)
            else:
                set_led(0, 0, 2)
            s = get_fets()
            return f"OK S {s[0]} {s[1]} {s[2]} {s[3]}"

        elif cmd == "Q":
            s = get_fets()
            return f"OK Q {s[0]} {s[1]} {s[2]} {s[3]}"

        elif cmd == "I":
            readings = ina226_read_all_json()
            return f"OK I {json.dumps(readings)}"

        elif cmd == "T":
            if len(parts) < 2:
                return "ERR T requires 1 arg: T <hz>"
            hz = float(parts[1])
            if hz <= 0:
                stream_hz = 0
                stream_interval = 0
                set_led(0, 0, 2)  # blue = idle
                return "OK T 0"
            else:
                cap = _max_stream_hz()
                if hz > cap:
                    hz = cap
                stream_hz = hz
                stream_interval = 1.0 / hz
                set_led(0, 4, 4)  # cyan = streaming
                return f"OK T {hz:.1f}"

        elif cmd == "A":
            if len(parts) != 2:
                return "ERR A requires 1 arg: A <avg>"
            try:
                avg = int(parts[1])
            except ValueError:
                return "ERR A avg must be integer"
            if avg not in _AVG_VALUES:
                return "ERR A avg must be one of 1/4/16/64/128/256/512/1024"
            _ina226_avg = avg
            ina226_apply_all()
            # New cap may be lower than current stream rate — clamp.
            cap = _max_stream_hz()
            if stream_hz > cap:
                stream_hz = cap
                stream_interval = 1.0 / cap
            return f"OK A {_ina226_avg} {cap:.1f}"

        elif cmd == "V":
            if len(parts) != 2:
                return "ERR V requires 1 arg: V <every>"
            try:
                every = int(parts[1])
            except ValueError:
                return "ERR V every must be integer"
            if every < 0 or every > 1000:
                return "ERR V every must be 0..1000"
            _bus_every = every
            _bus_counter = 0
            cap = _max_stream_hz()
            if stream_hz > cap:
                stream_hz = cap
                stream_interval = 1.0 / cap
            return f"OK V {_bus_every} {cap:.1f}"

        elif cmd == "M":
            cap = _max_stream_hz()
            return f"OK M {_ina226_avg} {_bus_every} {cap:.1f}"

        elif cmd == "Z":
            # Profile emit_stream_line. Time N iterations and divide.
            # Uses the same code path the stream uses, so the measured
            # value is what streaming actually delivers.
            n = 50
            if len(parts) == 2:
                try:
                    n = max(10, min(500, int(parts[1])))
                except ValueError:
                    pass
            # Suspend streaming so the profiler doesn't race with it.
            prev_hz = stream_hz
            stream_hz = 0
            t0 = time.ticks_us()
            for _ in range(n):
                emit_stream_line()
            t1 = time.ticks_us()
            total_us = time.ticks_diff(t1, t0)
            avg_us = total_us // n
            _measured_emit_us = avg_us
            # Re-clamp stream rate to new honest cap.
            cap = _max_stream_hz()
            if prev_hz > cap:
                prev_hz = cap
            stream_hz = prev_hz
            if stream_hz > 0:
                stream_interval = 1.0 / stream_hz
            return f"OK Z {n} {avg_us} {cap:.1f}"

        elif cmd == "L":
            if len(parts) != 4:
                return "ERR L requires 3 args: L <R> <G> <B>"
            r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
            set_led(r, g, b)
            return "OK L"

        elif cmd == "P":
            return f"OK P {time.ticks_us()}"

        elif cmd == "B":
            # Burst recording: B <count> to start, B 0 to stop/cancel
            if len(parts) < 2:
                return "ERR B requires 1 arg: B <count>"
            count = int(parts[1])
            if count <= 0:
                burst_active = False
                burst_buffer = None
                burst_target = 0
                return "OK B 0"
            else:
                count = min(count, 3000)  # RAM limit
                burst_buffer = []
                burst_target = count
                burst_active = True
                set_led(10, 0, 4)  # purple = burst recording
                return f"OK B {count}"

        elif cmd == "BD":
            # Download burst buffer
            if burst_active:
                return "ERR burst still recording"
            if not burst_buffer:
                return "ERR no burst data"
            # Send header
            sys.stdout.write(f"OK BD {len(burst_buffer)}\n")
            # Send rows: timestamp_us P1v P1i P2v P2i N1v N1i N2v N2i fets
            for ts_us, readings, fet_state in burst_buffer:
                parts_out = [str(ts_us)]
                for bus_v, current_a in readings:
                    parts_out.append(f"{bus_v:.4f}")
                    parts_out.append(f"{current_a:.6f}")
                parts_out.extend(str(f) for f in fet_state)
                sys.stdout.write("BR " + " ".join(parts_out) + "\n")
            burst_buffer = None
            return None  # header already sent

        elif cmd == "R":
            found = ina226_scan()
            names = ", ".join(f"{n}@0x{a:02X}" for n, a in found.items())
            return f"OK R {names}"

        elif cmd == "U":
            # U <vsh_us> <vbus_us> — runtime conversion-time tuning. Same
            # 8-code ladder as the INA226 CONFIG register; each value must
            # match one of _CT_VALUES. Re-encodes config word and re-applies
            # to every present sensor, then resets _measured_emit_us so the
            # next Z (or M) reflects the new floor honestly.
            if len(parts) != 3:
                return "ERR U requires 2 args: U <vsh_us> <vbus_us>"
            try:
                vsh = int(parts[1])
                vbus = int(parts[2])
            except ValueError:
                return "ERR U values must be integer µs"
            if vsh not in _CT_VALUES or vbus not in _CT_VALUES:
                return "ERR U values must be in 140/204/332/588/1100/2116/4156/8244"
            _VSHCT_CODE = _CT_VALUES.index(vsh)
            _VBUSCT_CODE = _CT_VALUES.index(vbus)
            _VSHCT_US = vsh
            _VBUSCT_US = vbus
            ina226_apply_all()
            _measured_emit_us = 0  # invalidate so the cap reflects new CT
            cap = _max_stream_hz()
            if stream_hz > cap:
                stream_hz = cap
                stream_interval = 1.0 / cap
            return f"OK U {_VSHCT_US} {_VBUSCT_US} {cap:.1f}"

        elif cmd == "E":
            return f"OK E {gc.mem_free()}"

        elif cmd == "X":
            import machine
            machine.reset()

        elif cmd == "C":
            # C <n> <s1> ... <sn> — program packed-state cycle
            if len(parts) < 2:
                return "ERR C requires count: C <n> <s1> ... <sn>"
            n = int(parts[1])
            if n < 1 or n > 64:
                return "ERR C count must be 1..64"
            if len(parts) != 2 + n:
                return f"ERR C expected {n} states, got {len(parts) - 2}"
            new_seq = bytearray(n)
            for i in range(n):
                v = int(parts[2 + i])
                if v < 0 or v > 15:
                    return f"ERR C state {i} out of range 0..15"
                new_seq[i] = v
            # Halt the existing timer BEFORE touching _seq_idx. Otherwise
            # stale ticks fire between C and G (at 1 kHz switching that's
            # ~5-10 ticks over the C→F→G serial round-trip) and advance
            # _seq_idx away from 0 mod 2, landing the firmware on state 1
            # instead of state 0 when G applies _seq[_seq_idx]. That was
            # the root cause of the 2026-04-24 DOE inversion — the Pi's
            # _step_at_resume = 0 assumption was fine; firmware was coming
            # up mid-cycle.
            _switching_halt()
            _seq = new_seq
            _seq_idx = 0
            return f"OK C {n}"

        elif cmd == "F":
            if len(parts) != 2:
                return "ERR F requires 1 arg: F <period_us>"
            us = int(parts[1])
            if us < 50:
                return "ERR F period_us must be >= 50"
            _period_us = us
            # If switching is active, re-arm the timer with the new period.
            # _seq_idx is preserved, so the cycle continues where it was;
            # the step that was in flight is effectively truncated or
            # extended to the new period (the timing-bias behavior).
            if _running:
                _switching_start()
            return f"OK F {us}"

        elif cmd == "G":
            if _switching_start():
                set_led(4, 0, 4)  # magenta = switching active
                return f"OK G {_period_us} {len(_seq)} {_last_start_ticks_us}"
            return "ERR G requires C and F first"

        elif cmd == "H":
            _switching_halt()
            set_led(0, 0, 2)
            return "OK H"

        elif cmd == "K":
            if len(_seq) == 0:
                return "ERR K requires C first"
            _seq_idx = (_seq_idx + 1) % len(_seq)
            _apply_packed(_seq[_seq_idx])
            return f"OK K {_seq_idx}"

        else:
            return f"ERR unknown command: {cmd}"

    except Exception as e:
        return f"ERR {e}"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    global stream_hz, stream_interval, burst_buffer, burst_target, burst_active

    led_startup()
    all_off()

    found = ina226_scan()
    if found:
        names = ", ".join(f"{n}@0x{a:02X}" for n, a in found.items())
        print(f"INA226 found: {names}")
    else:
        print("INA226: none found on I2C bus")

    set_led(0, 0, 2)
    print("OK READY")

    buf = ""
    last_stream = time.ticks_us()

    # Pre-allocated stdin poller: select.select in a hot loop allocates
    # fresh lists every call, and that garbage triggers GC pauses that
    # delay Timer callbacks — visible as a ~4 Hz stutter at 300 Hz
    # switching. poll objects are reused so the hot path allocates nothing.
    poller = uselect.poll()
    poller.register(sys.stdin, uselect.POLLIN)

    # Clean slate before entering the steady-state loop.
    gc.collect()

    while True:
        now = time.ticks_us()

        # Switching runs on machine.Timer in IRQ context — no main-loop
        # tick here; this loop just services commands, bursts, and streams.

        # --- Handle incoming commands (non-blocking) ---
        # ipoll returns a reusable iterator (vs poll() which allocates a
        # fresh list every call) — keeps the hot path allocation-free.
        for _ in poller.ipoll(0):
            byte = sys.stdin.buffer.read(1)
            if not byte:
                pass
            elif byte == b'\x03':
                _switching_halt()
                stream_hz = 0
                set_led(10, 0, 0)
                print("STOPPED")
                return
            else:
                ch = byte.decode("utf-8", "replace")
                if ch != '\r':
                    buf += ch
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        resp = handle_command(line)
                        if resp:
                            print(resp)
            break  # only one fd is registered; stop after servicing it

        # --- Burst recording: capture as fast as possible ---
        if burst_active and burst_buffer is not None:
            readings = ina226_read_all_fast()
            fet_state = get_fets()
            burst_buffer.append((time.ticks_us(), readings, fet_state))
            if len(burst_buffer) >= burst_target:
                burst_active = False
                set_led(10, 4, 0)  # orange = burst complete, ready to download
                print(f"OK BURST_DONE {len(burst_buffer)}")
            continue  # skip streaming during burst for max speed

        # --- Streaming: emit sensor data at configured rate ---
        if stream_hz > 0:
            elapsed = time.ticks_diff(now, last_stream)
            if elapsed >= stream_interval * 1_000_000:
                emit_stream_line()
                last_stream = now
        else:
            # Idle (switching is IRQ-driven so it's fine to sleep here) —
            # short sleep to avoid busy-looping on stdin polling.
            time.sleep_us(100)


main()
