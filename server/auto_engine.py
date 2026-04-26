"""
Switching Circuit V2 - Auto Mode Engine.

Daemon thread that runs a loaded schedule, applies circuit actions at each
step, and uses the CyclerDetector to confirm and sync with the external
Arbin cycler.  The detector estimates cycler current via KCL on the HV
bus (sum of P-side shunts vs. sum of N-side shunts), so sensing is
continuous and never interrupts the active switching pattern.
"""

from __future__ import annotations

import enum
import logging
import threading
from collections import deque
from datetime import datetime
from time import monotonic, sleep
from typing import Callable, Optional

from server.auto_logger import AutoLogger
from server.cycler_detector import CyclerDetector, CyclerState, DetectionResult
from server.schedule import Schedule

log = logging.getLogger(__name__)


class StepPhase(enum.Enum):
    ENTERING = "entering"
    ACTIVE = "active"
    TRANSITIONING = "transitioning"


class AutoEngine:
    """
    Runs a cycler schedule, applying circuit actions and detecting
    the external cycler state via INA226 sensors.

    The engine is a daemon thread.  It calls back into the mode controller
    via the provided set_mode_fn and set_sequence_fn callables (to avoid
    circular dependency / the engine stopping itself).
    """

    def __init__(
        self,
        schedule: Schedule,
        get_sensor_data_fn: Callable[[], dict],
        set_mode_fn: Callable[[str], None],
        set_sequence_fn: Callable[[int], None],
        set_frequency_fn: Callable[[float], None],
        on_event: Optional[Callable[[dict], None]] = None,
    ):
        self._schedule = schedule
        self._get_sensor_data = get_sensor_data_fn
        self._set_mode = set_mode_fn
        self._set_sequence = set_sequence_fn
        self._set_frequency = set_frequency_fn
        self._on_event = on_event or (lambda e: None)

        self._detector = CyclerDetector(schedule.detection_thresholds)
        self._logger = AutoLogger()

        # State
        self._cycle = 0
        self._step_index = 0
        self._step_phase = StepPhase.ENTERING
        self._step_start_time = 0.0
        self._last_heartbeat_time = 0.0

        # Heartbeat interval (seconds)
        self._heartbeat_interval = 300.0  # 5 minutes

        # Recent event ring buffer for TUI display
        self._recent_events: deque = deque(maxlen=10)

        # Timeout tracking (set per-step in _run_step)
        self._in_timeout = False

        # Thread control
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # starts unpaused
        self._lock = threading.Lock()
        self._running = False

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        """Start the auto engine thread."""
        if self._running:
            return
        self._stop_event.clear()
        self._pause_event.set()
        self._running = True
        self._cycle = 0
        self._step_index = 0
        self._step_phase = StepPhase.ENTERING
        self._last_heartbeat_time = monotonic()
        self._detector.reset()
        self._logger.start(self._schedule.name)

        self._thread = threading.Thread(
            target=self._run, name="AutoEngine", daemon=True,
        )
        self._thread.start()
        log.info("AutoEngine started: schedule=%r, %d steps x %d cycles",
                 self._schedule.name, len(self._schedule.steps), self._schedule.repeat)
        self._emit_event("auto_started", {
            "schedule": self._schedule.name,
            "total_cycles": self._schedule.repeat,
        })

    def stop(self):
        """Stop the auto engine and go idle."""
        if not self._running:
            return
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused
        if self._thread:
            self._thread.join(timeout=5.0)
        self._running = False
        self._set_mode("idle")
        log.info("AutoEngine stopped")
        self._emit_event("auto_stopped", {
            "cycle": self._cycle,
            "step": self._step_index,
        })
        self._logger.stop()

    def pause(self):
        """Pause the auto engine (holds current circuit state)."""
        self._pause_event.clear()
        log.info("AutoEngine paused at cycle=%d step=%d", self._cycle, self._step_index)
        self._emit_event("auto_paused", {})

    def resume(self):
        """Resume the auto engine."""
        self._pause_event.set()
        log.info("AutoEngine resumed")
        self._emit_event("auto_resumed", {})

    def skip_step(self):
        """Manually advance to the next step."""
        with self._lock:
            self._advance_step()
        log.info("AutoEngine: manually skipped to step %d", self._step_index)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return not self._pause_event.is_set()

    # -- status --------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current auto engine status for broadcasting."""
        with self._lock:
            step = self._current_step()
            detected = self._detector.get_state()
            elapsed = monotonic() - self._step_start_time if self._step_start_time else 0.0

            step_list = [
                {"name": s.name, "expected_state": s.expected_state,
                 "action": s.circuit_action, "timeout_s": s.timeout_s}
                for s in self._schedule.steps
            ]

            return {
                "running": self._running,
                "paused": self.paused,
                "schedule_name": self._schedule.name,
                "cycle": self._cycle,
                "total_cycles": self._schedule.repeat,
                "step_index": self._step_index,
                "total_steps": len(self._schedule.steps),
                "step_name": step.name if step else "",
                "step_elapsed_s": round(elapsed, 1),
                "step_timeout_s": step.timeout_s if step else 0,
                "step_phase": self._step_phase.value,
                "expected_state": step.expected_state if step else "",
                "detected_state": detected.state.value,
                "detected_confidence": round(detected.confidence, 2),
                "detected_current_ma": round(detected.avg_current * 1000, 3),
                "detected_voltage_v": round(detected.avg_voltage, 4),
                "match": (detected.state.value == step.expected_state) if step else False,
                "circuit_action": step.circuit_action if step else "",
                "on_timeout": step.on_timeout if step else "wait",
                "timeout_grace_s": step.effective_grace() if step else 0,
                "in_timeout": self._in_timeout,
                "steps": step_list,
                "recent_events": list(self._recent_events),
            }

    # -- main loop -----------------------------------------------------------

    def _run(self):
        """Main engine loop."""
        try:
            for cycle in range(self._schedule.repeat):
                if self._stop_event.is_set():
                    break
                self._cycle = cycle
                log.info("AutoEngine: starting cycle %d/%d", cycle + 1, self._schedule.repeat)
                self._emit_event("cycle_start", {"cycle": cycle})

                for step_idx in range(len(self._schedule.steps)):
                    if self._stop_event.is_set():
                        break
                    with self._lock:
                        self._step_index = step_idx
                        self._step_phase = StepPhase.ENTERING
                    self._run_step(step_idx)

                if not self._stop_event.is_set():
                    self._emit_event("cycle_complete", {"cycle": cycle})

            if not self._stop_event.is_set():
                log.info("AutoEngine: schedule complete (%d cycles)", self._schedule.repeat)
                self._emit_event("schedule_complete", {})
                self._set_mode("idle")
                self._running = False

        except Exception:
            log.exception("AutoEngine: fatal error in run loop")
            self._set_mode("idle")
            self._running = False

    def _run_step(self, step_idx: int):
        """Execute a single schedule step."""
        step = self._schedule.steps[step_idx]
        log.info("AutoEngine: step %d/%d — %s (expect=%s, action=%s)",
                 step_idx + 1, len(self._schedule.steps),
                 step.name, step.expected_state, step.circuit_action)
        self._emit_event("step_start", {
            "step_index": step_idx,
            "step_name": step.name,
            "expected_state": step.expected_state,
            "circuit_action": step.circuit_action,
        })

        # --- ENTERING phase ---
        with self._lock:
            self._step_phase = StepPhase.ENTERING
            self._step_start_time = monotonic()

        if step.circuit_action == "charge":
            self._set_sequence(step.sequence)
            self._set_frequency(step.frequency)
            self._set_mode("charge")
        else:
            self._apply_circuit_action(step.circuit_action)

        # --- ACTIVE phase ---
        with self._lock:
            self._step_phase = StepPhase.ACTIVE
            self._in_timeout = False

        timeout_logged = False
        grace_deadline = 0.0
        last_timeout_log = 0.0
        TIMEOUT_LOG_INTERVAL = 30.0

        while not self._stop_event.is_set():
            # Respect pause
            if not self._pause_event.wait(timeout=0.5):
                continue

            now = monotonic()
            elapsed = now - self._step_start_time

            # Feed sensor data to detector
            sensor_data = self._get_sensor_data()
            if sensor_data:
                result = self._detector.feed(sensor_data)
            else:
                sleep(0.066)
                continue

            # Heartbeat
            if now - self._last_heartbeat_time >= self._heartbeat_interval:
                self._last_heartbeat_time = now
                self._emit_event("heartbeat", {
                    "cycle": self._cycle,
                    "step_index": self._step_index,
                    "step_name": step.name,
                    "elapsed_s": round(elapsed, 1),
                    "detected_state": result.state.value,
                    "match": result.state.value == step.expected_state,
                })

            # Check for state transition to next step
            next_step = self._peek_next_step()
            if next_step and result.state.value == next_step.expected_state:
                log.info("AutoEngine: detected transition to %s (next step: %s)",
                         result.state.value, next_step.name)
                self._emit_event("step_transition_detected", {
                    "from_step": step.name,
                    "to_step": next_step.name,
                    "detected_state": result.state.value,
                    "elapsed_s": round(elapsed, 1),
                })
                self._in_timeout = False
                break

            # --- Timeout handling ---
            if elapsed > step.timeout_s:
                self._in_timeout = True

                if not timeout_logged:
                    timeout_logged = True
                    grace = step.effective_grace()
                    grace_deadline = step.timeout_s + grace
                    last_timeout_log = now
                    log.warning("AutoEngine: step %r exceeded timeout (%.0fs), "
                                "grace=%.0fs, on_timeout=%s",
                                step.name, step.timeout_s, grace, step.on_timeout)
                    self._emit_event("step_timeout", {
                        "step_name": step.name,
                        "timeout_s": step.timeout_s,
                        "grace_s": grace,
                        "on_timeout": step.on_timeout,
                        "elapsed_s": round(elapsed, 1),
                    })

                # Periodic timeout logging
                if now - last_timeout_log >= TIMEOUT_LOG_INTERVAL:
                    last_timeout_log = now
                    log.warning("AutoEngine: step %r still waiting (elapsed=%.0fs)",
                                step.name, elapsed)
                    self._emit_event("step_timeout_waiting", {
                        "step_name": step.name,
                        "elapsed_s": round(elapsed, 1),
                    })

                # Grace period exhausted — apply on_timeout behaviour
                if grace_deadline > 0 and elapsed > grace_deadline:
                    if step.on_timeout == "advance":
                        log.warning("AutoEngine: step %r grace exhausted, advancing",
                                    step.name)
                        self._emit_event("step_timeout_advance", {
                            "step_name": step.name,
                            "elapsed_s": round(elapsed, 1),
                        })
                        break
                    elif step.on_timeout == "abort":
                        log.warning("AutoEngine: step %r grace exhausted, aborting",
                                    step.name)
                        self._emit_event("step_timeout_abort", {
                            "step_name": step.name,
                            "elapsed_s": round(elapsed, 1),
                        })
                        self._stop_event.set()
                        break
                    # "wait" — continue looping, periodic logs keep firing

            sleep(0.066)  # ~15 Hz loop rate

        self._in_timeout = False
        self._emit_event("step_complete", {
            "step_index": step_idx,
            "step_name": step.name,
            "elapsed_s": round(monotonic() - self._step_start_time, 1),
        })

    def _apply_circuit_action(self, action: str):
        """Apply a circuit action string to the mode controller."""
        if action == "idle":
            self._set_mode("idle")
        elif action == "discharge":
            self._set_mode("discharge")
        elif action == "pulse_charge":
            self._set_mode("pulse_charge")
        elif action == "charge":
            self._set_mode("charge")

    # -- step navigation -----------------------------------------------------

    def _current_step(self):
        if 0 <= self._step_index < len(self._schedule.steps):
            return self._schedule.steps[self._step_index]
        return None

    def _peek_next_step(self):
        """Return the next step in the schedule (or first step of next cycle)."""
        next_idx = self._step_index + 1
        if next_idx < len(self._schedule.steps):
            return self._schedule.steps[next_idx]
        # Wrap to first step of next cycle
        if self._cycle + 1 < self._schedule.repeat:
            return self._schedule.steps[0]
        return None  # end of schedule

    def _advance_step(self):
        """Advance to the next step (called under lock)."""
        self._step_index += 1
        if self._step_index >= len(self._schedule.steps):
            self._step_index = 0
            self._cycle += 1
        self._step_phase = StepPhase.ENTERING
        self._step_start_time = monotonic()

    # -- events --------------------------------------------------------------

    def _emit_event(self, event_type: str, data: dict):
        """Emit an event to the callback, log, and recent event buffer."""
        event = {"event_type": event_type, "timestamp": monotonic(), **data}
        self._logger.write_event(event_type, data)

        # Compact display string for TUI
        ts = datetime.now().strftime("%H:%M:%S")
        summary = self._format_event(event_type, data)
        self._recent_events.append(f"{ts} {summary}")

        try:
            self._on_event(event)
        except Exception:
            log.exception("AutoEngine: error in event callback")

    @staticmethod
    def _format_event(event_type: str, data: dict) -> str:
        """Format an event into a compact display string for TUI."""
        name = data.get("step_name", data.get("from_step", ""))
        if event_type == "step_start":
            return f">> Step: {name} (expect={data.get('expected_state', '?')})"
        elif event_type == "step_transition_detected":
            return f"<> {data.get('from_step', '?')} \u2192 {data.get('to_step', '?')}"
        elif event_type == "step_timeout":
            return f"!! Timeout: {name} (on_timeout={data.get('on_timeout', '?')})"
        elif event_type == "step_timeout_waiting":
            return f"!! Waiting: {name} ({data.get('elapsed_s', 0):.0f}s)"
        elif event_type == "step_timeout_advance":
            return f">> Auto-advance: {name}"
        elif event_type == "step_timeout_abort":
            return f"XX Abort: {name}"
        elif event_type == "step_complete":
            return f"OK Done: {name} ({data.get('elapsed_s', 0):.0f}s)"
        elif event_type == "cycle_start":
            return f"-- Cycle {data.get('cycle', 0) + 1} started"
        elif event_type == "cycle_complete":
            return f"-- Cycle {data.get('cycle', 0) + 1} complete"
        elif event_type == "schedule_complete":
            return "== Schedule complete"
        elif event_type == "heartbeat":
            m = "\u2714" if data.get("match") else "\u2718"
            return f".. {name} {m} ({data.get('elapsed_s', 0):.0f}s)"
        elif event_type in ("auto_started", "auto_stopped", "auto_paused", "auto_resumed"):
            return f"-- {event_type.replace('auto_', '').capitalize()}"
        return f"[{event_type}]"
