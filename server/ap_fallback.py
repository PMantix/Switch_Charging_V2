"""
Switching Circuit V2 - Boot-time AP activation.

On boot, flip this Pi into AP mode unconditionally so the `pi_SW#`
access point is always reachable from the operator's MacBook. To join
a known client WiFi instead, double-press the PWR button after boot
(handled by server.power_button).

Equivalent LED cue to the double-press "enter AP" path: 2x green ACT
flash, same nmcli path via network_mode.set_mode("ap"). Runs as a
systemd oneshot after NetworkManager.service.

`active_client_profile` is still exported from this module because
server.power_button imports it to decide whether to restart
rpi-connect after flipping back to client mode.
"""

import logging
import subprocess
import sys
from typing import Optional

from server.fleet import my_ap_profile
from server.network_mode import set_mode
from server.power_button import blink

log = logging.getLogger("ap_fallback")

NMCLI = "/usr/bin/nmcli"


def active_client_profile() -> Optional[str]:
    """Name of the wlan0 client profile that is currently active, if any.

    The Pi's own AP profile (pi_SW#) is explicitly not considered a
    client connection — if we somehow came up in AP already, we still
    have no route out, so None is the honest answer.
    """
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
