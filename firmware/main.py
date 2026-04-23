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
    L <R> <G> <B>            Set NeoPixel LED color (0-255 each)
    P                        Ping (heartbeat)
    R                        Re-scan INA226 sensors
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
    OK T <hz>                     Streaming started/stopped
    D <P1v> <P1i> <P2v> <P2i> <N1v> <N1i> <N2v> <N2i>   Stream data
    OK L                          LED set
    OK P                          Pong
    OK C <n>                      Sequence programmed (n states)
    OK F <us>                     Period set
    OK G <us> <n>                 Switching started
    OK H                          Switching halted
    OK K <idx>                    Stepped to idx
    ERR <message>                 Error
"""

import sys
import select
import json
from machine import Pin, I2C
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
i2c = I2C(1, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=400_000)
ina226_present = {}

# Streaming state
stream_hz = 0       # 0 = off
stream_interval = 0  # seconds between stream readings

# Burst recording state
burst_buffer = None   # list of (timestamp_us, readings) or None
burst_target = 0      # target sample count
burst_active = False

# In-loop switching timer state. The main loop polls ticks_us() so we get
# microsecond-resolution switching without the ISR-context constraints of
# machine.Timer. A step is applied whenever (now - _last_tick) >= _period_us.
_seq = bytearray()     # packed states, each byte 0-15
_seq_idx = 0           # current position; preserved across F/G/H
_period_us = 0         # 0 until F is received
_last_tick = 0
_running = False


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
    """Apply a 4-bit packed FET state. Bit order: P1<<3 | P2<<2 | N1<<1 | N2."""
    fets[0].value((b >> 3) & 1)
    fets[1].value((b >> 2) & 1)
    fets[2].value((b >> 1) & 1)
    fets[3].value(b & 1)


def _switching_start():
    global _running, _last_tick
    if len(_seq) == 0 or _period_us <= 0:
        return False
    _apply_packed(_seq[_seq_idx])
    _last_tick = time.ticks_us()
    _running = True
    return True


def _switching_halt():
    global _running
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


def ina226_init(addr):
    """AVG=4, VBUSCT=332us, VSHCT=332us, continuous shunt+bus."""
    config = 0x0297
    ina226_write_reg(addr, INA226_REG_CONFIG, config)


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
    """Read bus voltage and shunt voltage. Returns (bus_raw, shunt_raw)."""
    i2c.writeto(addr, bytes([INA226_REG_BUS_V]))
    bv = i2c.readfrom(addr, 2)
    i2c.writeto(addr, bytes([INA226_REG_SHUNT_V]))
    sv = i2c.readfrom(addr, 2)
    return (bv[0] << 8) | bv[1], (sv[0] << 8) | sv[1]


def ina226_read_all_fast():
    """Read all sensors. Returns list of (bus_v, current_a) in SENSOR_ORDER.
    Missing sensors return (0.0, 0.0)."""
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


def emit_stream_line():
    """Read all sensors and print a compact D line."""
    readings = ina226_read_all_fast()
    # Format: D <P1v> <P1i> <P2v> <P2i> <N1v> <N1i> <N2v> <N2i>
    parts = []
    for bus_v, current_a in readings:
        parts.append(f"{bus_v:.4f}")
        parts.append(f"{current_a:.6f}")
    sys.stdout.write("D " + " ".join(parts) + "\n")


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------
def handle_command(line):
    global stream_hz, stream_interval, burst_buffer, burst_target, burst_active
    global _seq, _seq_idx, _period_us, _last_tick, _running
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
                hz = min(hz, 200.0)
                stream_hz = hz
                stream_interval = 1.0 / hz
                set_led(0, 4, 4)  # cyan = streaming
                return f"OK T {hz:.1f}"

        elif cmd == "L":
            if len(parts) != 4:
                return "ERR L requires 3 args: L <R> <G> <B>"
            r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
            set_led(r, g, b)
            return "OK L"

        elif cmd == "P":
            return "OK P"

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
            _seq = new_seq
            # Reset index on a sequence change — preserving it across a
            # different-length cycle is ambiguous; F preserves within the
            # same cycle, which is what the timing-bias requirement asks for.
            _seq_idx = 0
            return f"OK C {n}"

        elif cmd == "F":
            if len(parts) != 2:
                return "ERR F requires 1 arg: F <period_us>"
            us = int(parts[1])
            if us < 50:
                return "ERR F period_us must be >= 50"
            _period_us = us
            # Preserve _seq_idx and _last_tick deliberately: a frequency
            # change during switching cuts the current step short or
            # extends it based on elapsed time, per design.
            return f"OK F {us}"

        elif cmd == "G":
            if _switching_start():
                set_led(4, 0, 4)  # magenta = switching active
                return f"OK G {_period_us} {len(_seq)}"
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

    global _seq_idx, _last_tick

    while True:
        now = time.ticks_us()

        # --- Switching tick (highest priority, cheap) ---
        if _running and len(_seq) > 0:
            if time.ticks_diff(now, _last_tick) >= _period_us:
                _seq_idx = (_seq_idx + 1) % len(_seq)
                _apply_packed(_seq[_seq_idx])
                _last_tick = now

        # --- Handle incoming commands (non-blocking) ---
        if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
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
        elif not _running:
            # Idle — sleep briefly to avoid busy-wait
            time.sleep_us(100)


main()
