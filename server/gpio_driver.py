"""
Switching Circuit V2 - GPIO Driver (RP2040 Serial).

Sends FET state commands to the RP2040-Zero over USB serial.
Receives streaming sensor data via push from the RP2040.
Falls back to a mock implementation when pyserial is unavailable
or the RP2040 is not connected.

Stream wire format is BINARY (since 2026-04-24): the firmware emits
25-byte frames `0xAA 0x55 'B' 20 <ticks_us:u32 LE> <(shunt:i16,bus:u16)*4 LE> <xor>`.
The parser is a small state machine that resyncs on the AA 55 sync
pair so a firmware reboot mid-stream (which dumps ASCII boot banner)
recovers cleanly. Float conversion (raw × LSB / shunt_R) lives here on
the Pi instead of on the MCU. Command replies (`OK ...`, `ERR ...`,
boot text) remain ASCII line-delimited; we sniff for ASCII bytes
in the SCAN_SYNC state and route any complete `\\n`-terminated line
to the response queue or log.
"""

import json
import logging
import queue
import struct
import threading
import time

from server.config import (
    RP2040_SERIAL_PORT, RP2040_SERIAL_BAUD,
    STATE_DEFS,
)

# INA226 LSB constants — float conversion moved from firmware to Pi.
_INA226_SHUNT_LSB_V = 2.5e-6   # 2.5 µV per LSB
_INA226_BUS_LSB_V = 1.25e-3    # 1.25 mV per LSB
# Shunt resistor value (Ω). Mirror of firmware's SHUNT_RESISTOR.
_SHUNT_R_OHMS = 0.1

# Binary frame constants — must mirror firmware/main.py emit_stream_line_binary.
_FRAME_SYNC0 = 0xAA
_FRAME_SYNC1 = 0x55
_FRAME_TAG_B = 0x42  # 'B'
_FRAME_PAYLOAD_LEN = 20
_FRAME_TOTAL_LEN = 25  # 4 header + 20 payload + 1 checksum

log = logging.getLogger(__name__)

# Sensor names in the order the RP2040 streams them
SENSOR_ORDER = ["P1", "P2", "N1", "N2"]

try:
    import serial
    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False
    log.warning("pyserial not available — using mock GPIO driver")


