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

        # Latency-probe ping slot. Only one ping in flight at a time; the
        # recv loop fills in t_server_ns and the receive time, then fires
        # the event so ping_server() can compute offset.
        self._ping_lock = threading.Lock()
        self._ping_event: Optional[threading.Event] = None
        self._ping_token: int = 0
        self._ping_t_server_ns: int = 0
        self._ping_t_recv_ns: int = 0

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
            # Recv thread is started by subscribe() — not here. Otherwise it
            # races send_command's direct readline path (the recv loop only
            # routes event=state / event=pong, drops everything else, and
            # whoever wins the race on each line decides whether the caller
            # sees the reply or it vanishes). With deferred start, all
            # pre-subscribe send_command calls (subscribe itself, on-connect
            # bootstrap) read their replies cleanly.
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

    def reconnect_to(self, host: str, port: int = 5555) -> None:
        """Disconnect any current socket, then retarget the client at host:port.

        Used by the AP-flip flow: after sending `set_network_mode`, the Pi's
        current socket will die within a second, so we tear ours down
        proactively and start the retry loop against the new address.
        """
        self.disconnect()
        self.connect_with_retry(host, port)

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
        # Now that the ack is in hand, hand the socket over to the recv
        # thread. From this point on, command replies and state events
        # both arrive through the same loop — which is fine because the
        # server only pushes state events from here on, and command
        # replies are routed on demand (e.g. ping_server's Event).
        if self._recv_thread is None or not self._recv_thread.is_alive():
            self._recv_thread = threading.Thread(
                target=self._recv_loop, daemon=True, name="pi-recv"
            )
            self._recv_thread.start()
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

    # -- Background receive --------------------------------------------------

    def _recv_loop(self) -> None:
        """Read lines from the socket in a loop, dispatching state events."""
        while not self._stop_event.is_set():
            try:
                if not self._rfile:
                    break
                line = self._rfile.readline()
                t_recv_ns = time.monotonic_ns()
                if not line:
                    raise ConnectionError("Server closed connection")
                data = json.loads(line)
                event = data.get("event")
                if event == "state" and self._on_state:
                    # Inject mac-side receive timestamp so the app can
                    # measure queue and net latency without a second dict.
                    data["_t_recv_ns"] = t_recv_ns
                    self._on_state(data)
                elif event == "pong":
                    self._handle_pong(data, t_recv_ns)
            except (OSError, ConnectionError, json.JSONDecodeError) as exc:
                if not self._stop_event.is_set():
                    log.warning("Receive loop error: %s", exc)
                    self._handle_disconnect()
                break

    def _handle_pong(self, data: dict, t_recv_ns: int) -> None:
        with self._ping_lock:
            ev = self._ping_event
            if ev is None or data.get("t_client_ns") != self._ping_token:
                return  # stale or unexpected pong
            self._ping_t_server_ns = int(data.get("t_server_ns", 0))
            self._ping_t_recv_ns = t_recv_ns
        ev.set()

    def ping_server(self, timeout: float = 1.0) -> Optional[int]:
        """Measure clock offset (mac_ns - pi_ns) via one ping round-trip.

        Returns the offset in nanoseconds, or None on timeout / failure.
        Uses min-RTT/2 under the assumption of symmetric latency.
        """
        if self._state != ConnectionState.CONNECTED or not self._sock:
            return None
        ev = threading.Event()
        t_send_ns = time.monotonic_ns()
        with self._ping_lock:
            self._ping_event = ev
            self._ping_token = t_send_ns
            self._ping_t_server_ns = 0
            self._ping_t_recv_ns = 0
        try:
            line = json.dumps({"cmd": "ping", "t_client_ns": t_send_ns}) + "\n"
            with self._wlock:
                self._sock.sendall(line.encode("utf-8"))
        except (OSError, ConnectionError) as exc:
            log.warning("ping send failed: %s", exc)
            with self._ping_lock:
                self._ping_event = None
            return None

        if not ev.wait(timeout):
            with self._ping_lock:
                self._ping_event = None
            return None

        with self._ping_lock:
            t_server = self._ping_t_server_ns
            t_recv = self._ping_t_recv_ns
            self._ping_event = None

        if t_server == 0:
            return None
        # rtt/2 offset: mac_time_at_server_response ≈ t_send + rtt/2
        rtt = t_recv - t_send_ns
        offset = (t_send_ns + rtt // 2) - t_server  # mac_ns - pi_ns
        return offset

    def _handle_disconnect(self) -> None:
        """Handle an unexpected disconnect; attempt auto-reconnect."""
        if self._state == ConnectionState.DISCONNECTED:
            return
        old_host, old_port = self._host, self._port
        self.disconnect()
        # Auto-reconnect
        self.connect_with_retry(old_host, old_port)
