"""
Switching Circuit V2 - Fleet network watchdog.

Manages WiFi so multiple Pis share one subnet automatically:

  Boot:  see a pi_SW# AP? join it.  No AP? start your own.
  Watch: if the AP you joined drops, check if you're the lowest-
         numbered survivor.  If yes, start your own AP.  If no,
         wait for the lower-numbered Pi to start one, then join.

Runs as a long-lived systemd service (Type=simple).
"""

import logging
import subprocess
import sys
import time
from typing import Optional

from server.fleet import (
    AP_GATEWAY,
    AP_PASSWORD,
    FLEET_SSID_RANGE,
    SSID_PREFIX,
    my_ap_profile,
    my_ap_ssid,
    my_fleet_index,
)
from server.network_mode import set_mode
from server.power_button import blink

log = logging.getLogger("ap_fallback")

NMCLI = "/usr/bin/nmcli"
SCAN_RETRIES = 3
SCAN_DELAY = 2.0
POLL_INTERVAL = 10.0
PING_TIMEOUT = 3
PING_FAILS_BEFORE_ACTION = 3
FAILOVER_WAIT = 20.0


def active_client_profile() -> Optional[str]:
    """Name of the wlan0 client profile that is currently active, if any."""
    try:
        result = subprocess.run(
            [NMCLI, "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("nmcli query failed: %s", exc)
        return None
    if result.returncode != 0:
        return None
    ap_profile = my_ap_profile()
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        name, device, conn_type = parts[0], parts[1], parts[2]
        if device == "wlan0" and conn_type.startswith("802-11") and name != ap_profile:
            return name
    return None


def _ssid_index(ssid: str) -> int:
    try:
        return int(ssid.replace(SSID_PREFIX, ""))
    except ValueError:
        return 99


def current_mode() -> str:
    """'ap' if our own AP profile is active on wlan0, else 'client'."""
    try:
        result = subprocess.run(
            [NMCLI, "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"
    ap_profile = my_ap_profile()
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[0] == ap_profile and parts[1] == "wlan0":
            return "ap"
    return "client"


def scan_fleet_aps() -> list[str]:
    """Scan WiFi and return visible pi_SW# SSIDs (excluding our own)."""
    own_ssid = my_ap_ssid()
    for attempt in range(SCAN_RETRIES):
        subprocess.run(
            [NMCLI, "device", "wifi", "rescan"], capture_output=True, timeout=10,
        )
        time.sleep(SCAN_DELAY)
        result = subprocess.run(
            [NMCLI, "-t", "-f", "SSID", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            continue
        visible = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        fleet_aps = sorted(
            [ssid for ssid in FLEET_SSID_RANGE if ssid in visible and ssid != own_ssid],
            key=_ssid_index,
        )
        if fleet_aps:
            log.info("scan %d/%d: found fleet APs: %s", attempt + 1, SCAN_RETRIES, fleet_aps)
            return fleet_aps
        log.info("scan %d/%d: no fleet APs visible", attempt + 1, SCAN_RETRIES)
    return []


def join_fleet_ap(ssid: str) -> bool:
    """Connect to an existing fleet AP as a WiFi client."""
    profile = f"fleet-{ssid}"
    subprocess.run(
        [NMCLI, "connection", "delete", profile],
        capture_output=True, timeout=10,
    )
    own = my_ap_profile()
    subprocess.run(
        [NMCLI, "connection", "down", own],
        capture_output=True, timeout=10,
    )
    add_result = subprocess.run(
        [NMCLI, "connection", "add",
         "type", "wifi", "ifname", "wlan0", "con-name", profile,
         "ssid", ssid,
         "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", AP_PASSWORD],
        capture_output=True, text=True, timeout=10,
    )
    if add_result.returncode != 0:
        log.warning("failed to create profile for %s: %s", ssid, add_result.stderr.strip())
        return False
    result = subprocess.run(
        [NMCLI, "connection", "up", profile],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode == 0:
        log.info("joined fleet AP %s", ssid)
        return True
    log.warning("failed to join %s: %s", ssid, result.stderr.strip())
    subprocess.run([NMCLI, "connection", "delete", profile], capture_output=True, timeout=10)
    return False


def _start_own_ap():
    log.info("starting own AP")
    blink(2)
    result = set_mode("ap")
    if not result.get("ok"):
        log.error("AP activation failed: %s", result.get("error") or result.get("stderr"))


def _ping_gateway() -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(PING_TIMEOUT), AP_GATEWAY],
        capture_output=True, timeout=PING_TIMEOUT + 2,
    )
    return result.returncode == 0


def boot():
    """Join any existing fleet AP, or start own."""
    fleet_aps = scan_fleet_aps()
    if fleet_aps:
        for ssid in fleet_aps:
            log.info("boot: joining fleet AP %s", ssid)
            blink(3)
            if join_fleet_ap(ssid):
                return
        log.warning("could not join any fleet AP, falling back to own AP")

    log.info("boot: no fleet AP found, starting own")
    _start_own_ap()


def watch_loop():
    """Monitor connectivity; handle AP failover."""
    my_idx = my_fleet_index()
    consecutive_ping_fails = 0

    while True:
        time.sleep(POLL_INTERVAL)
        mode = current_mode()

        if mode == "ap":
            consecutive_ping_fails = 0
            continue

        # --- client mode ---
        if _ping_gateway():
            consecutive_ping_fails = 0
            continue

        consecutive_ping_fails += 1
        log.warning("gateway unreachable (%d/%d)",
                     consecutive_ping_fails, PING_FAILS_BEFORE_ACTION)

        if consecutive_ping_fails < PING_FAILS_BEFORE_ACTION:
            continue

        log.info("AP lost — scanning for survivors")
        consecutive_ping_fails = 0

        fleet_aps = scan_fleet_aps()
        lower_aps = [s for s in fleet_aps if _ssid_index(s) < my_idx]

        if lower_aps:
            log.info("lower-numbered AP found: %s — joining", lower_aps[0])
            for ssid in lower_aps:
                if join_fleet_ap(ssid):
                    blink(3)
                    break
            continue

        # Wait for a lower-numbered Pi to maybe come back
        log.info("waiting %.0fs for a lower-numbered Pi to start AP", FAILOVER_WAIT)
        time.sleep(FAILOVER_WAIT)
        fleet_aps = scan_fleet_aps()
        lower_aps = [s for s in fleet_aps if _ssid_index(s) < my_idx]
        if lower_aps:
            log.info("lower AP came back: %s — joining", lower_aps[0])
            for ssid in lower_aps:
                if join_fleet_ap(ssid):
                    blink(3)
                    break
            continue

        log.info("no lower-numbered AP available — promoting self")
        _start_own_ap()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    boot()
    watch_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
