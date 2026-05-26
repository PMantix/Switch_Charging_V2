"""
Fleet AP cycler — drives WiFi adapter through pi_SW# access points.

Cycles through each Pi's AP, polls state, sends queued commands,
and updates the fleet state store. Runs in a background thread.

Platform support:
  - macOS: networksetup + system_profiler / CoreWLAN
  - Linux: nmcli
  - Windows: netsh (scan only — join not yet implemented)

The wifi_scan module handles the platform-specific scanning and
joining; this module orchestrates the cycling loop.
"""

import logging
import re
import threading
import time
from typing import Optional

from fleet.poller import poll_status, send_command
from fleet.state_store import FleetStateStore

log = logging.getLogger(__name__)

SSID_PATTERN = re.compile(r"^pi_SW(\d+)$")


def _ssid_to_pi_num(ssid: str) -> Optional[int]:
    m = SSID_PATTERN.match(ssid)
    return int(m.group(1)) if m else None


def _pi_num_to_ssid(pi_num: int) -> str:
    return f"pi_SW{pi_num}"


class APCycler:
    """Cycles through Pi APs, polling state and dispatching commands."""

    def __init__(
        self,
        store: FleetStateStore,
        poll_pause: float = 1.0,
        on_cycle_complete: Optional[callable] = None,
    ):
        self._store = store
        self._poll_pause = poll_pause
        self._on_cycle_complete = on_cycle_complete

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._paused.set()

        self._cycle_count = 0
        self._current_pi: Optional[int] = None
        self._current_phase = "idle"

        # Import wifi_scan here to keep the import lazy — it runs
        # platform-specific subprocess probes on import.
        from tui.wifi_scan import scan_pi_aps, join_ap, current_ssid
        self._scan_pi_aps = scan_pi_aps
        self._join_ap = join_ap
        self._current_ssid = current_ssid

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def current_pi(self) -> Optional[int]:
        return self._current_pi

    @property
    def current_phase(self) -> str:
        return self._current_phase

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._cycle_loop, daemon=True, name="ap-cycler",
        )
        self._thread.start()
        log.info("AP cycler started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)
        log.info("AP cycler stopped")

    def pause(self) -> None:
        self._paused.clear()
        self._current_phase = "paused"

    def resume(self) -> None:
        self._paused.set()

    def _cycle_loop(self) -> None:
        while not self._stop_event.is_set():
            self._paused.wait()
            if self._stop_event.is_set():
                break

            self._current_phase = "scanning"
            visible = self._scan_visible()

            visit_order = self._prioritized_order(visible)

            for pi_num in visit_order:
                if self._stop_event.is_set():
                    break
                self._paused.wait()
                if self._stop_event.is_set():
                    break
                self._visit_pi(pi_num, visible)

            self._cycle_count += 1
            self._current_phase = "idle"
            if self._on_cycle_complete:
                try:
                    self._on_cycle_complete(self._cycle_count)
                except Exception:
                    pass

            self._stop_event.wait(self._poll_pause)

    def _scan_visible(self) -> dict[int, Optional[int]]:
        """Scan for visible pi_SW# APs. Returns {pi_num: signal_dbm}.
        Also registers newly-discovered Pis in the store."""
        try:
            result = self._scan_pi_aps()
            visible: dict[int, Optional[int]] = {}
            for ap in result.aps:
                pi_num = _ssid_to_pi_num(ap.ssid)
                if pi_num is not None:
                    visible[pi_num] = ap.signal_dbm
                    self._store.ensure_pi(pi_num)
            return visible
        except Exception as exc:
            log.warning("WiFi scan failed: %s", exc)
            return {}

    def _prioritized_order(self, visible: dict[int, Optional[int]]) -> list[int]:
        """Pis with pending commands first, then the rest in numeric order."""
        priority = self._store.pis_with_pending_commands()
        order: list[int] = []
        for pi_num in sorted(priority):
            if pi_num in visible:
                order.append(pi_num)
        for pi_num in sorted(visible):
            if pi_num not in order:
                order.append(pi_num)
        # Mark previously-seen but now-invisible Pis as offline
        for pi_num in self._store.known_pi_nums():
            if pi_num not in visible:
                self._store.mark_offline(pi_num)
        return order

    def _visit_pi(self, pi_num: int, visible: dict[int, Optional[int]]) -> None:
        ssid = _pi_num_to_ssid(pi_num)
        self._current_pi = pi_num
        self._current_phase = f"joining {ssid}"
        log.info("Visiting Pi SW%d (%s)", pi_num, ssid)

        join_result = self._join_ap(ssid)
        if not join_result.ok:
            log.warning("Failed to join %s: %s", ssid, join_result.error)
            self._store.mark_offline(pi_num)
            self._current_pi = None
            return

        self._current_phase = f"polling Pi SW{pi_num}"
        state = poll_status()
        if state is None:
            log.warning("Pi SW%d: poll failed after AP join", pi_num)
            self._store.mark_offline(pi_num)
            self._current_pi = None
            return

        signal = visible.get(pi_num)
        self._store.update_state(pi_num, state, signal_dbm=signal)

        pending = self._store.drain_commands(pi_num)
        for qc in pending:
            self._current_phase = f"sending cmd to Pi SW{pi_num}"
            log.info("Sending command to Pi SW%d: %s", pi_num, qc.cmd)
            result = send_command(qc.cmd)
            ok = result is not None and result.get("ok", False)
            self._store.mark_command_result(qc.id, result or {}, confirmed=ok)

            if ok:
                verify = poll_status()
                if verify is not None:
                    self._store.update_state(pi_num, verify, signal_dbm=signal)

        self._current_pi = None
