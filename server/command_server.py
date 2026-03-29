"""
Switching Circuit V2 - TCP JSON Command Server.

Listens on a configurable port for line-delimited JSON commands.
Supports multiple concurrent clients and a streaming subscription mode
that pushes state updates at ~15 Hz.
"""

import json
import logging
import socket
import threading
from time import sleep

from server.config import SERVER_HOST, SERVER_PORT

log = logging.getLogger(__name__)

# Update rate for subscription broadcasts (Hz)
_SUBSCRIBE_HZ = 15


class CommandServer:
    """
    TCP server that accepts JSON-line commands and dispatches them
    to the ModeController and SequenceEngine.
    """

    def __init__(self, mode_controller, sequence_engine, host=SERVER_HOST, port=SERVER_PORT):
        self._mc = mode_controller
        self._engine = sequence_engine
        self._host = host
        self._port = port

        self._server_socket = None
        self._stop_event = threading.Event()

        # Active subscribers (set of socket objects)
        self._subscribers = set()
        self._sub_lock = threading.Lock()

        self._threads = []

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        """Bind and start accepting connections in a background thread."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self._host, self._port))
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)  # allow periodic stop checks

        accept_t = threading.Thread(
            target=self._accept_loop, name="CmdServer-Accept", daemon=True,
        )
        accept_t.start()
        self._threads.append(accept_t)

        broadcast_t = threading.Thread(
            target=self._broadcast_loop, name="CmdServer-Broadcast", daemon=True,
        )
        broadcast_t.start()
        self._threads.append(broadcast_t)

        log.info("CommandServer listening on %s:%d", self._host, self._port)

    def stop(self):
        """Shut down the server and disconnect all clients."""
        self._stop_event.set()
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
        with self._sub_lock:
            for sock in list(self._subscribers):
                try:
                    sock.close()
                except OSError:
                    pass
            self._subscribers.clear()
        for t in self._threads:
            t.join(timeout=3.0)
        log.info("CommandServer stopped")

    # -- accept loop --------------------------------------------------------

    def _accept_loop(self):
        while not self._stop_event.is_set():
            try:
                client_sock, addr = self._server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            log.info("Client connected: %s:%d", *addr)
            t = threading.Thread(
                target=self._handle_client,
                args=(client_sock, addr),
                name=f"CmdServer-Client-{addr[0]}:{addr[1]}",
                daemon=True,
            )
            t.start()

    # -- client handler -----------------------------------------------------

    def _handle_client(self, sock, addr):
        """Read line-delimited JSON commands from a single client."""
        buf = ""
        sock.settimeout(1.0)
        try:
            while not self._stop_event.is_set():
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not data:
                    break  # client disconnected

                buf += data.decode("utf-8", errors="replace")

                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    response = self._dispatch(line, sock)
                    if response is not None:
                        self._send_line(sock, response)
        except Exception:
            log.exception("Unexpected error handling client %s:%d", *addr)
        finally:
            with self._sub_lock:
                self._subscribers.discard(sock)
            try:
                sock.close()
            except OSError:
                pass
            log.info("Client disconnected: %s:%d", *addr)

    # -- command dispatch ---------------------------------------------------

    def _dispatch(self, line, sock):
        """Parse a JSON command line and return a response dict (or None for subscribe)."""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"Invalid JSON: {exc}"}

        cmd = msg.get("cmd")
        if not cmd:
            return {"ok": False, "error": "Missing 'cmd' field"}

        try:
            if cmd == "get_status":
                status = self._mc.get_status()
                return {"ok": True, **status}

            elif cmd == "set_mode":
                mode_str = msg.get("mode")
                if mode_str is None:
                    return {"ok": False, "error": "Missing 'mode' field"}
                new_mode = self._mc.set_mode(mode_str)
                return {"ok": True, "mode": new_mode.value}

            elif cmd == "set_sequence":
                seq = msg.get("sequence")
                if seq is None:
                    return {"ok": False, "error": "Missing 'sequence' field"}
                self._engine.set_sequence(int(seq))
                return {"ok": True, "sequence": self._engine.get_sequence()}

            elif cmd == "set_frequency":
                freq = msg.get("frequency")
                if freq is None:
                    return {"ok": False, "error": "Missing 'frequency' field"}
                self._engine.set_frequency(float(freq))
                return {"ok": True, "frequency": self._engine.get_frequency()}

            elif cmd == "subscribe":
                with self._sub_lock:
                    self._subscribers.add(sock)
                log.info("Client subscribed to state updates")
                return {"ok": True, "subscribed": True}

            else:
                return {"ok": False, "error": f"Unknown command: {cmd!r}"}

        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Error processing command %r", cmd)
            return {"ok": False, "error": f"Internal error: {exc}"}

    # -- subscription broadcast ---------------------------------------------

    def _broadcast_loop(self):
        """Push state snapshots to all subscribers at ~15 Hz."""
        interval = 1.0 / _SUBSCRIBE_HZ
        while not self._stop_event.is_set():
            sleep(interval)

            with self._sub_lock:
                if not self._subscribers:
                    continue
                subscribers = list(self._subscribers)

            status = self._mc.get_status()
            payload = {"event": "state", **status}

            dead = []
            for sock in subscribers:
                try:
                    self._send_line(sock, payload)
                except (OSError, BrokenPipeError):
                    dead.append(sock)

            if dead:
                with self._sub_lock:
                    for sock in dead:
                        self._subscribers.discard(sock)
                        try:
                            sock.close()
                        except OSError:
                            pass

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _send_line(sock, obj):
        """Serialize a dict as a JSON line and send it."""
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        sock.sendall(line.encode("utf-8"))
