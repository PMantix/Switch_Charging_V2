"""
Switching Circuit V2 - GPIO Driver (RP2040 Serial).

Sends FET state commands to the RP2040-Zero over USB serial.
Receives streaming sensor data via push from the RP2040.
Falls back to a mock implementation when pyserial is unavailable
or the RP2040 is not connected.
"""

import json
import logging
import queue
import threading
import time

from server.config import (
    RP2040_SERIAL_PORT, RP2040_SERIAL_BAUD,
    STATE_DEFS,
)

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

        # Sensor data (updated by reader thread from stream)
        self._sensor_data = {}
        self._sensor_lock = threading.Lock()
        self._sensor_hz = 0.0
        self._sensor_new = threading.Event()  # set when fresh data arrives

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
        """Continuously read lines from RP2040. Route stream data to cache,
        command responses to the response queue."""
        while not self._stop_event.is_set():
            try:
                if not self._ser or not self._ser.is_open:
                    break
                raw = self._ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                if line.startswith("D "):
                    self._handle_stream_line(line)
                elif line.startswith("BR ") or line.startswith("OK ") or line.startswith("ERR"):
                    self._response_q.put(line)
                elif "BURST_DONE" in line:
                    self._response_q.put(line)
                    log.info("RP2040: %s", line)
                else:
                    # Boot messages, info, etc
                    log.debug("RP2040: %s", line)
            except (serial.SerialException, OSError) as e:
                if not self._stop_event.is_set():
                    log.error("Reader error: %s", e)
                break

    def _handle_stream_line(self, line):
        """Parse a D line: D <P1v> <P1i> <P2v> <P2i> <N1v> <N1i> <N2v> <N2i>"""
        try:
            parts = line.split()
            if len(parts) != 9:  # "D" + 8 values
                return
            vals = [float(x) for x in parts[1:]]
            data = {}
            for i, name in enumerate(SENSOR_ORDER):
                data[name] = {
                    "voltage": round(vals[i * 2], 4),
                    "current": round(vals[i * 2 + 1], 6),
                }
            with self._sensor_lock:
                self._sensor_data = data
            self._sensor_new.set()
        except (ValueError, IndexError):
            pass

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
        """Set the RP2040 streaming rate."""
        hz = max(0, min(200.0, float(hz)))
        resp = self._send(f"T {hz:.1f}")
        if resp and resp.startswith("OK T"):
            self._sensor_hz = hz
            log.info("Sensor stream rate set to %.1f Hz", hz)
        else:
            log.warning("Failed to set stream rate: %s", resp)

    def get_sensor_rate(self):
        return self._sensor_hz

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
        return resp == "OK P"

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