class GPIODriver:
    """Controls the four H-bridge MOSFET outputs via RP2040 serial.
    Receives streaming sensor data from the RP2040 push model."""

    def __init__(self, port=RP2040_SERIAL_PORT, baud=RP2040_SERIAL_BAUD):
        self._lock = threading.Lock()  # protects _ser for writes
        self._fet_states = [False, False, False, False]
        self._port_name = port
        self._ser = None
        self._mock = True

        # Pi↔firmware clock offset, populated by sync_firmware_clock(). Stored
        # as "firmware_seconds - pi_seconds" so an absolute firmware ticks_us
        # value V (in seconds) maps to Pi monotonic() via:
        #     pi_s = V/1e6 - _clock_offset_s
        # None until sync_firmware_clock has run successfully — callers must
        # treat absence as "fall back to midpoint anchor".
        self._clock_offset_s = None
        self._clock_offset_rtt_s = None  # last measured P round-trip (for logging)

        # Sensor data (updated by reader thread from stream)
        # Initialize with zero readings so mock mode returns valid data
        self._sensor_data = {
            name: {"voltage": 0.0, "current": 0.0}
            for name in SENSOR_ORDER
        }
        self._sensor_lock = threading.Lock()
        self._sensor_hz = 0.0
        self._sensor_new = threading.Event()  # set when fresh data arrives

        # Optional callback fired in the reader thread after each fresh
        # sensor frame is cached. Used by command_server to drive Pi-side
        # recording at the true sensor rate, independent of broadcast cadence.
        self.on_sensor_tick = None  # type: ignore[assignment]

        # Response queue for command replies
        self._response_q = queue.Queue()

        self._stop_event = threading.Event()

        if _HAS_SERIAL:
            ports_to_try = [port] + [f"/dev/ttyACM{i}" for i in range(4) if f"/dev/ttyACM{i}" != port]
            for try_port in ports_to_try:
                try:
                    self._ser = serial.Serial(try_port, baud, timeout=0.5)
                    self._wait_for_ready()
                    self._mock = False
                    self._port_name = try_port
                    log.info("GPIODriver connected to RP2040 on %s @ %d", try_port, baud)
                    break
                except (serial.SerialException, OSError) as e:
                    log.debug("Could not open %s: %s", try_port, e)
                    self._ser = None
                    continue
            if self._mock:
                log.warning("No RP2040 found on any serial port — using mock driver")
        else:
            log.info("GPIODriver running in mock mode (no pyserial)")

        # Start reader thread (reads all incoming lines from RP2040)
        if not self._mock:
            self._reader_thread = threading.Thread(
                target=self._reader_loop, name="RP2040-Reader", daemon=True,
            )
            self._reader_thread.start()

        self.all_off()

    def _wait_for_ready(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ser and self._ser.in_waiting:
                line = self._ser.readline().decode("utf-8", errors="replace").strip()
                log.debug("RP2040 boot: %s", line)
                if "OK READY" in line:
                    return
            time.sleep(0.05)
        log.warning("RP2040 did not send READY within %.1fs — continuing anyway", timeout)

    # -- Reader thread -------------------------------------------------------

    def _reader_loop(self):
        """Read bytes from the RP2040 and dispatch frames + ASCII lines.

        State machine — the wire alternates binary stream frames with
        ASCII command replies. We scan byte-by-byte:

          - In SCAN_SYNC: look for the AA 55 'B' <len> header. Any byte
            that doesn't match is treated as ASCII; we accumulate it in
            ``ascii_buf`` until we see ``\\n``, then dispatch the line.
          - On valid header: read the next 21 bytes (20 payload + 1 cksum),
            verify XOR, parse, and either dispatch sensor data or drop on
            checksum failure (next iteration re-enters SCAN_SYNC).

        This handles the firmware-reboot-mid-stream case naturally — when
        the MCU resets, the partially-buffered frame becomes garbage,
        SCAN_SYNC drops bytes until it finds the boot banner's text
        (routed as ASCII lines to debug log) followed by the next valid
        AA 55 frame.

        Reading is done in chunks (`self._ser.read(N)` with timeout=0.5)
        to amortize syscall overhead at 700+ Hz frame rates.
        """
        ascii_buf = bytearray()
        # Persistent byte buffer across read iterations so frames that
        # straddle chunk boundaries are reassembled. Bounded in practice
        # by the read chunk size + at most one frame of unparsed tail.
        rxbuf = bytearray()
        while not self._stop_event.is_set():
            try:
                if not self._ser or not self._ser.is_open:
                    break
                # Read a chunk. 256 bytes covers ~10 frames at 25 B each;
                # the 0.5s pyserial timeout bounds idle wait when the
                # stream is paused.
                chunk = self._ser.read(256)
                if not chunk:
                    continue
                rxbuf.extend(chunk)
                pos = 0
                blen = len(rxbuf)
                while pos < blen:
                    # Need at least 4 bytes to test for a valid header.
                    if blen - pos < 4:
                        break
                    b = rxbuf[pos]
                    if b == _FRAME_SYNC0 \
                            and rxbuf[pos + 1] == _FRAME_SYNC1 \
                            and rxbuf[pos + 2] == _FRAME_TAG_B \
                            and rxbuf[pos + 3] == _FRAME_PAYLOAD_LEN:
                        # Header looks valid. Need full 25-byte frame.
                        if blen - pos < _FRAME_TOTAL_LEN:
                            break  # wait for more bytes
                        # Verify XOR checksum over payload bytes.
                        cksum = 0
                        for k in range(_FRAME_PAYLOAD_LEN):
                            cksum ^= rxbuf[pos + 4 + k]
                        if cksum == rxbuf[pos + 4 + _FRAME_PAYLOAD_LEN]:
                            self._handle_stream_frame(
                                bytes(rxbuf[pos + 4:pos + 4 + _FRAME_PAYLOAD_LEN])
                            )
                            pos += _FRAME_TOTAL_LEN
                            continue
                        # Bad checksum — drop the sync byte, resync next.
                        log.debug("Bad binary frame checksum, resyncing")
                        pos += 1
                        continue
                    # Not a frame start — treat as ASCII.
                    if b == 0x0A:  # '\n'
                        line = ascii_buf.decode("utf-8", errors="replace").rstrip("\r")
                        ascii_buf = bytearray()
                        if line:
                            self._dispatch_ascii_line(line)
                    elif b != 0x00 and b != _FRAME_SYNC0:
                        # Drop stray 0xAA bytes (broken-frame remnants);
                        # nulls are filtered to avoid polluting log output.
                        ascii_buf.append(b)
                    pos += 1
                # Compact rxbuf: keep only the unparsed tail.
                if pos > 0:
                    del rxbuf[:pos]
            except (serial.SerialException, OSError) as e:
                if not self._stop_event.is_set():
                    log.error("Reader error: %s", e)
                break

    def _dispatch_ascii_line(self, line):
        """Route a complete ASCII line — command reply, error, or
        boot/info chatter."""
        if line.startswith("BR ") or line.startswith("OK ") or line.startswith("ERR"):
            self._response_q.put(line)
        elif "BURST_DONE" in line:
            self._response_q.put(line)
            log.info("RP2040: %s", line)
        else:
            log.debug("RP2040: %s", line)

    def _handle_stream_frame(self, payload):
        """Decode a 20-byte sensor payload into the consumer dict shape.

        Layout (mirrors firmware emit_stream_line_binary):
          [0:4]   uint32 LE  ticks_us
          [4..]   per-sensor (int16 shunt LE, uint16 bus LE) × 4

        Yields the same `{P1: {voltage, current}, ...}` dict the legacy
        ASCII path produced — consumers (command_server, recorder,
        mode_controller) are unchanged. Float conversion (raw × LSB /
        shunt_R) happens here so the firmware never touches floats.
        """
        try:
            # Mixed signed-shunt / unsigned-bus layout. Single
            # `struct.unpack` doesn't handle the alternation cleanly,
            # so unpack ticks_us + 4 shunt int16s, then re-pull the
            # bus uint16s by offset. Both calls operate on the same
            # 20-byte buffer; cheap relative to the float math below.
            fw_ts_us, sh_p1, _, sh_p2, _, sh_n1, _, sh_n2, _ = \
                struct.unpack("<Ihhhhhhhh", payload)
            bus_p1 = struct.unpack_from("<H", payload, 6)[0]
            bus_p2 = struct.unpack_from("<H", payload, 10)[0]
            bus_n1 = struct.unpack_from("<H", payload, 14)[0]
            bus_n2 = struct.unpack_from("<H", payload, 18)[0]

            # Convert raw → physical units. Same math the firmware did
            # before; just running on the Pi.
            data = {
                "P1": {
                    "voltage": round(bus_p1 * _INA226_BUS_LSB_V, 4),
                    "current": round(sh_p1 * _INA226_SHUNT_LSB_V / _SHUNT_R_OHMS, 6),
                },
                "P2": {
                    "voltage": round(bus_p2 * _INA226_BUS_LSB_V, 4),
                    "current": round(sh_p2 * _INA226_SHUNT_LSB_V / _SHUNT_R_OHMS, 6),
                },
                "N1": {
                    "voltage": round(bus_n1 * _INA226_BUS_LSB_V, 4),
                    "current": round(sh_n1 * _INA226_SHUNT_LSB_V / _SHUNT_R_OHMS, 6),
                },
                "N2": {
                    "voltage": round(bus_n2 * _INA226_BUS_LSB_V, 4),
                    "current": round(sh_n2 * _INA226_SHUNT_LSB_V / _SHUNT_R_OHMS, 6),
                },
            }
            # Same clock-offset arithmetic as the legacy ASCII path: feed
            # the firmware ticks_us through self._clock_offset_s to land
            # on Pi monotonic seconds at sample-capture time. Preserves
            # the recently-fixed phase-correctness mechanism.
            if self._clock_offset_s is not None:
                sample_pi_s = fw_ts_us / 1_000_000.0 - self._clock_offset_s
            else:
                sample_pi_s = time.monotonic()
            with self._sensor_lock:
                self._sensor_data = data
            self._sensor_new.set()
            cb = self.on_sensor_tick
            if cb is not None:
                try:
                    cb(data, sample_pi_s)
                except Exception:
                    log.exception("on_sensor_tick callback failed")
        except struct.error:
            log.debug("Bad binary frame payload, dropping")

    # -- Command interface ---------------------------------------------------

    def _send(self, cmd):
        """Send a command and wait for the response."""
        with self._lock:
            if self._mock or not self._ser:
                return None
            try:
                # Drain any old responses
                while not self._response_q.empty():
                    try:
                        self._response_q.get_nowait()
                    except queue.Empty:
                        break
                self._ser.write((cmd + "\n").encode("utf-8"))
                self._ser.flush()
                # Wait for response (reader thread will put it in the queue)
                try:
                    return self._response_q.get(timeout=2.0)
                except queue.Empty:
                    log.warning("No response for command: %s", cmd)
                    return None
            except (serial.SerialException, OSError) as e:
                log.error("Serial error: %s", e)
                return None

    # -- public API ---------------------------------------------------------

    def apply_state(self, state_tuple):
        vals = [int(bool(v)) for v in state_tuple]
        resp = self._send(f"S {vals[0]} {vals[1]} {vals[2]} {vals[3]}")
        self._fet_states = [bool(v) for v in vals]
        if resp and not resp.startswith("OK"):
            log.warning("RP2040 error: %s", resp)

    def all_on(self):
        self.apply_state(STATE_DEFS[4])

    def all_off(self):
        self.apply_state(STATE_DEFS[5])

    def get_fet_states(self):
        return list(self._fet_states)

    def read_sensors(self):
        """One-shot sensor read (for compatibility). Prefer get_sensor_data()."""
        resp = self._send("I")
        if resp and resp.startswith("OK I "):
            try:
                return json.loads(resp[5:])
            except (ValueError, json.JSONDecodeError):
                log.warning("Bad sensor response: %s", resp)
        return None

    def set_sensor_rate(self, hz):
        """Set the RP2040 streaming rate. Firmware caps the actual rate to
        whatever _max_stream_hz() returns for the current AVG + bus decimation
        profile; the OK T reply echoes the effective rate."""
        hz = max(0.0, float(hz))
        resp = self._send(f"T {hz:.1f}")
        if resp and resp.startswith("OK T"):
            try:
                actual = float(resp.split()[2])
            except (ValueError, IndexError):
                actual = hz
            self._sensor_hz = actual
            log.info("Sensor stream rate set to %.1f Hz (requested %.1f)", actual, hz)
        else:
            log.warning("Failed to set stream rate: %s", resp)

    def get_sensor_rate(self):
        return self._sensor_hz

    def set_ina226_avg(self, avg):
        """Set INA226 averaging count. Valid: 1/4/16/64/128/256/512/1024.
        Returns (actual_avg, max_hz) — firmware echoes the new cap so the
        caller can re-clamp the stream rate if needed."""
        resp = self._send(f"A {int(avg)}")
        if resp and resp.startswith("OK A"):
            parts = resp.split()
            try:
                actual = int(parts[2])
                max_hz = float(parts[3])
                log.info("INA226 AVG set to %d (max stream %.1f Hz)", actual, max_hz)
                return actual, max_hz
            except (ValueError, IndexError):
                pass
        log.warning("Failed to set INA226 AVG: %s", resp)
        return None, None

    def set_bus_every(self, every):
        """Set bus-voltage decimation. 0 = never read bus, 1 = every sample,
        N = every Nth shunt sweep. Returns (actual_every, max_hz)."""
        resp = self._send(f"V {int(every)}")
        if resp and resp.startswith("OK V"):
            parts = resp.split()
            try:
                actual = int(parts[2])
                max_hz = float(parts[3])
                log.info("INA226 bus_every set to %d (max stream %.1f Hz)", actual, max_hz)
                return actual, max_hz
            except (ValueError, IndexError):
                pass
        log.warning("Failed to set bus decimation: %s", resp)
        return None, None

    def get_sensor_profile(self):
        """Query firmware for current AVG / bus_every / max_hz. Returns dict
        or None on failure."""
        resp = self._send("M")
        if resp and resp.startswith("OK M"):
            parts = resp.split()
            try:
                return {
                    "avg": int(parts[2]),
                    "bus_every": int(parts[3]),
                    "max_hz": float(parts[4]),
                }
            except (ValueError, IndexError):
                pass
        return None

    def get_sensor_data(self):
        with self._sensor_lock:
            return dict(self._sensor_data)

    def wait_for_new_sensor_data(self, timeout=0.1):
        """Block until fresh sensor data arrives. Returns True if new data, False on timeout."""
        got_new = self._sensor_new.wait(timeout=timeout)
        if got_new:
            self._sensor_new.clear()
        return got_new

    def ping(self):
        resp = self._send("P")
        return bool(resp and resp.startswith("OK P"))

    def sync_firmware_clock(self):
        """Measure the Pi↔firmware clock offset using the P command.

        The firmware now replies with `OK P <ticks_us>` where ticks_us is
        time.ticks_us() at the moment the reply is built. Under symmetric
        serial latency the firmware stamp lands at ~midpoint of (t_send,
        t_reply) Pi-monotonic time, so:
            offset = firmware_seconds - pi_seconds
                   = ticks_us/1e6 - 0.5*(t_send + t_reply)
        Stores the offset and round-trip time on self for later use by
        start_switching(). Returns the offset in seconds, or None on
        failure (mock mode, malformed reply, etc.).

        This sync underpins the inversion fix: with a known offset, the
        OK G ticks_us value translates straight to Pi monotonic, removing
        the asymmetric stdin-poll latency on the firmware side that the
        prior midpoint-only anchor couldn't see.
        """
        from time import monotonic
        if self._mock:
            return None
        t_send = monotonic()
        resp = self._send("P")
        t_reply = monotonic()
        if not resp or not resp.startswith("OK P"):
            log.warning("sync_firmware_clock: bad reply: %s", resp)
            return None
        parts = resp.split()
        if len(parts) < 3:
            # Old firmware that just replies "OK P" — can't sync.
            log.warning("sync_firmware_clock: firmware reply missing ticks_us; "
                        "old firmware? reply=%r", resp)
            return None
        try:
            fw_ticks_us = int(parts[2])
        except ValueError:
            log.warning("sync_firmware_clock: unparseable ticks_us in %r", resp)
            return None
        midpoint_pi_s = 0.5 * (t_send + t_reply)
        offset = fw_ticks_us / 1_000_000.0 - midpoint_pi_s
        rtt = t_reply - t_send
        self._clock_offset_s = offset
        self._clock_offset_rtt_s = rtt
        log.info("Firmware clock sync: offset=%.6fs rtt=%.3fms",
                 offset, rtt * 1000.0)
        return offset

    def get_clock_offset(self):
        """Return the most recent (offset_s, rtt_s) tuple or (None, None)."""
        return self._clock_offset_s, self._clock_offset_rtt_s

    # -- Firmware-resident switching cycle (C/F/G/H/K) ----------------------
    # The RP2040 owns periodic switching via machine.Timer. Pi-side code
    # just programs the cycle and period, then starts/stops the timer.

    def program_sequence(self, packed_states):
        """Program the RP2040's switching cycle.

        `packed_states` is an iterable of ints (each 0-15) where bits are
        P1<<3 | P2<<2 | N1<<1 | N2. Firmware resets its step index to 0
        on receipt.
        """
        parts = [str(int(s) & 0xF) for s in packed_states]
        if not parts:
            raise ValueError("program_sequence requires at least one state")
        cmd = "C " + str(len(parts)) + " " + " ".join(parts)
        resp = self._send(cmd)
        if resp and not resp.startswith("OK"):
            log.warning("RP2040 C error: %s", resp)

    def set_step_period_us(self, period_us):
        """Set the per-step period in microseconds. If switching is running,
        firmware re-arms the timer preserving its current step index."""
        period_us = max(50, int(period_us))
        resp = self._send(f"F {period_us}")
        if resp and not resp.startswith("OK"):
            log.warning("RP2040 F error: %s", resp)

    def start_switching(self):
        """Start periodic switching. Requires program_sequence() and
        set_step_period_us() to have been called first.

        Returns (anchor_pi_s, fw_ticks_us): Pi-monotonic estimate of when
        firmware applied state 0 on the FET pins, plus the firmware's
        own ticks_us stamp from the OK G reply.

        Anchor selection:
        - If sync_firmware_clock() has populated self._clock_offset_s AND
          the OK G reply included a usable ticks_us, anchor is computed
          DIRECTLY from the firmware stamp:
              anchor_pi_s = fw_ticks_us/1e6 - _clock_offset_s
          This is the authoritative anchor because it removes the
          asymmetric latency baked into _switching_start (the firmware's
          stdin-poll loop adds variable cost that midpoint-of-RTT can't
          model — symptom was 100 Hz inverting while 10/200 Hz aligned
          in the 2026-04-24 DOE).
        - Otherwise, fall back to the midpoint of send/reply monotonic
          times. Same correction the prior version did; works under the
          symmetric-latency assumption."""
        from time import monotonic
        t_send = monotonic()
        resp = self._send("G")
        t_reply = monotonic()
        if resp and not resp.startswith("OK"):
            log.warning("RP2040 G error: %s", resp)
            return None, None
        fw_ticks_us = None
        try:
            parts = resp.split()
            if len(parts) >= 5:
                fw_ticks_us = int(parts[4])
        except (ValueError, IndexError):
            pass
        if self._clock_offset_s is not None and fw_ticks_us is not None:
            anchor_pi_s = fw_ticks_us / 1_000_000.0 - self._clock_offset_s
        else:
            anchor_pi_s = 0.5 * (t_send + t_reply)
        return anchor_pi_s, fw_ticks_us

    def stop_switching(self):
        """Halt switching and set all FETs off."""
        resp = self._send("H")
        if resp and not resp.startswith("OK"):
            log.warning("RP2040 H error: %s", resp)
        # Firmware turns everything off; mirror that in our cache.
        self._fet_states = [False, False, False, False]

    def debug_step_cycle(self):
        """Advance one step in the programmed cycle (for DEBUG stepping)."""
        resp = self._send("K")
        if resp and not resp.startswith("OK"):
            log.warning("RP2040 K error: %s", resp)

    def cleanup(self):
        self._stop_event.set()
        # Stop streaming
        if self._ser and not self._mock:
            try:
                self._ser.write(b"T 0\n")
                self._ser.flush()
            except OSError:
                pass
        self.all_off()
        if self._ser:
            try:
                self._ser.close()
            except OSError:
                pass
        log.info("GPIODriver cleaned up")
