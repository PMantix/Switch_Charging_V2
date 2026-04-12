"""
Switching Circuit V2 - Pi-side CSV Recorder.

Records sensor and circuit state data directly on the Pi filesystem
for medium-duration captures with better timing than Mac-side logging.
"""

import csv
import logging
import threading
from datetime import datetime
from pathlib import Path
from time import monotonic

log = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / "SwitchingCircuitV2_logs"


class PiRecorder:
    """Records state+sensor data to CSV on the Pi filesystem."""

    def __init__(self, log_dir: Path = DEFAULT_LOG_DIR):
        self._log_dir = log_dir
        self._file = None
        self._writer = None
        self._path = None
        self._sample_count = 0
        self._max_samples = 0
        self._start_time = 0.0
        self._lock = threading.Lock()
        self._active = False

    @property
    def is_recording(self):
        return self._active

    @property
    def sample_count(self):
        return self._sample_count

    @property
    def max_samples(self):
        return self._max_samples

    @property
    def file_path(self):
        return self._path

    def start(self, max_samples: int, mode: str = "unknown",
              freq: float = 1.0, seq: int = 0, sensor_hz: float = 15.0):
        """Start recording. Returns the file path."""
        with self._lock:
            if self._active:
                self._stop_internal()

            self._log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            freq_str = f"{freq:.1f}Hz".replace(".", "p")
            sensor_str = f"{sensor_hz:.0f}sps"
            self._path = self._log_dir / f"pi_{mode}_seq{seq+1}_{freq_str}_{sensor_str}_{ts}.csv"

            self._file = open(self._path, "w", newline="")
            self._writer = csv.writer(self._file)
            self._writer.writerow([
                "elapsed_s",
                "mode", "sequence", "step", "frequency_hz",
                "p1_on", "p2_on", "n1_on", "n2_on",
                "p1_voltage", "p1_current_a",
                "p2_voltage", "p2_current_a",
                "n1_voltage", "n1_current_a",
                "n2_voltage", "n2_current_a",
                "auto_step", "auto_detected_state", "auto_match",
            ])

            self._sample_count = 0
            self._max_samples = max_samples
            self._start_time = monotonic()
            self._active = True
            log.info("Pi recording started: %s (%d max samples)", self._path, max_samples)
            return str(self._path)

    def record(self, status: dict):
        """Record one sample. Auto-stops when max_samples reached.
        Returns True if still recording, False if just stopped."""
        with self._lock:
            if not self._active or not self._writer:
                return False

            elapsed = monotonic() - self._start_time
            mode = status.get("mode", "")
            seq = status.get("sequence", 0)
            step = status.get("step", 0)
            freq = status.get("frequency", 0.0)
            fets = status.get("fet_states", [False] * 4)
            sensors = status.get("sensors", {})

            def _sv(name, field):
                s = sensors.get(name, {})
                return s.get(field, 0.0) if isinstance(s, dict) and "error" not in s else 0.0

            # Auto mode columns (empty strings if not in auto mode)
            auto = status.get("auto", {})
            auto_step = auto.get("step_name", "") if auto else ""
            auto_detected = auto.get("detected_state", "") if auto else ""
            auto_match = auto.get("match", "") if auto else ""

            self._writer.writerow([
                f"{elapsed:.6f}",
                mode, seq, step, f"{freq:.2f}",
                int(fets[0]), int(fets[1]), int(fets[2]), int(fets[3]),
                f"{_sv('P1', 'voltage'):.6f}", f"{_sv('P1', 'current'):.8f}",
                f"{_sv('P2', 'voltage'):.6f}", f"{_sv('P2', 'current'):.8f}",
                f"{_sv('N1', 'voltage'):.6f}", f"{_sv('N1', 'current'):.8f}",
                f"{_sv('N2', 'voltage'):.6f}", f"{_sv('N2', 'current'):.8f}",
                auto_step, auto_detected, auto_match,
            ])

            self._sample_count += 1

            if self._sample_count % 100 == 0:
                self._file.flush()

            if self._max_samples > 0 and self._sample_count >= self._max_samples:
                self._stop_internal()
                return False

            return True

    def stop(self):
        """Stop recording. Returns file path."""
        with self._lock:
            return self._stop_internal()

    def _stop_internal(self):
        path = self._path
        if self._file:
            self._file.flush()
            self._file.close()
            log.info("Pi recording stopped: %s (%d samples)", self._path, self._sample_count)
        self._file = None
        self._writer = None
        self._active = False
        return str(path) if path else None
