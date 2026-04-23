"""
Switching Circuit V2 - PWR button → AP-mode toggle.

Listens to the Pi 5's on-board power button (`pwr_button` / KEY_POWER) and
toggles the `pi_SW#` access point on a double-press. Blinks the green ACT
LED as acknowledgment before the radio flip takes the network down.

Runs as root (needs /dev/input/event* and /sys/class/leds/ACT write access).
Grabs the input device exclusively so logind can't also react — pair with
`HandlePowerKey=ignore` in /etc/systemd/logind.conf.d/.

Single press does nothing. The user said they'll pull power to shut down.
"""

import logging
import select
import subprocess
import threading
import time
from pathlib import Path

import evdev

from server.fleet import my_ap_profile
from server.network_mode import set_mode

log = logging.getLogger(__name__)

DEVICE_NAME = "pwr_button"
DOUBLE_PRESS_WINDOW_S = 0.5
ACT_LED = Path("/sys/class/leds/ACT")
NMCLI = "/usr/bin/nmcli"
SYSTEMCTL = "/usr/bin/systemctl"
CLIENT_REASSOCIATE_WAIT_S = 20.0
CLIENT_REASSOCIATE_POLL_S = 1.0


def find_power_button() -> evdev.InputDevice:
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.name == DEVICE_NAME:
            return dev
    raise RuntimeError(f"no evdev device named {DEVICE_NAME!r} found")


def is_ap_active() -> bool:
    profile = my_ap_profile()
    result = subprocess.run(
        [NMCLI, "-t", "-f", "NAME", "connection", "show", "--active"],
        capture_output=True, text=True, timeout=5,
    )
    return profile in result.stdout.splitlines()


def _read_current_trigger() -> str:
    """Parse `/sys/class/leds/ACT/trigger`; the active one is wrapped in [brackets]."""
    raw = (ACT_LED / "trigger").read_text()
    for tok in raw.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return "none"


def blink(times: int, on_ms: int = 120, off_ms: int = 120) -> None:
    """Pulse ACT LED `times` times, then restore its previous trigger."""
    trigger_path = ACT_LED / "trigger"
    brightness_path = ACT_LED / "brightness"
    try:
        prev = _read_current_trigger()
        trigger_path.write_text("none")
        try:
            for _ in range(times):
                brightness_path.write_text("1")
                time.sleep(on_ms / 1000)
                brightness_path.write_text("0")
                time.sleep(off_ms / 1000)
        finally:
            trigger_path.write_text(prev)
    except OSError:
        log.exception("LED blink failed")


def _wait_for_client_profile(timeout_s: float = CLIENT_REASSOCIATE_WAIT_S) -> bool:
    """Poll until wlan0 has an active non-AP client profile, or timeout."""
    from server.ap_fallback import active_client_profile  # lazy: avoid import cycle
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if active_client_profile():
            return True
        time.sleep(CLIENT_REASSOCIATE_POLL_S)
    return False


def _restart_rpi_connect() -> None:
    """Bounce the pi-user rpi-connect service.

    Why: rpi-connectd holds a long-lived cloud session that doesn't reliably
    recover after the AP flip drops its network. Restarting once wlan0 is back
    on a client profile is the standard workaround.
    """
    argv = [SYSTEMCTL, "--machine=pi@.host", "--user", "restart", "rpi-connect"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("rpi-connect restart errored: %s", exc)
        return
    if result.returncode != 0:
        log.warning("rpi-connect restart failed: %s", (result.stderr or "").strip())
    else:
        log.info("rpi-connect restarted")


def _toggle_worker() -> None:
    try:
        if is_ap_active():
            log.info("double-press: leaving AP mode")
            blink(3)
            result = set_mode("client")
            if not result.get("ok"):
                return
            if _wait_for_client_profile():
                _restart_rpi_connect()
            else:
                log.warning(
                    "no client profile after %.0fs — skipping rpi-connect restart",
                    CLIENT_REASSOCIATE_WAIT_S,
                )
        else:
            log.info("double-press: entering AP mode")
            blink(2)
            set_mode("ap")
    except Exception:
        log.exception("toggle failed")


def toggle_ap() -> None:
    """Run the toggle on a background thread so the evdev loop keeps draining."""
    threading.Thread(target=_toggle_worker, daemon=True).start()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dev = find_power_button()
    dev.grab()
    log.info("listening on %s (%s)", dev.path, dev.name)

    last_press = 0.0
    while True:
        r, _, _ = select.select([dev.fd], [], [])
        for event in dev.read():
            if event.type != evdev.ecodes.EV_KEY:
                continue
            if event.code != evdev.ecodes.KEY_POWER:
                continue
            if event.value != 1:  # keydown only
                continue
            now = time.monotonic()
            if now - last_press <= DOUBLE_PRESS_WINDOW_S:
                toggle_ap()
                last_press = 0.0
            else:
                last_press = now


if __name__ == "__main__":
    run()
