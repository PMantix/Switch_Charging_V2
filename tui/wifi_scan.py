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
Windows: `netsh wlan show networks mode=bssid` (scan)
       `netsh wlan add profile filename=<xml>` + `netsh wlan connect name=<ssid>`
       (connect needs a saved profile, so we synthesize a WPA2-PSK profile XML).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

AP_GATEWAY = "10.42.0.1"
AP_PORT = 5555
SSID_PATTERN = re.compile(r"^pi_SW\d+$")
DEFAULT_AP_PASSWORD = "raspberry"

# system_profiler's cache is fast to read but frequently stale on recent macOS,
# so we prefer CoreWLAN (via a tiny Swift helper) which does a real live scan.
# The CoreWLAN scan sweeps all channels and can take 20-30s on dual-band radios.
SCAN_TIMEOUT = 8.0            # system_profiler fallback — returns cached data
SCAN_TIMEOUT_CORE = 15.0      # Swift + CoreWLAN live scan (usually ~5-8s)
JOIN_TIMEOUT = 20.0           # networksetup cold join
GATEWAY_POLL_INTERVAL = 0.15
GATEWAY_POLL_DEADLINE = 10.0  # how long to wait for AP_GATEWAY:AP_PORT TCP


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


@dataclass(frozen=True)
class ScanResult:
    aps: list[PiAP]
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_windows() -> bool:
    return sys.platform == "win32"


# ---------------------------------------------------------------------------
# Current SSID
# ---------------------------------------------------------------------------

def current_ssid() -> Optional[str]:
    """SSID the laptop is currently joined to, or None."""
    if _is_macos():
        return _current_ssid_macos()
    if _is_linux():
        return _current_ssid_linux()
    if _is_windows():
        return _current_ssid_windows()
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


def _current_ssid_windows() -> Optional[str]:
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    # Output has both "SSID : pi_SW1" and "BSSID : aa:bb:..." — match the
    # SSID line only (skip BSSID), and only when actually associated.
    for line in result.stdout.splitlines():
        s = line.strip()
        if s.startswith("SSID") and not s.startswith("BSSID") and ":" in s:
            val = s.split(":", 1)[1].strip()
            return val or None
    return None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_pi_aps() -> ScanResult:
    """Return visible pi_SW# access points, sorted by signal strength desc.

    The `warning` field carries a human-readable hint for cases where the
    scan ran but couldn't actually see SSIDs (e.g. macOS Location Services
    disabled for the host Terminal app — the OS returns the JSON shape but
    with empty `_name` fields).
    """
    if _is_macos():
        aps, warning = _scan_macos()
    elif _is_linux():
        aps, warning = _scan_linux(), None
    elif _is_windows():
        aps, warning = _scan_windows(), None
    else:
        log.warning("wifi_scan: unsupported platform %s", sys.platform)
        aps, warning = [], None
    aps = sorted(
        aps,
        key=lambda a: (a.signal_dbm if a.signal_dbm is not None else -999),
        reverse=True,
    )
    return ScanResult(aps=aps, warning=warning)


def _scan_macos() -> tuple[list[PiAP], Optional[str]]:
    """macOS scan. Prefers CoreWLAN live scan via Swift (what the menu bar
    uses) so newly-broadcasting APs actually appear. Falls back to
    system_profiler when Swift isn't available."""
    aps, warning = _scan_macos_corewlan()
    if aps is not None:
        return aps, warning
    return _scan_macos_system_profiler()


_SWIFT_CHECKED = False
_SWIFT_OK = False


def _swift_available() -> bool:
    global _SWIFT_CHECKED, _SWIFT_OK
    if _SWIFT_CHECKED:
        return _SWIFT_OK
    _SWIFT_CHECKED = True
    if not shutil.which("swift"):
        return False
    try:
        r = subprocess.run(
            ["swift", "--version"], capture_output=True, text=True, timeout=3,
        )
        _SWIFT_OK = r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        _SWIFT_OK = False
    return _SWIFT_OK


