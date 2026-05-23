"""
Fleet poller — lightweight TCP client for one-shot status polls.

Unlike the TUI's PiClient (persistent connection + subscription stream),
this connects, sends get_status, reads the reply, and disconnects.
Designed for the AP-cycling workflow where each Pi is only reachable
for a few seconds per cycle.
"""

import json
import logging
import socket
from typing import Optional

log = logging.getLogger(__name__)

AP_GATEWAY = "10.42.0.1"
AP_PORT = 5555
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 3.0


def poll_status(host: str = AP_GATEWAY, port: int = AP_PORT) -> Optional[dict]:
    """Connect, send get_status, return the response dict, disconnect."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect((host, port))
        sock.settimeout(READ_TIMEOUT)

        cmd = json.dumps({"cmd": "get_status"}) + "\n"
        sock.sendall(cmd.encode("utf-8"))

        rfile = sock.makefile("r", encoding="utf-8")
        line = rfile.readline()
        if not line:
            log.warning("poll_status: empty response from %s:%d", host, port)
            return None
        resp = json.loads(line)
        rfile.close()
        sock.close()
        return resp
    except (OSError, ConnectionError, json.JSONDecodeError) as exc:
        log.warning("poll_status failed (%s:%d): %s", host, port, exc)
        return None


def send_command(cmd_dict: dict, host: str = AP_GATEWAY, port: int = AP_PORT) -> Optional[dict]:
    """Connect, send a single command, return the response, disconnect."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect((host, port))
        sock.settimeout(READ_TIMEOUT)

        line = json.dumps(cmd_dict) + "\n"
        sock.sendall(line.encode("utf-8"))

        rfile = sock.makefile("r", encoding="utf-8")
        resp_line = rfile.readline()
        if not resp_line:
            log.warning("send_command: empty response from %s:%d", host, port)
            return None
        resp = json.loads(resp_line)
        rfile.close()
        sock.close()
        return resp
    except (OSError, ConnectionError, json.JSONDecodeError) as exc:
        log.warning("send_command failed (%s:%d): %s", host, port, exc)
        return None
