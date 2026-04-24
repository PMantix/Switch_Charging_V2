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
import time
from time import sleep

from pathlib import Path

from server import network_mode
from server.config import SERVER_HOST, SERVER_PORT, AUTO_SCHEDULE_DIR
from server.fleet import AP_GATEWAY, my_ap_profile
from server.recorder import PiRecorder
from server.schedule import load_schedule, load_schedule_inline, validate_schedule_semantics

log = logging.getLogger(__name__)

# Broadcast rate bounds (Hz). Counter-intuitive tuning note: lower is
# NOT gentler over WiFi. Sparse traffic (5 Hz → 200 ms gaps) lets the
# Mac WiFi radio drop into power-save between packets and lets the TCP
# congestion window collapse; each new frame then pays a wake-up and
# slow-start penalty measured in tens-to-hundreds of ms, which
# accumulates into the TCP buffer and eventually kills the connection.
# A steady 30 Hz keeps the radio and TCP window fully open and is
# actually cheaper per-frame. Do not lower this without measuring.
_DEFAULT_SUBSCRIBE_HZ = 30
_MAX_SUBSCRIBE_HZ = 30


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
        self._broadcast_hz = _DEFAULT_SUBSCRIBE_HZ
        self._recorder = PiRecorder()

        # Active subscribers (set of socket objects)
        self._subscribers = set()
        self._sub_lock = threading.Lock()

        self._threads = []

        # Drive Pi-side recording from the serial reader thread so the
        # recording rate equals the RP2040's sensor stream rate — not the
        # TUI broadcast rate, which used to cap everything at 30 Hz.
        self._mc._gpio.on_sensor_tick = self._on_sensor_tick

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
            try:
                # Disable Nagle — our broadcast writes are small state-frame
                # JSON lines; with Nagle on they sit for ~200ms waiting for
                # the next write, and combine with delayed ACKs to produce
                # multi-100ms pseudo-latency that looks like a stall.
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError as exc:
                log.warning("TCP_NODELAY setsockopt failed: %s", exc)
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

            elif cmd == "set_fet":
                index = msg.get("index")
                on = msg.get("on")
                if index is None or on is None:
                    return {"ok": False, "error": "Missing 'index' or 'on' field"}
                self._mc.set_fet(int(index), bool(on))
                return {"ok": True, "fet_states": self._mc.get_status()["fet_states"]}

            elif cmd == "debug_step":
                step = self._mc.debug_step()
                return {
                    "ok": True,
                    "debug_step": step,
                    "fet_states": self._mc.get_status()["fet_states"],
                }

            elif cmd == "set_sensor_rate":
                rate = msg.get("rate")
                if rate is None:
                    return {"ok": False, "error": "Missing 'rate' field"}
                rate = float(rate)
                self._mc._gpio.set_sensor_rate(rate)
                # Broadcast to TUI capped at _MAX_SUBSCRIBE_HZ
                self._broadcast_hz = min(max(rate, _DEFAULT_SUBSCRIBE_HZ), _MAX_SUBSCRIBE_HZ)
                return {"ok": True, "sensor_rate": self._mc._gpio.get_sensor_rate()}

            elif cmd == "pi_record_start":
                max_samples = int(msg.get("max_samples", 0))
                rec_mode = msg.get("rec_mode", "unknown")
                rec_freq = float(msg.get("rec_freq", 1.0))
                rec_seq = int(msg.get("rec_seq", 0))
                rec_sensor_hz = float(msg.get("rec_sensor_hz", 15.0))
                path = self._recorder.start(
                    max_samples, mode=rec_mode, freq=rec_freq,
                    seq=rec_seq, sensor_hz=rec_sensor_hz,
                )
                return {"ok": True, "path": path}

            elif cmd == "pi_record_stop":
                path = self._recorder.stop()
                count = self._recorder.sample_count
                return {"ok": True, "path": path, "samples": count}

            elif cmd == "pi_record_status":
                return {
                    "ok": True,
                    "recording": self._recorder.is_recording,
                    "samples": self._recorder.sample_count,
                    "max_samples": self._recorder.max_samples,
                    "path": str(self._recorder.file_path) if self._recorder.file_path else None,
                }

            elif cmd == "subscribe":
                with self._sub_lock:
                    self._subscribers.add(sock)
                log.info("Client subscribed to state updates")
                return {"ok": True, "subscribed": True}

            elif cmd == "ping":
                # Latency-probe clock-offset round-trip. Echoes the client
                # timestamp and returns the server's monotonic_ns so the TUI
                # can compute offset = mac_ns - pi_ns. Sent inline on the
                # subscriber socket as event=pong so it flows through the
                # recv loop alongside state frames.
                t_client_ns = msg.get("t_client_ns", 0)
                pong = {
                    "event": "pong",
                    "t_server_ns": time.monotonic_ns(),
                    "t_client_ns": t_client_ns,
                }
                with self._sub_lock:
                    subscribed = sock in self._subscribers
                if subscribed:
                    try:
                        self._send_line(sock, pong)
                    except (OSError, BrokenPipeError):
                        pass
                    return None  # already sent
                return {"ok": True, **pong}

            # -- auto mode commands ------------------------------------------

            elif cmd == "load_schedule":
                path = msg.get("path")
                inline = msg.get("schedule")
                if inline:
                    sched = load_schedule_inline(inline)
                elif path:
                    sched = load_schedule(path)
                else:
                    return {"ok": False, "error": "Provide 'path' or 'schedule'"}
                self._mc.load_schedule(sched)
                warnings = validate_schedule_semantics(sched)
                return {
                    "ok": True,
                    "schedule_name": sched.name,
                    "steps": sched.total_steps_per_cycle,
                    "repeat": sched.repeat,
                    "warnings": warnings,
                }

            elif cmd == "list_schedules":
                sched_dir = Path(AUTO_SCHEDULE_DIR)
                files = []
                if sched_dir.is_dir():
                    files = sorted(
                        str(p) for p in sched_dir.glob("*.json")
                    )
                return {"ok": True, "schedules": files}

            elif cmd == "auto_status":
                ae = self._mc.get_auto_engine()
                if ae:
                    return {"ok": True, "auto": ae.get_status()}
                return {"ok": True, "auto": None}

            elif cmd == "auto_pause":
                ae = self._mc.get_auto_engine()
                if not ae:
                    return {"ok": False, "error": "Auto mode not active"}
                ae.pause()
                return {"ok": True}

            elif cmd == "auto_resume":
                ae = self._mc.get_auto_engine()
                if not ae:
                    return {"ok": False, "error": "Auto mode not active"}
                ae.resume()
                return {"ok": True}

            elif cmd == "auto_skip_step":
                ae = self._mc.get_auto_engine()
                if not ae:
                    return {"ok": False, "error": "Auto mode not active"}
                ae.skip_step()
                return {"ok": True, "auto": ae.get_status()}

            # -- network mode (client <-> AP) --------------------------------

            elif cmd == "set_network_mode":
                target = msg.get("mode")
                if target not in ("ap", "client"):
                    return {"ok": False, "error": "mode must be 'ap' or 'client'"}
                ack = {
                    "ok": True,
                    "mode": target,
                    "profile": my_ap_profile(),
                    "ap_address": AP_GATEWAY if target == "ap" else None,
                }
                # Defer the nmcli call — sending the ack happens synchronously
                # when we return from _dispatch, and activating the AP tears
                # down this socket. 0.5s is plenty for the TCP ack to flush.
                def _flip():
                    try:
                        result = network_mode.set_mode(target)
                        log.info("network mode flip to %s: %s", target, result)
                    except Exception:
                        log.exception("network mode flip to %s failed", target)
                threading.Timer(0.5, _flip).start()
                return ack

            else:
                return {"ok": False, "error": f"Unknown command: {cmd!r}"}

        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.exception("Error processing command %r", cmd)
            return {"ok": False, "error": f"Internal error: {exc}"}

    # -- sensor-driven recording --------------------------------------------

    def _on_sensor_tick(self, sensor_data):
        """Called from the GPIO reader thread after each fresh sensor frame
        is cached. Records one CSV row per frame (bounded by the queue in
        PiRecorder itself) so recording runs at the sensor stream rate,
        not at broadcast cadence.

        `sensor_data` is the freshly-parsed dict but we pull full status via
        mode_controller to pick up mode / sequence / step / fet_states,
        which change independently of the sensor cadence.
        """
        if not self._recorder.is_recording:
            return
        try:
            status = self._mc.get_status()
            still_going = self._recorder.record(status)
            if not still_going:
                log.info(
                    "Pi recording auto-stopped at %d samples",
                    self._recorder.sample_count,
                )
        except Exception:
            log.exception("recording tick failed")

    # -- subscription broadcast ---------------------------------------------

    def _broadcast_loop(self):
        """Push state snapshots to all subscribers when fresh sensor data arrives."""
        from time import monotonic
        last_broadcast = 0.0
        while not self._stop_event.is_set():
            # Wait for fresh sensor data, but enforce minimum interval
            min_interval = 1.0 / self._broadcast_hz
            gpio = self._mc._gpio
            got_new = gpio.wait_for_new_sensor_data(timeout=min_interval)
            if self._stop_event.is_set():
                break

            # Throttle: skip if we broadcasted too recently
            now = monotonic()
            if now - last_broadcast < min_interval * 0.9:
                continue
            last_broadcast = now

            with self._sub_lock:
                if not self._subscribers:
                    continue
                subscribers = list(self._subscribers)

            # Pi-side recording is driven from _on_sensor_tick (reader
            # thread) at the full sensor rate — not here. The broadcast
            # loop is purely for pushing state to TUI subscribers.
            status = self._mc.get_status()
            payload = {"event": "state", "t_emit_ns": time.monotonic_ns(), **status}

            dead = []
            line = json.dumps(payload, separators=(",", ":")) + "\n"
            line_bytes = line.encode("utf-8")
            for sock in subscribers:
                try:
                    # Send with a generous timeout: transient WiFi stalls or
                    # Mac-side render blips of 100-500ms are normal and
                    # shouldn't murder the TCP stream. 50ms was killing
                    # healthy connections on any brief hiccup. If a client
                    # really is hung for >2s, we do want to evict it — so
                    # keep the timeout as a kill signal, just not a
                    # hair-trigger one. (We can't retry on timeout: sendall
                    # may have partially written, which would corrupt the
                    # JSON framing on the next send.)
                    sock.settimeout(2.0)
                    sock.sendall(line_bytes)
                except socket.timeout:
                    log.warning(
                        "broadcast sendall timed out >2s — dropping subscriber"
                    )
                    dead.append(sock)
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