_COREWLAN_SCRIPT = r"""
import CoreWLAN
import Foundation

guard let iface = CWWiFiClient.shared().interface() else {
    print("ERR\tno_interface")
    exit(1)
}
print("CURRENT\t\(iface.ssid() ?? "")")
do {
    let networks = try iface.scanForNetworks(withName: nil)
    for n in networks {
        if let s = n.ssid {
            // Filter happens on the Python side; we keep this simple so
            // callers can repurpose the helper later if needed.
            print("HIT\t\(s)\t\(n.rssiValue)")
        }
    }
} catch {
    print("ERR\t\(error)")
    exit(2)
}
"""


def _scan_macos_corewlan() -> tuple[Optional[list[PiAP]], Optional[str]]:
    """Return (aps, warning) using Swift + CoreWLAN for a real live scan.
    Returns (None, _) if the Swift path is unavailable — caller falls back."""
    if not _swift_available():
        return None, None

    path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".swift", delete=False, encoding="utf-8",
        ) as f:
            f.write(_COREWLAN_SCRIPT)
            path = f.name
        result = subprocess.run(
            ["swift", path],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT_CORE,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("CoreWLAN scan failed: %s", exc)
        return None, None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    if result.returncode != 0:
        log.warning("CoreWLAN scan returncode %d: %s",
                    result.returncode, result.stderr.strip()[:200])
        return None, None

    current = ""
    aps: dict[str, PiAP] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        tag = parts[0]
        if tag == "CURRENT" and len(parts) >= 2:
            current = parts[1]
        elif tag == "HIT" and len(parts) >= 3:
            ssid = parts[1]
            if not SSID_PATTERN.match(ssid):
                continue
            try:
                rssi = int(parts[2])
            except ValueError:
                rssi = None
            is_current = (ssid == current)
            existing = aps.get(ssid)
            if existing is None or (rssi is not None and (
                existing.signal_dbm is None or rssi > existing.signal_dbm
            )):
                aps[ssid] = PiAP(ssid=ssid, signal_dbm=rssi, is_current=is_current)
    return list(aps.values()), None


def _scan_macos_system_profiler() -> tuple[list[PiAP], Optional[str]]:
    """Use system_profiler — returns rich JSON including the current SSID.
    Serves a cached list; kept as a fallback when Swift isn't available."""
    try:
        result = subprocess.run(
            ["system_profiler", "-json", "SPAirPortDataType"],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT,
        )
        if result.returncode != 0:
            log.warning("system_profiler failed: %s", result.stderr.strip())
            return [], None
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        log.warning("macOS scan failed: %s", exc)
        return [], None

    current = _current_ssid_macos()
    found: dict[str, PiAP] = {}
    saw_entries = False
    saw_any_ssid = False

    # Structure: SPAirPortDataType -> [ { spairport_airport_interfaces: [ { spairport_airport_other_local_wireless_networks: [ {...} ] } ] } ]
    for root in data.get("SPAirPortDataType", []):
        for iface in root.get("spairport_airport_interfaces", []):
            for key in (
                "spairport_airport_other_local_wireless_networks",
                "spairport_airport_local_wireless_networks",
            ):
                for net in iface.get(key, []) or []:
                    saw_entries = True
                    ssid = net.get("_name")
                    if ssid:
                        saw_any_ssid = True
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

    # Location Services off for the host app: system_profiler still emits
    # network entries but every `_name` is an empty string.
    warning: Optional[str] = None
    if saw_entries and not saw_any_ssid:
        warning = (
            "macOS returned no SSIDs — enable Location Services for this "
            "Terminal in System Settings → Privacy & Security, then rescan."
        )

    return list(found.values()), warning


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


_WIN_SSID_RE = re.compile(r"^SSID\s+\d+\s*:\s*(.*)$")
_WIN_SIGNAL_RE = re.compile(r"^Signal\s*:\s*(\d+)\s*%")


def _scan_windows() -> list[PiAP]:
    """Scan via `netsh wlan show networks mode=bssid`.

    netsh reports signal as a 0–100 percentage, one per BSSID. We keep the
    strongest BSSID per SSID and convert percent → approximate dBm with the
    common linear map (dBm = pct/2 - 100): good enough for ranking, not a
    true measurement.
    """
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("netsh scan failed: %s", exc)
        return []
    if result.returncode != 0:
        log.warning("netsh scan returncode %d: %s",
                    result.returncode, result.stderr.strip()[:200])
        return []

    current = _current_ssid_windows()
    aps: dict[str, PiAP] = {}
    cur_ssid: Optional[str] = None
    for raw in result.stdout.splitlines():
        line = raw.strip()
        m = _WIN_SSID_RE.match(line)
        if m:
            ssid = m.group(1).strip()
            cur_ssid = ssid if SSID_PATTERN.match(ssid) else None
            if cur_ssid and cur_ssid not in aps:
                aps[cur_ssid] = PiAP(
                    ssid=cur_ssid, signal_dbm=None, is_current=(cur_ssid == current),
                )
            continue
        ms = _WIN_SIGNAL_RE.match(line)
        if ms and cur_ssid is not None:
            dbm = int(ms.group(1)) // 2 - 100
            existing = aps[cur_ssid]
            if existing.signal_dbm is None or dbm > existing.signal_dbm:
                aps[cur_ssid] = PiAP(
                    ssid=cur_ssid, signal_dbm=dbm, is_current=existing.is_current,
                )
    return list(aps.values())


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def join_ap(
    ssid: str,
    password: str = DEFAULT_AP_PASSWORD,
    verify_host: str = AP_GATEWAY,
    verify_port: int = AP_PORT,
    status_cb: Optional[Callable[[str], None]] = None,
) -> JoinResult:
    """Drive the laptop onto <ssid> and verify the server is reachable.

    Chain: networksetup/nmcli associate → wait for TCP :verify_port on
    verify_host. We validate by reachability rather than by SSID name
    because `networksetup -getairportnetwork` on recent macOS lies — it
    reports "not associated" even when the Mac genuinely has an IP on
    the new network. A successful TCP connect to 10.42.0.1:5555 proves
    the whole chain worked end to end.

    `status_cb` receives short progress strings so the UI can show what
    step is currently running.
    """
    if not SSID_PATTERN.match(ssid):
        return JoinResult(False, ssid, None, f"refusing to join non-fleet SSID {ssid!r}")

    def _say(msg: str) -> None:
        if status_cb is not None:
            try:
                status_cb(msg)
            except Exception:
                pass

    _say(f"associating to {ssid}…")
    if _is_macos():
        err = _join_macos(ssid, password)
    elif _is_linux():
        err = _join_linux(ssid, password)
    elif _is_windows():
        err = _join_windows(ssid, password)
    else:
        return JoinResult(False, ssid, None, f"unsupported platform {sys.platform}")

    if err:
        return JoinResult(False, ssid, current_ssid(),
                          f"associate: {err}")

    _say(f"contacting {verify_host}:{verify_port}…")
    if not _wait_for_tcp(verify_host, verify_port, GATEWAY_POLL_DEADLINE):
        return JoinResult(
            False, ssid, current_ssid(),
            f"associated but no server at {verify_host}:{verify_port}",
        )

    return JoinResult(True, ssid, ssid)


def _wait_for_tcp(host: str, port: int, timeout_s: float) -> bool:
    """Poll a TCP endpoint until it accepts a connect or timeout elapses.
    Each probe uses a short connect timeout so we don't block the full
    deadline on a single attempt."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            pass
        time.sleep(GATEWAY_POLL_INTERVAL)
    return False


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


_WINDOWS_PROFILE_TEMPLATE = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM><security>
    <authEncryption>
      <authentication>{auth}</authentication>
      <encryption>AES</encryption>
      <useOneX>false</useOneX>
    </authEncryption>
    <sharedKey>
      <keyType>passPhrase</keyType>
      <protected>false</protected>
      <keyMaterial>{password}</keyMaterial>
    </sharedKey>
  </security></MSM>
</WLANProfile>
"""


def _windows_ap_auth(ssid: str) -> str:
    """Return the netsh profile <authentication> keyword matching what <ssid>
    actually advertises, read from a live scan.

    The Pi APs are created with NetworkManager `key-mgmt=wpa-psk` and no
    explicit proto, which Windows sees as "WPA-Personal" (WPA1) — so a
    hardcoded WPA2PSK profile fails to associate (no DHCP lease, APIPA
    address). We map the advertised auth instead. Defaults to WPA2PSK if the
    SSID isn't found in the scan.
    """
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError):
        return "WPA2PSK"
    cur: Optional[str] = None
    for raw in result.stdout.splitlines():
        line = raw.strip()
        m = _WIN_SSID_RE.match(line)
        if m:
            cur = m.group(1).strip()
            continue
        if cur == ssid and line.startswith("Authentication"):
            val = line.split(":", 1)[1].strip().lower()
            if "wpa3" in val:
                return "WPA3SAE"
            if "wpa2" in val:
                return "WPA2PSK"
            if "wpa" in val:
                return "WPAPSK"
            break
    return "WPA2PSK"


