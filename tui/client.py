"""
Switching Circuit V2 - TCP Client for Raspberry Pi server.

Manages a persistent TCP connection to the Pi, sends JSON commands,
and receives the ~15 Hz state stream in a background thread.
"""

import json
import logging
import socket
import threading
import time
from enum import Enum
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class PiClient:
    """TCP connection manager for the Switching Circuit Pi server."""

    def __init__(
        self,
        on_state: Optional[Callable[[dict], None]] = None,
        on_connection_change: Optional[Callable[[ConnectionState], None]] = None,
        retry_delay: float = 2.0,
        max_retries: int = 0,
    ):
        self._host: str = ""
        self._port: int = 5555
        self._sock: Optional[socket.socket] = None
        self._rfile: Optional[Any] = None
        self._wlock = threading.Lock()

        self._state = ConnectionState.DISCONNECTED
        self._on_state = on_state
        self._on_connection_change = on_connection_change
        self._retry_delay = retry_delay
        self._max_retries = max_retries  # 0 = infinite

        self._recv_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._subscribed = False
        self._latency: float = 0.0

    # -- Properties ----------------------------------------------------------

    @property
    def connection_state(self) -> ConnectionState:
        return self._state

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def latency_ms(self) -> float:
        return self._latency * 1000

    # -- Connection ----------------------------------------------------------

    def _set_state(self, state: ConnectionState) -> None:
        self._state = state
        if self._on_connection_change:
            try:
                self._on_connection_change(state)
            except Exception:
                pass

    def connect(self, host: str, port: int = 5555) -> bool:
        """Establish a TCP connection to the Pi server."""
        self._host = host
        self._port = port
        self._stop_event.clear()

        self._set_state(ConnectionState.CONNECTING)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((host, port))
            sock.settimeout(None)
            self._sock = sock
            self._rfile = sock.makefile("r", encoding="utf-8")
            self._set_state(ConnectionState.CONNECTED)
            log.info("Connected to %s:%d", host, port)

            # Start the receive loop
            self._recv_thread = threading.Thread(
                target=self._recv_loop, daemon=True, name="pi-recv"
            )
            self._recv_thread.start()
            return True

        except (OSError, ConnectionError) as exc:
            log.warning("Connection to %s:%d failed: %s", host, port, exc)
            self._set_state(ConnectionState.DISCONNECTED)
            return False

    def disconnect(self) -> None:
        """Cleanly close the connection."""
        self._stop_event.set()
        self._subscribed = False
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._rfile = None
        self._set_state(ConnectionState.DISCONNECTED)
        log.info("Disconnected")

    def connect_with_retry(self, host: str, port: int = 5555) -> None:
        """Try to connect in a background thread, retrying on failure."""
        def _retry_loop() -> None:
            attempts = 0
            while not self._stop_event.is_set():
                attempts += 1
                if self.connect(host, port):
                    return
                if self._max_retries and attempts >= self._max_retries:
                    log.warning("Max retries (%d) reached", self._max_retries)
                    return
                self._stop_event.wait(self._retry_delay)

        t = threading.Thread(target=_retry_loop, daemon=True, name="pi-connect")
        t.start()

    # -- Commands ------------------------------------------------------------

    def send_command(self, cmd_dict: dict) -> Optional[dict]:
        """Send a JSON command and return the parsed response (blocking)."""
        if self._state != ConnectionState.CONNECTED or not self._sock:
            return {"ok": False, "error": "Not connected"}

        line = json.dumps(cmd_dict) + "\n"
        t0 = time.monotonic()
        try:
            with self._wlock:
                self._sock.sendall(line.encode("utf-8"))
            # If we're subscribed, responses come through the recv loop,
            # and we don't expect a synchronous reply here.  For non-subscribe
            # commands sent before subscribing, we read a line directly.
            if not self._subscribed and self._rfile:
                resp_line = self._rfile.readline()
                self._latency = time.monotonic() - t0
                if not resp_line:
                    raise ConnectionError("Server closed connection")
                return json.loads(resp_line)
            return {"ok": True}
        except (OSError, ConnectionError, json.JSONDecodeError) as exc:
            log.warning("send_command failed: %s", exc)
            self._handle_disconnect()
            return {"ok": False, "error": str(exc)}

    def subscribe(self) -> Optional[dict]:
        """Send the subscribe command to start the state stream."""
        if self._state != ConnectionState.CONNECTED:
            return {"ok": False, "error": "Not connected"}
        # Send subscribe and read the ack before marking subscribed
        resp = self.send_command({"cmd": "subscribe"})
        self._subscribed = True
        return resp

    def get_status(self) -> Optional[dict]:
        return self.send_command({"cmd": "get_status"})

    def set_mode(self, mode: str) -> Optional[dict]:
        return self.send_command({"cmd": "set_mode", "mode": mode})

    def set_sequence(self, sequence: int) -> Optional[dict]:
        return self.send_command({"cmd": "set_sequence", "sequence": sequence})

    def set_frequency(self, frequency: float) -> Optional[dict]:
        return self.send_command({"cmd": "set_frequency", "frequency": frequency})

    def set_fet(self, index: int, on: bool) -> Optional[dict]:
        return self.send_command({"cmd": "set_fet", "index": index, "on": on})

    def debug_step(self) -> Optional[dict]:
        return self.send_command({"cmd": "debug_step"})

    # -- Auto mode -----------------------------------------------------------

    def load_schedule(self, path: str) -> Optional[dict]:
        return self.send_command({"cmd": "load_schedule", "path": path})

    def list_schedules(self) -> Optional[dict]:
        return self.send_command({"cmd": "list_schedules"})

    def auto_status(self) -> Optional[dict]:
        return self.send_command({"cmd": "auto_status"})

    def auto_pause(self) -> Optional[dict]:
        return self.send_command({"cmd": "auto_pause"})

    def auto_resume(self) -> Optional[dict]:
        return self.send_command({"cmd": "auto_resume"})

    def auto_skip_step(self) -> Optional[dict]:
        return self.send_command({"cmd": "auto_skip_step"})

    # -- Background receive --------------------------------------------------

    def _recv_loop(self) -> None:
        """Read lines from the socket in a loop, dispatching state events."""
        while not self._stop_event.is_set():
            try:
                if not self._rfile:
                    break
                line = self._rfile.readline()
                if not line:
                    raise ConnectionError("Server closed connection")
                data = json.loads(line)
                if data.get("event") == "state" and self._on_state:
                    self._on_state(data)
            except (OSError, ConnectionError, json.JSONDecodeError) as exc:
                if not self._stop_event.is_set():
                    log.warning("Receive loop error: %s", exc)
                    self._handle_disconnect()
                break

    def _handle_disconnect(self) -> None:
        """Handle an unexpected disconnect; attempt auto-reconnect."""
        if self._state == ConnectionState.DISCONNECTED:
            return
        old_host, old_port = self._host, self._port
        self.disconnect()
        # Auto-reconnect
        self.connect_with_retry(old_host, old_port)
