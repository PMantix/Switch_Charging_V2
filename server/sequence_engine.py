"""
Switching Circuit V2 - Sequence Engine (thin wrapper over RP2040 timer).

The RP2040 firmware now owns periodic switching via machine.Timer
(see firmware/main.py). This module's job is to translate high-level
Pi-side requests — set_frequency, set_sequence, set_pulse_mode,
pause/resume — into the firmware's C/F/G/H command set, and to
present an estimated "current step" for status reporting by computing
it from elapsed time since the last resume or frequency change.

The estimate drifts slightly from the RP2040's true _seq_idx because
the Pi and RP2040 clocks are not synchronized, but the drift stays
well under one step for display purposes and there's no way to get
the firmware's index without a round-trip per query.
"""

import logging
import threading
from time import monotonic

from server.config import (
    SEQUENCES, STATE_DEFS, NUM_SEQUENCES,
    DEFAULT_FREQ, MIN_FREQ, MAX_FREQ,
    PULSE_CHARGE_SEQUENCE,
)

log = logging.getLogger(__name__)


def _pack_state(state_tuple):
    """Pack a (P1, P2, N1, N2) tuple of bools/ints into a 4-bit int."""
    return (
        (int(bool(state_tuple[0])) << 3)
        | (int(bool(state_tuple[1])) << 2)
        | (int(bool(state_tuple[2])) << 1)
        | int(bool(state_tuple[3]))
    )


