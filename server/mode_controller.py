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

            self._mode = mode
            return self._mode

    def get_mode(self):
        with self._lock:
            return self._mode

    def get_status(self):
        """Return full system state dict."""
        with self._lock:
            mode = self._mode
        engine_state = self._engine.get_state()
        return {
            "mode": mode.value,
            "sequence": engine_state["sequence"],
            "step": engine_state["step"],
            "frequency": engine_state["frequency"],
            "fet_states": engine_state["fet_states"],
        }
