"""
Switching Circuit V2 - Mode Controller.

State machine with three modes: IDLE, CHARGE, DISCHARGE.
Every transition enforces a dead-time interlock (all-off gap) to prevent
shoot-through.  All public methods are thread-safe.
"""

import enum
import logging
import threading
from time import sleep

from server.auto_follow import ALLOWED_TARGET_MODES, AutoFollow
from server.config import DEAD_TIME

log = logging.getLogger(__name__)


class Mode(enum.Enum):
    IDLE = "idle"
    CHARGE = "charge"
    DISCHARGE = "discharge"
    PULSE_CHARGE = "pulse_charge"
    DEBUG = "debug"
    AUTO = "auto"


class ModeController:
    """Coordinates the GPIODriver, SequenceEngine, and system mode."""

    def __init__(self, gpio_driver, sequence_engine):
        self._gpio = gpio_driver
        self._engine = sequence_engine
        self._lock = threading.Lock()
        self._mode = Mode.IDLE
        self._auto_engine = None       # AutoEngine instance when in AUTO mode
        self._loaded_schedule = None   # Schedule loaded but not yet running

        # Auto-follow: hysteresis-based current-driven mode switcher.
        # Started here but disabled by default; user toggles via TUI.
        self._auto_follow = AutoFollow(
            get_sensor_data_fn=self._gpio.get_sensor_data,
            set_mode_fn=lambda m: self._set_mode_internal(Mode(m)),
        )
        self._auto_follow.start()

        # Ensure we start in a safe state
        self._gpio.all_off()
        self._engine.pause()
        log.info("ModeController initialised — mode=IDLE")

    # -- public API ---------------------------------------------------------

    def set_mode(self, mode):
        """
        Transition to a new mode.

        Accepts a Mode enum member or a string ('idle', 'charge', 'discharge').
        Returns the new Mode value.
        Raises ValueError for unknown mode strings.

        Interaction with auto-follow: while auto-follow is enabled,
        picking a switching target (`charge` / `pulse_charge`) updates
        the auto-follow target without forcing the mode (auto-follow's
        hysteresis decides when to engage). Picking any other mode
        (`idle` / `discharge` / `auto` / `debug`) disables auto-follow
        first, then applies the requested mode.
        """
        if isinstance(mode, str):
            try:
                mode = Mode(mode.lower())
            except ValueError:
                raise ValueError(
                    f"Unknown mode {mode!r}. "
                    f"Valid modes: {', '.join(m.value for m in Mode)}"
                )

        # Route through auto-follow when enabled and the target is a
        # switching mode — let the controller's hysteresis decide
        # engagement instead of forcing the transition here.
        if self._auto_follow.enabled and mode.value in ALLOWED_TARGET_MODES:
            self._auto_follow.set_target_mode(mode.value)
            return self.get_mode()

        # Any other mode pick disables auto-follow before proceeding.
        if self._auto_follow.enabled:
            self._auto_follow.set_enabled(False)

        # If leaving AUTO externally, stop the auto engine first
        if self._auto_engine and mode != Mode.AUTO:
            log.info("Stopping auto engine due to external mode change to %s", mode.value)
            self._auto_engine.stop()
            self._auto_engine = None

        if mode == Mode.AUTO:
            return self._start_auto_mode()

        return self._set_mode_internal(mode)

    def _set_mode_internal(self, mode):
        """
        Internal mode transition — used by AutoEngine to change circuit
        state without stopping itself.  When the auto engine is running,
        the reported mode stays AUTO; only the hardware state changes.
        """
        with self._lock:
            auto_active = self._auto_engine and self._auto_engine.running
            if auto_active:
                log.info("Auto circuit action: %s", mode.value)
            else:
                if mode == self._mode:
                    return self._mode
                log.info("Mode transition: %s -> %s", self._mode.value, mode.value)

            # Dead-time interlock: all off -> wait -> new state
            self._engine.pause()
            self._gpio.all_off()
            sleep(DEAD_TIME)

            if mode == Mode.IDLE:
                # Stay all-off, engine paused (already done above)
                pass

            elif mode == Mode.CHARGE:
                self._engine.set_pulse_mode(False)
                self._engine.resume()

            elif mode == Mode.DISCHARGE:
                # All FETs on, engine stays paused
                self._gpio.all_on()

            elif mode == Mode.PULSE_CHARGE:
                self._engine.set_pulse_mode(True)
                self._engine.resume()

            elif mode == Mode.DEBUG:
                # All off, engine stays paused — individual FETs controlled manually
                self._debug_step = -1  # -1 = manual, 0-3 = stepping

            # Keep reported mode as AUTO when auto engine is driving
            if not (self._auto_engine and self._auto_engine.running):
                self._mode = mode
            return self._mode

    def _start_auto_mode(self):
        """Start auto mode with the loaded schedule."""
        from server.auto_engine import AutoEngine

        if self._loaded_schedule is None:
            raise ValueError("No schedule loaded — load a schedule before entering auto mode")

        # Stop any existing auto engine
        if self._auto_engine:
            self._auto_engine.stop()

        self._auto_engine = AutoEngine(
            schedule=self._loaded_schedule,
            get_sensor_data_fn=self._gpio.get_sensor_data,
            set_mode_fn=lambda m: self._set_mode_internal(Mode(m)),
            set_sequence_fn=self._engine.set_sequence,
            set_frequency_fn=self._engine.set_frequency,
        )
        with self._lock:
            self._mode = Mode.AUTO
        self._auto_engine.start()
        log.info("Auto mode started with schedule %r", self._loaded_schedule.name)
        return Mode.AUTO

    # -- schedule management -------------------------------------------------

    def load_schedule(self, schedule):
        """Load a schedule for auto mode (does not start it)."""
        self._loaded_schedule = schedule
        log.info("Schedule loaded: %r (%d steps, %d repeats)",
                 schedule.name, len(schedule.steps), schedule.repeat)

    def get_loaded_schedule(self):
        return self._loaded_schedule

    def get_auto_engine(self):
        return self._auto_engine

    def get_mode(self):
        with self._lock:
            return self._mode

    # -- auto-follow ---------------------------------------------------------

    def set_auto_follow_enabled(self, enabled: bool):
        """Enable or disable threshold-driven mode switching."""
        # Stop any schedule-driven AUTO engine first — they shouldn't both
        # be driving the mode at once.
        if enabled and self._auto_engine:
            log.info("Stopping auto engine because auto-follow is enabling")
            self._auto_engine.stop()
            self._auto_engine = None
        self._auto_follow.set_enabled(enabled)

    def set_auto_follow_thresholds(self, i_enter_a: float, i_exit_a: float):
        self._auto_follow.set_thresholds(i_enter_a, i_exit_a)

    def set_auto_follow_target(self, mode: str):
        self._auto_follow.set_target_mode(mode)

    def get_auto_follow_status(self) -> dict:
        return self._auto_follow.get_status()

    def set_fet(self, index: int, on: bool):
        """Set an individual FET (0=P1, 1=P2, 2=N1, 3=N2). Only works in DEBUG mode."""
        with self._lock:
            if self._mode != Mode.DEBUG:
                raise ValueError("set_fet only available in debug mode")
            if not 0 <= index <= 3:
                raise ValueError(f"FET index must be 0-3, got {index}")
            states = self._gpio.get_fet_states()
            states[index] = bool(on)
            self._gpio.apply_state(tuple(states))
            log.info("Debug: FET %d -> %s", index, "ON" if on else "OFF")

    def debug_step(self):
        """Step through FETs one at a time: P1 -> P2 -> N1 -> N2 -> all off -> repeat."""
        FET_NAMES = ["P1", "P2", "N1", "N2"]
        with self._lock:
            if self._mode != Mode.DEBUG:
                raise ValueError("debug_step only available in debug mode")
            step = getattr(self, "_debug_step", -1)
            step = (step + 1) % 5  # 0-3 = one FET on, 4 = all off
            self._debug_step = step
            if step < 4:
                state = [False, False, False, False]
                state[step] = True
                self._gpio.apply_state(tuple(state))
                log.info("Debug step: %s ON (step %d)", FET_NAMES[step], step)
            else:
                self._gpio.all_off()
                log.info("Debug step: all OFF (step %d)", step)
            return step

    def get_status(self):
        """Return full system state dict (engine step labelled at
        ``monotonic()`` — fine for live broadcasts/TUI)."""
        return self._build_status(self._engine.get_state())

    def get_status_at(self, sample_pi_s):
        """Return a status dict whose engine fields (step, fet_states) are
        labelled at ``sample_pi_s`` instead of ``monotonic()``. Used by
        the recorder so each captured row carries the step that was live
        at sample-capture time, not D-line receive time. Critical at
        higher switching freqs (100 Hz+) where the ~4.5 ms emit latency
        becomes a meaningful fraction of a step."""
        return self._build_status(self._engine.get_state_at(sample_pi_s))

    def _build_status(self, engine_state):
        with self._lock:
            mode = self._mode
            debug_step = getattr(self, "_debug_step", -1)
        status = {
            "mode": mode.value,
            "sequence": engine_state["sequence"],
            "step": engine_state["step"],
            "frequency": engine_state["frequency"],
            "fet_states": engine_state["fet_states"],
            "sensors": self._gpio.get_sensor_data(),
        }
        if mode == Mode.DEBUG:
            status["debug_step"] = debug_step
        if mode == Mode.AUTO and self._auto_engine:
            status["auto"] = self._auto_engine.get_status()
        status["auto_follow"] = self._auto_follow.get_status()
        return status
