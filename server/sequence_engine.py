"""
Switching Circuit V2 - Sequence Engine.

Runs in its own daemon thread, stepping through the selected switching
sequence at the configured frequency.  Supports pause/resume and
thread-safe parameter changes from any caller (rotary encoder callbacks,
command server, mode controller).
"""

import logging
import threading
from time import time

from server.config import (
    SEQUENCES, STATE_DEFS, NUM_SEQUENCES,
    DEFAULT_FREQ, MIN_FREQ, MAX_FREQ,
    PULSE_CHARGE_SEQUENCE,
)

log = logging.getLogger(__name__)


class SequenceEngine:
    """Drives the H-bridge through a 4-step switching sequence."""

    def __init__(self, gpio_driver):
        self._gpio = gpio_driver

        # Protected state
        self._lock = threading.Lock()
        self._sequence_index = 0        # 0-based index into SEQUENCES
        self._step = 0                  # current step within the sequence
        self._frequency = DEFAULT_FREQ  # Hz
        self._sequence_version = 0      # bumped on sequence change
        self._pulse_mode = False        # when True, use PULSE_CHARGE_SEQUENCE

        # Pause / resume
        self._pause_event = threading.Event()
        self._pause_event.clear()       # start paused (mode controller will resume)

        self._paused_at_version = self._sequence_version

        # Shutdown flag
        self._stop_event = threading.Event()

        # Worker thread
        self._thread = threading.Thread(
            target=self._run, name="SequenceEngine", daemon=True,
        )
        self._thread.start()
        log.info("SequenceEngine started (paused, freq=%.1f Hz, seq=%d)",
                 self._frequency, self._sequence_index)

    # -- public API (thread-safe) -------------------------------------------

    def set_frequency(self, hz):
        """Set switching frequency, clamped to [MIN_FREQ, MAX_FREQ]."""
        hz = max(MIN_FREQ, min(MAX_FREQ, float(hz)))
        with self._lock:
            self._frequency = hz
        log.info("Frequency set to %.2f Hz", hz)

    def get_frequency(self):
        with self._lock:
            return self._frequency

    def set_sequence(self, index):
        """Select a sequence by 0-based index."""
        index = max(0, min(NUM_SEQUENCES - 1, int(index)))
        with self._lock:
            self._sequence_index = index
            self._sequence_version += 1
        log.info("Sequence set to %d (%s)", index, SEQUENCES[index])

    def get_sequence(self):
        with self._lock:
            return self._sequence_index

    def set_pulse_mode(self, enabled):
        """Enable or disable pulse charge mode (fixed 2-step sequence [0, 3])."""
        with self._lock:
            self._pulse_mode = bool(enabled)
            if enabled:
                self._step = 0
        log.info("Pulse mode %s", "enabled" if enabled else "disabled")

    def pause(self):
        """Freeze stepping (FET outputs are NOT changed here — caller decides)."""
        with self._lock:
            self._paused_at_version = self._sequence_version
        self._pause_event.clear()
        log.debug("SequenceEngine paused")

    def resume(self):
        """Resume stepping.  Resets to step 0 if the sequence changed while paused."""
        with self._lock:
            if self._sequence_version != self._paused_at_version:
                self._step = 0
                log.debug("Sequence changed while paused — reset to step 0")
        self._pause_event.set()
        log.debug("SequenceEngine resumed")

    def stop(self):
        """Signal the worker thread to exit."""
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused
        self._thread.join(timeout=2.0)
        log.info("SequenceEngine stopped")

    def get_state(self):
        """Return a snapshot dict of the engine's current state."""
        with self._lock:
            seq_idx = self._sequence_index
            step = self._step
            freq = self._frequency
        return {
            "sequence": seq_idx,
            "step": step,
            "frequency": round(freq, 2),
            "fet_states": self._gpio.get_fet_states(),
        }

    # -- internals ----------------------------------------------------------

    def _run(self):
        """Main loop: step through the sequence at the configured rate."""
        last_step_time = time()

        while not self._stop_event.is_set():
            # Block until resumed (or stop)
            self._pause_event.wait(timeout=0.05)
            if self._stop_event.is_set():
                break
            if not self._pause_event.is_set():
                continue

            with self._lock:
                freq = self._frequency
                seq_idx = self._sequence_index
                step = self._step
                pulse = self._pulse_mode

            # step_time: for 4-step sequences, full cycle = 4 * step_time
            # pulse mode has 2 steps, so double step_time to match the same cycle period
            step_time = (1.0 / freq) / 2.0
            if pulse:
                step_time *= 2.0

            now = time()
            if now - last_step_time >= step_time:
                if pulse:
                    seq = PULSE_CHARGE_SEQUENCE
                    num_steps = len(seq)
                else:
                    seq = SEQUENCES[seq_idx]
                    num_steps = len(seq)
                state_index = seq[step % num_steps]
                self._gpio.apply_state(STATE_DEFS[state_index])

                with self._lock:
                    self._step = (step + 1) % num_steps

                last_step_time = now
