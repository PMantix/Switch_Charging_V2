"""
Switching Circuit V2 - WiFi scan + AP join (laptop side).

Platform-dispatched helpers so the TUI can:

    - list visible pi_SW# access points broadcast by Pis in the fleet
    - tell which SSID the laptop is currently on
    - drive the laptop onto a chosen pi_SW# AP

macOS: `system_profiler SPAirPortDataType -json` (no elevation needed for scan)
       `networksetup -setairportnetwork <iface> <ssid> <pw>` (admin prompt on
       Sequoia+; the OS dialog is the user's consent surface, don't try to
       suppress it).
Linux: `nmcli -t -f SSID,SIGNAL,IN-USE dev wifi list`
       `nmcli dev wifi connect <ssid> password <pw>`
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

AP_GATEWAY = "10.42.0.1"
AP_PORT = 5555
SSID_PATTERN = re.compile(r"^pi_SW\d+$")
DEFAULT_AP_PASSWORD = "switching"

SCAN_TIMEOUT = 8.0     # seconds — WiFi scans can be slow on macOS
JOIN_TIMEOUT = 15.0    # seconds — networksetup blocks until associated
SSID_POLL_INTERVAL = 0.5
SSID_POLL_DEADLINE = 10.0


@dataclass(frozen=True)
class PiAP:
    ssid: str
    signal_dbm: Optional[int]   # None if the platform doesn't report it
    is_current: bool


@dataclass(frozen=True)
class JoinResult:
    ok: bool
    ssid: str
    joined_ssid: Optional[str]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


# ---------------------------------------------------------------------------
# Current SSID
# ---------------------------------------------------------------------------

def current_ssid() -> Optional[str]:
    """SSID the laptop is currently joined to, or None."""
    if _is_macos():
        return _current_ssid_macos()
    if _is_linux():
        return _current_ssid_linux()
    return None


def _current_ssid_macos() -> Optional[str]:
    try:
        result = subprocess.run(
            ["networksetup", "-getairportnetwork", _wifi_interface_macos()],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        # Output: "Current Wi-Fi Network: pi_SW1" or "You are not associated..."
        line = result.stdout.strip()
        marker = "Current Wi-Fi Network: "
        if marker in line:
            return line.split(marker, 1)[1].strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _current_ssid_linux() -> Optional[str]:
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.startswith("yes:"):
                return line.split(":", 1)[1]
    except (subprocess.SubprocessError, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_pi_aps() -> list[PiAP]:
    """Return visible pi_SW# access points, sorted by signal strength desc."""
    if _is_macos():
        aps = _scan_macos()
    elif _is_linux():
        aps = _scan_linux()
    else:
        log.warning("wifi_scan: unsupported platform %s", sys.platform)
        aps = []
    return sorted(
        aps,
        key=lambda a: (a.signal_dbm if a.signal_dbm is not None else -999),
        reverse=True,
    )


def _scan_macos() -> list[PiAP]:
    """Use system_profiler — returns rich JSON including the current SSID."""
    try:
        result = subprocess.run(
            ["system_profiler", "-json", "SPAirPortDataType"],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT,
        )
        if result.returncode != 0:
            log.warning("system_profiler failed: %s", result.stderr.strip())
            return []
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        log.warning("macOS scan failed: %s", exc)
        return []

    current = _current_ssid_macos()
    found: dict[str, PiAP] = {}

    # Structure: SPAirPortDataType -> [ { spairport_airport_interfaces: [ { spairport_airport_other_local_wireless_networks: [ {...} ] } ] } ]
    for root in data.get("SPAirPortDataType", []):
        for iface in root.get("spairport_airport_interfaces", []):
            for key in (
                "spairport_airport_other_local_wireless_networks",
                "spairport_airport_local_wireless_networks",
            ):
                for net in iface.get(key, []) or []:
                    ssid = net.get("_name")
                    if not ssid or not SSID_PATTERN.match(ssid):
                        continue
                    signal = _parse_macos_signal(net.get("spairport_signal_noise"))
                    ap = PiAP(ssid=ssid, signal_dbm=signal, is_current=(ssid == current))
                    # Deduplicate multiple BSSIDs advertising the same SSID.
                    existing = found.get(ssid)
                    if existing is None or (signal is not None and (
                        existing.signal_dbm is None or signal > existing.signal_dbm
                    )):
                        found[ssid] = ap

    # Also include the current SSID if it matches and didn't show in the scan
    # (system_profiler sometimes omits the connected network from "other").
    if current and SSID_PATTERN.match(current) and current not in found:
        found[current] = PiAP(ssid=current, signal_dbm=None, is_current=True)

    return list(found.values())


