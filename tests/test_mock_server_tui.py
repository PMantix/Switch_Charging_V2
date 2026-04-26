"""
End-to-end test: mock server + TUI client communication.

Starts the server in-process (mock GPIO, no hardware), connects a raw
TCP client simulating the TUI, and verifies:
  1. Connection and subscription work
  2. State broadcasts arrive with expected fields
  3. Schedule loading succeeds and returns correct metadata
  4. Entering auto mode populates auto status in broadcasts
  5. Auto status contains step list, detection, events
  6. Mode changes work (idle, charge, discharge)
  7. Auto pause/resume/skip commands work
"""

import json
import socket
import threading
import unittest
from time import sleep

from server.gpio_driver import GPIODriver
from server.sequence_engine import SequenceEngine
from server.mode_controller import ModeController
from server.command_server import CommandServer


def _send_recv(sock, cmd: dict, timeout=3.0) -> dict:
    """Send a JSON command and read one JSON response line."""
    line = json.dumps(cmd) + "\n"
    sock.sendall(line.encode())
    sock.settimeout(timeout)
    buf = ""
    while "\n" not in buf:
        data = sock.recv(4096)
        if not data:
            raise ConnectionError("Server closed")
        buf += data.decode()
    return json.loads(buf.split("\n")[0])


class TestMockServerTUI(unittest.TestCase):
    """Spin up a real server on a random port with mock GPIO, talk to it over TCP."""

    @classmethod
    def setUpClass(cls):
        """Start server subsystems once for the whole test class."""
        cls.gpio = GPIODriver()  # mock mode (no serial)
        cls.engine = SequenceEngine(cls.gpio)
        cls.mc = ModeController(cls.gpio, cls.engine)
        # Use a high port to avoid conflicts
        cls.port = 15555
        cls.cmd_server = CommandServer(cls.mc, cls.engine, port=cls.port)
        cls.cmd_server.start()
        sleep(0.3)  # let server bind

    @classmethod
    def tearDownClass(cls):
        cls.cmd_server.stop()
        cls.engine.stop()
        cls.gpio.cleanup()

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))
        sock.settimeout(3.0)
        return sock

    # ----- Basic connectivity -----

    def test_01_get_status(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "get_status"})
            self.assertTrue(resp["ok"])
            self.assertIn("mode", resp)
            self.assertIn("frequency", resp)
            self.assertIn("sensors", resp)
            self.assertIn("fet_states", resp)
            self.assertEqual(resp["mode"], "idle")
        finally:
            sock.close()

    def test_02_subscribe_receives_broadcasts(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "subscribe"})
            self.assertTrue(resp["ok"])

            # Read a few broadcast frames
            sock.settimeout(3.0)
            buf = ""
            frames = []
            deadline = __import__("time").monotonic() + 2.0
            while __import__("time").monotonic() < deadline and len(frames) < 3:
                data = sock.recv(4096)
                buf += data.decode()
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if line.strip():
                        frames.append(json.loads(line))

            self.assertGreaterEqual(len(frames), 1, "Should receive at least 1 broadcast")
            frame = frames[0]
            self.assertEqual(frame.get("event"), "state")
            self.assertIn("mode", frame)
            self.assertIn("sensors", frame)
        finally:
            sock.close()

    # ----- Mode changes -----

    def test_03_set_mode_charge(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "set_mode", "mode": "charge"})
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["mode"], "charge")

            # Verify via get_status
            resp2 = _send_recv(sock, {"cmd": "get_status"})
            self.assertEqual(resp2["mode"], "charge")
        finally:
            # Reset to idle
            _send_recv(sock, {"cmd": "set_mode", "mode": "idle"})
            sock.close()

    def test_04_set_mode_discharge(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "set_mode", "mode": "discharge"})
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["mode"], "discharge")
        finally:
            _send_recv(sock, {"cmd": "set_mode", "mode": "idle"})
            sock.close()

    def test_05_set_frequency(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "set_frequency", "frequency": 25.0})
            self.assertTrue(resp["ok"])
            self.assertAlmostEqual(resp["frequency"], 25.0, places=1)
        finally:
            sock.close()

    def test_06_set_sequence(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "set_sequence", "sequence": 3})
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["sequence"], 3)
        finally:
            sock.close()

    # ----- Schedule loading -----

    def test_10_list_schedules(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "list_schedules"})
            self.assertTrue(resp["ok"])
            self.assertIsInstance(resp["schedules"], list)
            # Should find at least the example schedules
            names = [s.split("/")[-1] for s in resp["schedules"]]
            self.assertIn("example_cccv.json", names)
            self.assertIn("example_multistage.json", names)
        finally:
            sock.close()

    def test_11_load_schedule(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {
                "cmd": "load_schedule",
                "path": "schedules/example_multistage.json",
            })
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["schedule_name"], "multistage_formation")
            self.assertEqual(resp["steps"], 11)
            self.assertEqual(resp["repeat"], 5)
            self.assertIn("warnings", resp)
            # Multistage has adjacent cc_charge steps → 1 warning
            self.assertGreaterEqual(len(resp["warnings"]), 1)
        finally:
            sock.close()

    def test_12_load_schedule_inline(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {
                "cmd": "load_schedule",
                "schedule": {
                    "name": "inline_test",
                    "steps": [
                        {"name": "R", "expected_state": "rest",
                         "circuit_action": "idle", "timeout_s": 10},
                    ],
                },
            })
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["schedule_name"], "inline_test")
        finally:
            sock.close()

    def test_13_load_invalid_schedule(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {
                "cmd": "load_schedule",
                "schedule": {"name": "", "steps": []},
            })
            self.assertFalse(resp["ok"])
            self.assertIn("error", resp)
        finally:
            sock.close()

    # ----- Schedule monitor (passive PLAN/OBSERVED) -----

    def test_20_set_mode_auto_is_rejected(self):
        """The legacy schedule-driven 'auto' mode is removed. Trying to
        enter it should fail with an unknown-mode error."""
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "set_mode", "mode": "auto"})
            self.assertFalse(resp["ok"])
        finally:
            sock.close()

    def test_21_load_schedule_starts_monitor(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {
                "cmd": "load_schedule",
                "path": "schedules/example_short_test.json",
            })
            self.assertTrue(resp["ok"])

            resp = _send_recv(sock, {"cmd": "schedule_monitor_status"})
            self.assertTrue(resp["ok"])
            sm = resp["schedule_monitor"]
            self.assertTrue(sm["loaded"])
            self.assertTrue(sm["running"])
            self.assertEqual(sm["schedule_name"], "short_bench_test")
            self.assertIn("plan", sm)
            self.assertIn("observed", sm)
            self.assertIn("divergence", sm)
        finally:
            sock.close()

    def test_22_schedule_monitor_restart_resets_clock(self):
        sock = self._connect()
        try:
            _send_recv(sock, {
                "cmd": "load_schedule",
                "path": "schedules/example_short_test.json",
            })
            sleep(0.2)
            resp = _send_recv(sock, {"cmd": "schedule_monitor_restart"})
            self.assertTrue(resp["ok"])
            sm = resp["schedule_monitor"]
            self.assertEqual(sm["plan"]["step_index"], 0)
        finally:
            sock.close()

    def test_23_schedule_monitor_stop_freezes_running(self):
        sock = self._connect()
        try:
            _send_recv(sock, {
                "cmd": "load_schedule",
                "path": "schedules/example_short_test.json",
            })
            resp = _send_recv(sock, {"cmd": "schedule_monitor_stop"})
            self.assertTrue(resp["ok"])
            self.assertFalse(resp["schedule_monitor"]["running"])
        finally:
            sock.close()

    # ----- Error handling -----

    def test_30_unknown_command(self):
        sock = self._connect()
        try:
            resp = _send_recv(sock, {"cmd": "nonexistent"})
            self.assertFalse(resp["ok"])
            self.assertIn("Unknown command", resp["error"])
        finally:
            sock.close()

    def test_31_invalid_json(self):
        sock = self._connect()
        try:
            sock.sendall(b"not json\n")
            sock.settimeout(2.0)
            data = sock.recv(4096).decode()
            resp = json.loads(data.split("\n")[0])
            self.assertFalse(resp["ok"])
            self.assertIn("Invalid JSON", resp["error"])
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
