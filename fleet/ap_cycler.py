"""
Fleet AP cycler — drives WiFi adapter through pi_SW# access points.

Cycles through each Pi's AP, polls state, sends queued commands,
and updates the fleet state store. Runs in a background thread.

Platform support:
  - macOS: networksetup + system_profiler / CoreWLAN
  - Linux: nmcli
  - Windows: netsh (scan via "show networks", join via add profile + connect)

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
        idle_pause: float = 30.0,
        on_cycle_complete: Optional[callable] = None,
    ):
        self._store = store
        self._poll_pause = poll_pause
        self._idle_pause = idle_pause
        self._on_cycle_complete = on_cycle_complete

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._paused.set()

        self._cycle_count = 0
        self._current_pi: Optional[int] = None
        self._current_phase = "idle"

        # Pis whose settings we've already captured this session. Once a Pi
        # is in here we stop joining its AP unless it has a pending command —
        # so the cycler isn't perpetually hopping APs and dropping WiFi. A Pi
        # is dropped from this set when it disappears from scans, so it gets
        # re-polled when it returns.
        self._polled_once: set[int] = set()

        # Set by request_repoll() (web thread) to force the next cycle to
        # re-poll cleared Pis and to wake the idle wait immediately.
        self._repoll_event = threading.Event()

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

    def request_repoll(self, pi_num: Optional[int] = None) -> None:
        """Force a fresh poll of one Pi (pi_num) or all Pis (None) on the next
        cycle, and wake the cycler immediately if it's idling.

        Clearing the Pi(s) from _polled_once is what makes _join_list pick
        them up again; the event just shortcuts the idle wait so the refresh
        feels instant rather than waiting out --idle-pause."""
        if pi_num is None:
            self._polled_once.clear()
        else:
            self._polled_once.discard(pi_num)
        self._repoll_event.set()

    def _cycle_loop(self) -> None:
        while not self._stop_event.is_set():
            self._paused.wait()
            if self._stop_event.is_set():
                break

            # Consume any pending re-poll request as we begin this cycle; a
            # request that arrives mid-cycle re-sets it and triggers the next.
            self._repoll_event.clear()
            self._current_phase = "scanning"
            visible = self._scan_visible()
            self._reconcile_offline(visible)

            visit_order = self._join_list(visible)

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

            # If we actually joined APs this cycle, come back promptly to
            # finish any follow-up; otherwise idle until a command is queued
            # or the rediscovery interval elapses — no needless AP hopping.
            wait = self._poll_pause if visit_order else self._idle_pause
            self._wait_for_work(wait)

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

    def _reconcile_offline(self, visible: dict[int, Optional[int]]) -> None:
        """Mark previously-seen but now-invisible Pis offline, and forget that
        we polled them so they're re-captured when they reappear."""
        for pi_num in self._store.known_pi_nums():
            if pi_num not in visible:
                self._store.mark_offline(pi_num)
                self._polled_once.discard(pi_num)

    def _join_list(self, visible: dict[int, Optional[int]]) -> list[int]:
        """Which visible Pis actually warrant joining their AP this cycle:

          - any with a pending command (deliver it, then confirm), and
          - any whose settings we haven't captured yet this session.

        Pis we've already polled and that have no pending work are skipped on
        purpose — that's what stops the perpetual AP hopping. Pending-command
        Pis go first so committed changes are delivered ahead of discovery."""
        pending = self._store.pis_with_pending_commands()
        order: list[int] = []
        for pi_num in sorted(visible):
            if pi_num in pending:
                order.append(pi_num)
        for pi_num in sorted(visible):
            if pi_num not in order and pi_num not in self._polled_once:
                order.append(pi_num)
        return order

    def _wait_for_work(self, timeout: float) -> None:
        """Wait up to `timeout` seconds between cycles, but wake early the
        moment a command is queued — so a change committed in the dashboard
        is delivered within a fraction of a second rather than after a full
        idle interval."""
        end = time.monotonic() + timeout
        while not self._stop_event.is_set():
            if self._repoll_event.is_set() or self._store.pis_with_pending_commands():
                return
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            self._stop_event.wait(min(0.5, remaining))

    def _visit_pi(self, pi_num: int, visible: dict[int, Optional[int]]) -> None:
        ssid = _pi_num_to_ssid(pi_num)
        self._current_pi = pi_num
        self._current_phase = f"joining {ssid}"
        log.info("Visiting Pi SW%d (%s)", pi_num, ssid)

        join_result = self._join_ap(ssid)
        if not join_result.ok:
            log.warning("Failed to join %s: %s", ssid, join_result.error)
            self._store.mark_offline(pi_num)
            self._polled_once.discard(pi_num)
            self._current_pi = None
            return

        self._current_phase = f"polling Pi SW{pi_num}"
        state = poll_status()
        if state is None:
            log.warning("Pi SW%d: poll failed after AP join", pi_num)
            self._store.mark_offline(pi_num)
            self._polled_once.discard(pi_num)
            self._current_pi = None
            return

        signal = visible.get(pi_num)
        self._store.update_state(pi_num, state, signal_dbm=signal)
        # Settings captured — don't revisit until a command is queued.
        self._polled_once.add(pi_num)

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
