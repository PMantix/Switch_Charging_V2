"""
Switching Circuit V2 - Schedule Monitor.

Passive observer of cycler progress. Walks a loaded schedule on its
own time-based clock (PLAN) and reports what the cycler is actually
doing right now according to CyclerDetector (OBSERVED). Does NOT
control any modes — purely for verification that the cycler is
following the expected program.

Usage:
    monitor = ScheduleMonitor(get_sensor_data_fn=...)
    monitor.start_thread()              # one-time, on construction
    monitor.load_schedule(schedule)     # auto-starts the PLAN clock
    status = monitor.get_status()       # called from broadcast loop
    monitor.restart()                   # reset PLAN clock to step 0
"""

from __future__ import annotations

import logging
import threading
from time import monotonic, sleep
from typing import Callable, Optional

from server.config import AUTO_FOLLOW_LOOP_HZ
from server.cycler_detector import CyclerDetector
from server.schedule import Schedule

log = logging.getLogger(__name__)


class ScheduleMonitor:
    """Passive PLAN-vs-OBSERVED tracker for an Arbin-style schedule."""

    def __init__(
        self,
        get_sensor_data_fn: Callable[[], dict],
        loop_hz: float = AUTO_FOLLOW_LOOP_HZ,
    ):
        self._get_sensor_data = get_sensor_data_fn
        self._detector = CyclerDetector()
        self._lock = threading.Lock()

        self._schedule: Optional[Schedule] = None
        self._running = False
        self._start_time: Optional[float] = None  # monotonic seconds when PLAN clock started

        self._latest_avg_i = 0.0
        self._latest_avg_v = 0.0
        self._latest_state = "unknown"
        self._latest_confidence = 0.0

        self._loop_period = 1.0 / float(loop_hz)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # -- thread lifecycle ----------------------------------------------------

    def start_thread(self):
        """Spin up the polling thread. Always runs (regardless of whether
        a schedule is loaded) so OBSERVED is current the moment the
        monitor is opened in the TUI."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="ScheduleMonitor", daemon=True,
        )
        self._thread.start()
        log.info("ScheduleMonitor: thread started")

    def stop_thread(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        log.info("ScheduleMonitor: thread stopped")

    # -- schedule lifecycle --------------------------------------------------

    def load_schedule(self, schedule: Schedule):
        """Load (or replace) the schedule. Auto-starts the PLAN clock."""
        with self._lock:
            self._schedule = schedule
            self._running = True
            self._start_time = monotonic()
        log.info("ScheduleMonitor: loaded schedule %r and started PLAN clock",
                 schedule.name)

    def restart(self):
        """Reset PLAN clock to step 0 (cycle 0). Schedule must be loaded."""
        with self._lock:
            if self._schedule is None:
                raise ValueError("No schedule loaded")
            self._running = True
            self._start_time = monotonic()
        log.info("ScheduleMonitor: PLAN clock restarted")

    def stop(self):
        """Halt the PLAN clock. Schedule remains loaded; restart() resumes."""
        with self._lock:
            self._running = False
        log.info("ScheduleMonitor: PLAN clock stopped")

    # -- status --------------------------------------------------------------

    def get_status(self) -> dict:
        with self._lock:
            if self._schedule is None:
                return {
                    "loaded": False,
                    "running": False,
                    "observed": self._observed_dict_locked(),
                }
            plan = self._compute_plan_locked()
            observed = self._observed_dict_locked()
            divergence = self._compute_divergence(plan["expected_state"], observed["state"])
            return {
                "loaded": True,
                "running": self._running,
                "schedule_name": self._schedule.name,
                "total_cycles": self._schedule.repeat,
                "total_steps": len(self._schedule.steps),
                "plan": plan,
                "observed": observed,
                "divergence": divergence,
            }

    def _observed_dict_locked(self) -> dict:
        return {
            "state": self._latest_state,
            "confidence": round(self._latest_confidence, 2),
            "current_a": round(self._latest_avg_i, 6),
            "voltage_v": round(self._latest_avg_v, 4),
        }

    def _compute_plan_locked(self) -> dict:
        """Compute where the schedule pointer would be right now if the
        cycler were following the loaded program perfectly. Caller holds
        the lock."""
        steps = self._schedule.steps
        if not steps:
            return self._empty_plan()

        if not self._running or self._start_time is None:
            s = steps[0]
            return {
                "cycle": 0,
                "step_index": 0,
                "step_name": s.name,
                "expected_state": s.expected_state,
                "circuit_action": s.circuit_action,
                "step_elapsed_s": 0.0,
                "step_timeout_s": s.timeout_s,
                "schedule_complete": False,
            }

        elapsed = monotonic() - self._start_time
        cycle_duration = sum(s.timeout_s for s in steps)
        if cycle_duration <= 0:
            return self._empty_plan()
        total_duration = cycle_duration * self._schedule.repeat

        if elapsed >= total_duration:
            last = steps[-1]
            return {
                "cycle": self._schedule.repeat - 1,
                "step_index": len(steps) - 1,
                "step_name": last.name,
                "expected_state": last.expected_state,
                "circuit_action": last.circuit_action,
                "step_elapsed_s": last.timeout_s,
                "step_timeout_s": last.timeout_s,
                "schedule_complete": True,
            }

        cycle_idx = int(elapsed // cycle_duration)
        cycle_elapsed = elapsed - cycle_idx * cycle_duration

        cum = 0.0
        for step_idx, step in enumerate(steps):
            if cycle_elapsed < cum + step.timeout_s:
                return {
                    "cycle": cycle_idx,
                    "step_index": step_idx,
                    "step_name": step.name,
                    "expected_state": step.expected_state,
                    "circuit_action": step.circuit_action,
                    "step_elapsed_s": round(cycle_elapsed - cum, 1),
                    "step_timeout_s": step.timeout_s,
                    "schedule_complete": False,
                }
            cum += step.timeout_s

        # Fallthrough — should not happen, but be defensive
        last = steps[-1]
        return {
            "cycle": cycle_idx,
            "step_index": len(steps) - 1,
            "step_name": last.name,
            "expected_state": last.expected_state,
            "circuit_action": last.circuit_action,
            "step_elapsed_s": last.timeout_s,
            "step_timeout_s": last.timeout_s,
            "schedule_complete": False,
        }

    def _empty_plan(self) -> dict:
        return {
            "cycle": 0,
            "step_index": 0,
            "step_name": "",
            "expected_state": "",
            "circuit_action": "",
            "step_elapsed_s": 0.0,
            "step_timeout_s": 0.0,
            "schedule_complete": False,
        }

    @staticmethod
    def _compute_divergence(expected: str, observed: str) -> str:
        if not expected or observed in ("", "unknown"):
            return "unknown"
        return "match" if expected == observed else "mismatch"

    # -- main loop -----------------------------------------------------------

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("ScheduleMonitor: tick failed")
            sleep(self._loop_period)

    def _tick(self):
        sensor_data = self._get_sensor_data()
        if not sensor_data:
            return
        result = self._detector.feed(sensor_data)
        with self._lock:
            self._latest_avg_i = result.avg_current
            self._latest_avg_v = result.avg_voltage
            self._latest_state = result.state.value
            self._latest_confidence = result.confidence
