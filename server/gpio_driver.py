"""
Switching Circuit V2 - GPIO Driver.

Single point of hardware control for the 4 H-bridge MOSFETs.
Falls back to a mock implementation when gpiozero is unavailable
(development/testing on non-Pi machines).
"""

import logging

from server.config import (
    PIN_P1, PIN_P2, PIN_N1, PIN_N2,
    MOSFET_ACTIVE_HIGH,
    STATE_DEFS,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock for non-Pi development
# ---------------------------------------------------------------------------
try:
    from gpiozero import OutputDevice
    _MOCK = False
except ImportError:
    _MOCK = True
    log.warning("gpiozero not available — using MockOutputDevice")

    class OutputDevice:  # type: ignore[no-redef]
        """Minimal stand-in that logs state changes."""

        def __init__(self, pin, *, active_high=True):
            self.pin = pin
            self.active_high = active_high
            self._value = False
            log.debug("MockOutputDevice created on pin %d", pin)

        @property
        def value(self):
            return self._value

        @value.setter
        def value(self, v):
            self._value = bool(v)
            log.debug("MockOutputDevice pin %d -> %s", self.pin, self._value)

        def on(self):
            self.value = True

        def off(self):
            self.value = False

        def close(self):
            self.off()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
class GPIODriver:
    """Controls the four H-bridge MOSFET outputs."""

    def __init__(self):
        self._devices = [
            OutputDevice(PIN_P1, active_high=MOSFET_ACTIVE_HIGH),
            OutputDevice(PIN_P2, active_high=MOSFET_ACTIVE_HIGH),
            OutputDevice(PIN_N1, active_high=MOSFET_ACTIVE_HIGH),
            OutputDevice(PIN_N2, active_high=MOSFET_ACTIVE_HIGH),
        ]
        self.all_off()
        log.info(
            "GPIODriver initialised (mock=%s) — pins P1=%d P2=%d N1=%d N2=%d",
            _MOCK, PIN_P1, PIN_P2, PIN_N1, PIN_N2,
        )

    # -- public API ---------------------------------------------------------

    def apply_state(self, state_tuple):
        """Set all 4 FETs from a (P1, P2, N1, N2) bool tuple."""
        for dev, val in zip(self._devices, state_tuple):
            dev.value = val

    def all_on(self):
        """Turn every MOSFET on."""
        self.apply_state(STATE_DEFS[4])

    def all_off(self):
        """Turn every MOSFET off."""
        self.apply_state(STATE_DEFS[5])

    def get_fet_states(self):
        """Return current FET states as a list of bools [P1, P2, N1, N2]."""
        return [bool(d.value) for d in self._devices]

    def cleanup(self):
        """Safe shutdown: all off, then release resources."""
        self.all_off()
        for dev in self._devices:
            dev.close()
        log.info("GPIODriver cleaned up")