def _parse_macos_signal(field) -> Optional[int]:
    """Pull the dBm number out of a `spairport_signal_noise` string like
    "-62 dBm / -90 dBm"."""
    if not field:
        return None
    m = re.search(r"(-?\d+)\s*dBm", str(field))
    return int(m.group(1)) if m else None


def _scan_linux() -> list[PiAP]:
    try:
        # Trigger a fresh scan, then read the cache.
        subprocess.run(
            ["nmcli", "dev", "wifi", "rescan"],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT,
        )
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,IN-USE", "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("nmcli scan failed: %s", exc)
        return []

    aps: dict[str, PiAP] = {}
    for line in result.stdout.splitlines():
        # nmcli terse format escapes colons in SSIDs as "\:" — fine for our pattern.
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid = parts[0]
        if not SSID_PATTERN.match(ssid):
            continue
        try:
            signal_0_100 = int(parts[1]) if parts[1] else None
        except ValueError:
            signal_0_100 = None
        in_use = parts[2].strip() == "*"
        # nmcli reports signal as 0–100. Approximate dBm via -100 + signal
        # (nmcli uses a linear-ish mapping of a logarithmic quantity, so this
        # is fine for comparison but not as precise as macOS's real dBm).
        signal_dbm = (signal_0_100 - 100) if signal_0_100 is not None else None
        existing = aps.get(ssid)
        if existing is None or (signal_dbm is not None and (
            existing.signal_dbm is None or signal_dbm > existing.signal_dbm
        )):
            aps[ssid] = PiAP(ssid=ssid, signal_dbm=signal_dbm, is_current=in_use)
    return list(aps.values())


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def join_ap(ssid: str, password: str = DEFAULT_AP_PASSWORD) -> JoinResult:
    """Drive the laptop onto <ssid>. Blocks up to ~25 s.

    Returns a JoinResult; on success joined_ssid == ssid. On macOS the OS
    may prompt for admin credentials — that prompt is the user's consent
    surface, we don't try to suppress or pre-auth it.
    """
    if not SSID_PATTERN.match(ssid):
        return JoinResult(False, ssid, None, f"refusing to join non-fleet SSID {ssid!r}")

    if _is_macos():
        err = _join_macos(ssid, password)
    elif _is_linux():
        err = _join_linux(ssid, password)
    else:
        return JoinResult(False, ssid, None, f"unsupported platform {sys.platform}")

    if err:
        return JoinResult(False, ssid, current_ssid(), err)

    # Poll for the SSID switch to land — networksetup returns before the
    # association finalises.
    deadline = time.monotonic() + SSID_POLL_DEADLINE
    while time.monotonic() < deadline:
        now_ssid = current_ssid()
        if now_ssid == ssid:
            return JoinResult(True, ssid, now_ssid)
        time.sleep(SSID_POLL_INTERVAL)

    return JoinResult(False, ssid, current_ssid(), "join timed out waiting for SSID")


def _join_macos(ssid: str, password: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["networksetup", "-setairportnetwork", _wifi_interface_macos(), ssid, password],
            capture_output=True, text=True, timeout=JOIN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "networksetup timed out"
    except OSError as exc:
        return f"networksetup unavailable: {exc}"
    if result.returncode != 0 or "Error" in result.stdout:
        return (result.stderr or result.stdout).strip() or "networksetup failed"
    return None


def _join_linux(ssid: str, password: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["nmcli", "dev", "wifi", "connect", ssid, "password", password],
            capture_output=True, text=True, timeout=JOIN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "nmcli timed out"
    except OSError as exc:
        return f"nmcli unavailable: {exc}"
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or "nmcli failed"
    return None


# ---------------------------------------------------------------------------
# WiFi interface detection (macOS)
# ---------------------------------------------------------------------------

_wifi_iface_cache: Optional[str] = None


def _wifi_interface_macos() -> str:
    """Find the WiFi interface name on macOS (usually en0, but not guaranteed)."""
    global _wifi_iface_cache
    if _wifi_iface_cache:
        return _wifi_iface_cache
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5,
        )
        port_block: list[str] = []
        for line in result.stdout.splitlines():
            port_block.append(line)
            if line.startswith("Device:"):
                block_text = "\n".join(port_block[-3:])
                if "Wi-Fi" in block_text or "AirPort" in block_text:
                    _wifi_iface_cache = line.split(":", 1)[1].strip()
                    return _wifi_iface_cache
                port_block = []
    except (subprocess.SubprocessError, OSError):
        pass
    _wifi_iface_cache = "en0"
    return _wifi_iface_cache
