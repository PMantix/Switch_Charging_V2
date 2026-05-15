"""
Switching Circuit V2 - Boot-time AP activation.

On boot, start this Pi's own pi_SW# access point unconditionally so
the operator can connect directly from the MacBook. Each Pi runs its
own AP — the TUI switches between them by changing the Mac's WiFi.

Runs as a systemd oneshot after NetworkManager.service.
"""

import logging
import sys
from typing import Optional

from server.fleet import my_ap_profile
from server.network_mode import set_mode
from server.power_button import blink

log = logging.getLogger("ap_fallback")

NMCLI = "/usr/bin/nmcli"


def active_client_profile() -> Optional[str]:
    """Name of the wlan0 client profile that is currently active, if any."""
    import subprocess
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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("boot: entering AP mode")
    blink(2)
    result = set_mode("ap")
    if not result.get("ok"):
        log.error("AP activation failed: %s", result.get("error") or result.get("stderr"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
