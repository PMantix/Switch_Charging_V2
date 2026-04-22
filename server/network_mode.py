"""
Switching Circuit V2 - Network mode flip (client <-> access point).

Wraps `nmcli` so the command server can flip a Pi between its client
WiFi (e.g. iPhone hotspot) and its own `pi_SW#` access point. The
activation drops the caller's TCP session by nature — the caller must
ack the request to the client *before* invoking these helpers.

Auth: if the `pi` user is in the `netdev` group, Debian's default
polkit rules usually allow `nmcli connection up/down` without sudo. If
that turns out not to work on a given Pi, drop this snippet into
/etc/sudoers.d/switch-charging-nm:

    pi ALL=(root) NOPASSWD: /usr/bin/nmcli connection up pi_SW*, \\
                            /usr/bin/nmcli connection down pi_SW*

and set the `sudo_fallback=True` kwarg below. We try without sudo first
in any case.
"""

import logging
import subprocess
from typing import Optional

from server.fleet import AP_GATEWAY, my_ap_profile

log = logging.getLogger(__name__)

NMCLI = "/usr/bin/nmcli"
NMCLI_TIMEOUT = 15.0  # seconds — AP up can be slow as NM negotiates the radio


class NetworkModeError(Exception):
    """Raised when nmcli fails for reasons other than authorization."""


def _run_nmcli(args: list[str], use_sudo: bool = False) -> subprocess.CompletedProcess:
    argv = (["sudo", "-n", NMCLI] if use_sudo else [NMCLI]) + args
    log.info("nmcli: %s", " ".join(argv))
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=NMCLI_TIMEOUT
    )


def _activate(profile: str, sudo_fallback: bool = True) -> subprocess.CompletedProcess:
    """`nmcli connection up <profile>` with optional sudo retry."""
    result = _run_nmcli(["connection", "up", profile])
    if result.returncode != 0 and sudo_fallback and _looks_like_auth_error(result):
        log.warning("Polkit refused `nmcli up %s`; retrying via sudo", profile)
        result = _run_nmcli(["connection", "up", profile], use_sudo=True)
    return result


def _deactivate(profile: str, sudo_fallback: bool = True) -> subprocess.CompletedProcess:
    """`nmcli connection down <profile>` with optional sudo retry."""
    result = _run_nmcli(["connection", "down", profile])
    if result.returncode != 0 and sudo_fallback and _looks_like_auth_error(result):
        log.warning("Polkit refused `nmcli down %s`; retrying via sudo", profile)
        result = _run_nmcli(["connection", "down", profile], use_sudo=True)
    return result


def _looks_like_auth_error(result: subprocess.CompletedProcess) -> bool:
    haystack = (result.stderr or "") + (result.stdout or "")
    lowered = haystack.lower()
    return any(
        tag in lowered
        for tag in ("not authorized", "authorization", "permission denied")
    )


def set_mode(mode: str) -> dict:
    """Flip the Pi to AP mode (activate pi_SW#) or back to client mode (deactivate).

    mode='ap'     -> `nmcli connection up <my_ap_profile>`
                     NM switches the single WiFi radio to AP; client WiFi drops.
    mode='client' -> `nmcli connection down <my_ap_profile>`
                     The higher-priority client profile (iPhone/Aquino) autoconnects.

    Returns a dict suitable for logging. The caller should not block on
    this — activation tears down the socket that carried the request.
    """
    profile = my_ap_profile()

    try:
        if mode == "ap":
            result = _activate(profile)
        elif mode == "client":
            result = _deactivate(profile)
        else:
            return {"ok": False, "error": f"unknown mode {mode!r}"}
    except subprocess.TimeoutExpired as exc:
        log.error("nmcli timed out: %s", exc)
        return {"ok": False, "error": f"nmcli timed out after {NMCLI_TIMEOUT}s"}
    except OSError as exc:
        log.exception("nmcli invocation failed")
        return {"ok": False, "error": str(exc)}

    ok = result.returncode == 0
    payload: dict = {
        "ok": ok,
        "mode": mode,
        "profile": profile,
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }
    if ok and mode == "ap":
        payload["ap_address"] = AP_GATEWAY
    if not ok:
        log.error("nmcli %s %s failed: %s", mode, profile, payload["stderr"])
    else:
        log.info("nmcli %s %s succeeded", mode, profile)
    return payload