def _join_windows(ssid: str, password: str) -> Optional[str]:
    """Associate to <ssid> via netsh.

    netsh can only `connect` to a network that already has a saved WLAN
    profile, so we synthesize a PSK profile XML and add it (idempotent —
    re-adding overwrites) before connecting. The profile's authentication
    type is matched to what the AP advertises (see _windows_ap_auth), since a
    mismatch silently fails to associate. `netsh wlan connect` returns as soon
    as the request is queued, not when association completes — join_ap()'s TCP
    probe of 10.42.0.1:5555 is what actually confirms we're on.
    """
    iface = _wifi_interface_windows()
    if not iface:
        return "no WiFi interface found"

    auth = _windows_ap_auth(ssid)
    path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".xml", delete=False, encoding="utf-8",
        ) as f:
            f.write(_WINDOWS_PROFILE_TEMPLATE.format(
                ssid=ssid, password=password, auth=auth))
            path = f.name
        add = subprocess.run(
            ["netsh", "wlan", "add", "profile",
             f"filename={path}", f"interface={iface}", "user=current"],
            capture_output=True, text=True, timeout=JOIN_TIMEOUT,
        )
        if add.returncode != 0:
            out = (add.stderr or add.stdout)
            # A pre-existing all-user (or group-policy) profile with the same
            # name blocks a current-user add ("already exists in group policy
            # or different user scope"). Delete the stale profile and retry —
            # deleting an all-user profile works without admin; a group-policy
            # profile can't be removed and the retry fails with a clear msg.
            if "already exists" in out.lower():
                subprocess.run(
                    ["netsh", "wlan", "delete", "profile",
                     f"name={ssid}", f"interface={iface}"],
                    capture_output=True, text=True, timeout=JOIN_TIMEOUT,
                )
                add = subprocess.run(
                    ["netsh", "wlan", "add", "profile",
                     f"filename={path}", f"interface={iface}", "user=current"],
                    capture_output=True, text=True, timeout=JOIN_TIMEOUT,
                )
            if add.returncode != 0:
                return (add.stderr or add.stdout).strip() or "netsh add profile failed"
    except subprocess.TimeoutExpired:
        return "netsh add profile timed out"
    except OSError as exc:
        return f"netsh add profile unavailable: {exc}"
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    try:
        conn = subprocess.run(
            ["netsh", "wlan", "connect",
             f"name={ssid}", f"ssid={ssid}", f"interface={iface}"],
            capture_output=True, text=True, timeout=JOIN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "netsh connect timed out"
    except OSError as exc:
        return f"netsh connect unavailable: {exc}"
    if conn.returncode != 0:
        return (conn.stderr or conn.stdout).strip() or "netsh connect failed"
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


# ---------------------------------------------------------------------------
# WiFi interface detection (Windows)
# ---------------------------------------------------------------------------

_wifi_iface_cache_win: Optional[str] = None


def _wifi_interface_windows() -> Optional[str]:
    """Find the wireless interface name netsh uses (e.g. "Wi-Fi", "Wi-Fi 2").

    netsh's `interface=` argument wants the adapter's connection name, which
    is the "Name :" field in `netsh wlan show interfaces`. Returns None if no
    wireless interface is present.
    """
    global _wifi_iface_cache_win
    if _wifi_iface_cache_win:
        return _wifi_iface_cache_win
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    for line in result.stdout.splitlines():
        s = line.strip()
        if s.startswith("Name") and ":" in s:
            _wifi_iface_cache_win = s.split(":", 1)[1].strip()
            return _wifi_iface_cache_win
    return None