class SequenceEngine:
    """Programs the RP2040's firmware-resident switching cycle and tracks
    a running estimate of the current step for status reporting."""

    def __init__(self, gpio_driver):
        self._gpio = gpio_driver
        self._lock = threading.Lock()

        self._sequence_index = 0        # 0-based into SEQUENCES
        self._frequency = DEFAULT_FREQ
        self._pulse_mode = False
        self._paused = True             # start paused; mode controller resumes

        # Running-state tracking (in Pi's monotonic clock). When we program
        # the firmware, we note (step_at_resume, resume_time, period_us)
        # so get_state() can estimate the current step by:
        #     step = (step_at_resume + (now - resume_time) / period) % n
        self._step_at_resume = 0
        self._resume_time = 0.0
        self._period_us = 0

        log.info("SequenceEngine (RP2040-driven) initialised: freq=%.1f Hz, seq=%d",
                 self._frequency, self._sequence_index)

    # -- public API (thread-safe) -------------------------------------------

    def set_frequency(self, hz):
        """Set switching frequency, clamped to [MIN_FREQ, MAX_FREQ]."""
        hz = max(MIN_FREQ, min(MAX_FREQ, float(hz)))
        send_period = None
        with self._lock:
            if not self._paused and self._period_us > 0:
                # Snapshot the current step before the period changes so
                # elapsed-time estimation remains monotonic.
                self._step_at_resume = self._estimate_step_locked()
                self._resume_time = monotonic()
            self._frequency = hz
            self._period_us = self._compute_period_us_locked()
            if not self._paused:
                send_period = self._period_us
        if send_period is not None:
            # Firmware F preserves its _seq_idx; timer is re-armed with the
            # new period, biasing the current step short or long.
            self._gpio.set_step_period_us(send_period)
        log.info("Frequency set to %.2f Hz", hz)

    def get_frequency(self):
        with self._lock:
            return self._frequency

    def set_sequence(self, index):
        index = max(0, min(NUM_SEQUENCES - 1, int(index)))
        reprogram = False
        with self._lock:
            self._sequence_index = index
            self._step_at_resume = 0
            self._resume_time = monotonic()
            if not self._paused:
                reprogram = True
        if reprogram:
            self._program_and_go()
        log.info("Sequence set to %d (%s)", index, SEQUENCES[index])

    def get_sequence(self):
        with self._lock:
            return self._sequence_index

    def set_pulse_mode(self, enabled):
        """Toggle pulse charge mode — uses the fixed 2-step PULSE_CHARGE_SEQUENCE
        with doubled step period so the overall cycle time matches."""
        reprogram = False
        with self._lock:
            self._pulse_mode = bool(enabled)
            self._step_at_resume = 0
            self._resume_time = monotonic()
            self._period_us = self._compute_period_us_locked()
            if not self._paused:
                reprogram = True
        if reprogram:
            self._program_and_go()
        log.info("Pulse mode %s", "enabled" if enabled else "disabled")

    def pause(self):
        """Halt switching on the RP2040 and leave all FETs off."""
        was_running = False
        with self._lock:
            was_running = not self._paused
            self._paused = True
        if was_running:
            self._gpio.stop_switching()
        log.debug("SequenceEngine paused")

    def resume(self):
        """Program the current cycle + period and start switching."""
        with self._lock:
            self._paused = False
            self._step_at_resume = 0
            self._resume_time = monotonic()
            self._period_us = self._compute_period_us_locked()
        self._program_and_go()
        log.debug("SequenceEngine resumed")

    def stop(self):
        """Alias for pause — kept for API compatibility with the old thread
        version that had a distinct shutdown."""
        self.pause()

    def get_state(self):
        """Snapshot for status broadcasts. FET states are computed from the
        live sequence + estimated step while running; during pause we report
        whatever the GPIO cache says (mode controller may have set them
        directly, e.g. all-on during DISCHARGE)."""
        with self._lock:
            paused = self._paused
            sequence_idx = self._sequence_index
            freq = self._frequency
            if paused:
                step = self._step_at_resume
                fet_tuple = None  # signal: pull from gpio below
            else:
                packed_seq = self._current_packed_sequence_locked()
                step = self._estimate_step_locked()
                fet_tuple = _unpack(packed_seq[step])
        if fet_tuple is None:
            fet_states = self._gpio.get_fet_states()
        else:
            fet_states = list(fet_tuple)
        return {
            "sequence": sequence_idx,
            "step": step,
            "frequency": round(freq, 2),
            "fet_states": fet_states,
        }

    # -- internals ----------------------------------------------------------

    def _compute_period_us_locked(self):
        """Pi-side math preserved from the old busy-wait engine: for a
        4-step sequence, step_time = (1/f)/2 so one per-FET toggle cycle
        spans the full period; pulse mode uses 2 steps with doubled time."""
        step_time_s = (1.0 / self._frequency) / 2.0
        if self._pulse_mode:
            step_time_s *= 2.0
        return max(50, int(step_time_s * 1_000_000))

    def _current_packed_sequence_locked(self):
        if self._pulse_mode:
            indices = PULSE_CHARGE_SEQUENCE
        else:
            indices = SEQUENCES[self._sequence_index]
        return [_pack_state(STATE_DEFS[i]) for i in indices]

    def _estimate_step_locked(self):
        """Compute current step index from elapsed time since the last
        resume/freq-change. Not exact, but good enough for TUI display."""
        packed = self._current_packed_sequence_locked()
        n = len(packed)
        if n == 0 or self._period_us <= 0:
            return 0
        elapsed_us = int((monotonic() - self._resume_time) * 1_000_000)
        ticks = elapsed_us // self._period_us if elapsed_us > 0 else 0
        return (self._step_at_resume + ticks) % n

    def _program_and_go(self):
        """Send C + F + G to the firmware. Called outside the lock so the
        (potentially blocking) serial round-trip doesn't hold up get_state()."""
        with self._lock:
            packed = self._current_packed_sequence_locked()
            period_us = self._period_us
        self._gpio.program_sequence(packed)
        self._gpio.set_step_period_us(period_us)
        self._gpio.start_switching()


def _unpack(packed):
    """Inverse of _pack_state — returns a 4-tuple of bools."""
    return (
        bool((packed >> 3) & 1),
        bool((packed >> 2) & 1),
        bool((packed >> 1) & 1),
        bool(packed & 1),
    )
