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

from server.config import DEAD_TIME

log = logging.getLogger(__name__)


class Mode(enum.Enum):
    IDLE = "idle"
    CHARGE = "charge"
    DISCHARGE = "discharge"
    PULSE_CHARGE = "pulse_charge"
    DEBUG = "debug"


class ModeController:
    """Coordinates the GPIODriver, SequenceEngine, and system mode."""

    def __init__(self, gpio_driver, sequence_engine):
        self._gpio = gpio_driver
        self._engine = sequence_engine
        self._lock = threading.Lock()
        self._mode = Mode.IDLE

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
        """
        if isinstance(mode, str):
            try:
                mode = Mode(mode.lower())
            except ValueError:
                raise ValueError(
                    f"Unknown mode {mode!r}. "
                    f"Valid modes: {', '.join(m.value for m in Mode)}"
                )

        with self._lock:
            if mode == self._mode:
                return self._mode

            old = self._mode
            log.info("Mode transition: %s -> %s", old.value, mode.value)

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

            self._mode = mode
            return self._mode

    def get_mode(self):
        with self._lock:
            return self._mode

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
        """Return full system state dict."""
        with self._lock:
            mode = self._mode
            debug_step = getattr(self, "_debug_step", -1)
        engine_state = self._engine.get_state()
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
        return status
