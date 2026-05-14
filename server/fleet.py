"""
Switching Circuit V2 - Fleet conventions.

Single source of truth for how a Pi's hostname relates to its access
point SSID, and for the network addresses used when the Pi is in AP
mode. Imported by server/network_mode.py; the TUI side duplicates the
AP gateway constant to avoid a cross-package import.
"""

import socket


# NetworkManager `ipv4.method=shared` hands out 10.42.0.0/24 and makes
# the Pi itself reachable at 10.42.0.1 on any `pi_SW#` AP.
AP_GATEWAY = "10.42.0.1"
AP_PORT = 5555
AP_PASSWORD = "raspberry"

# hostname "pi-SW3"  <->  NM profile/SSID "pi_SW3"
HOSTNAME_PREFIX = "pi-SW"
SSID_PREFIX = "pi_SW"
FLEET_SSID_RANGE = [f"{SSID_PREFIX}{i}" for i in range(1, 9)]


def my_fleet_index() -> int:
    """Numeric index of this Pi in the fleet (e.g. pi-SW3 -> 3)."""
    hostname = socket.gethostname()
    if hostname.startswith(HOSTNAME_PREFIX):
        try:
            return int(hostname[len(HOSTNAME_PREFIX):])
        except ValueError:
            pass
    return 99


def my_ap_ssid() -> str:
    """SSID for this Pi's access point, derived from its hostname."""
    return socket.gethostname().replace("-", "_")


def my_ap_profile() -> str:
    """NetworkManager connection profile name for this Pi's AP.

    Matches the SSID — we created the profile as
    `nmcli connection add ... con-name pi_SW1 ssid pi_SW1`.
    """
    return my_ap_ssid()
