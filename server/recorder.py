"""
Switching Circuit V2 - Pi-side CSV Recorder.

Records sensor and circuit state data directly on the Pi filesystem
for medium-duration captures with better timing than Mac-side logging.

CSV writes happen on a dedicated daemon thread so the broadcast loop
(which drives circuit timing) is never blocked by file I/O. The queue
is unbounded; at 15 Hz × ~100 B per row, even minutes of disk stall
stay within a few MB of memory. If the queue depth ever exceeds
DEPTH_WARN_THRESHOLD we log one warning so backpressure is visible.
"""

import csv
import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from time import monotonic

log = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / "SwitchingCircuitV2_logs"
_DEPTH_WARN_THRESHOLD = 1000  # queue depth at which we log a backpressure warning
_STOP_SENTINEL = object()
_STOP_JOIN_TIMEOUT = 5.0  # seconds to wait for writer to drain before warning


class PiRecorder:
    """Records state+sensor data to CSV on the Pi filesystem."""

    def __init__(self, log_dir: Path = DEFAULT_LOG_DIR):
        self._log_dir = log_dir
        self._path = None
        self._sample_count = 0
        self._max_samples = 0
        self._start_time = 0.0
        self._lock = threading.Lock()
        self._active = False

        # Writer thread + queue
        self._queue: "queue.Queue" = queue.Queue()
        self._writer_thread: threading.Thread | None = None
        self._warned_backpressure = False

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
        # Make sure any prior writer thread has fully drained before we
        # re-open. stop() does this outside our setup lock.
        if self._active:
            self.stop()

        with self._lock:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            freq_str = f"{freq:.1f}Hz".replace(".", "p")
            sensor_str = f"{sensor_hz:.0f}sps"
            self._path = self._log_dir / f"pi_{mode}_seq{seq+1}_{freq_str}_{sensor_str}_{ts}.csv"

            self._sample_count = 0
            self._max_samples = max_samples
            self._start_time = monotonic()
            self._warned_backpressure = False
            self._queue = queue.Queue()  # fresh queue per session
            self._active = True
            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                args=(self._path, self._queue),
                name="PiRecorder-Writer",
                daemon=True,
            )
            self._writer_thread.start()
            log.info("Pi recording started: %s (%d max samples)", self._path, max_samples)
            return str(self._path)

    def record(self, status: dict):
        """Record one sample. Auto-stops when max_samples reached.
        Returns True if still recording, False if just stopped.

        Producer-side only: builds the row and enqueues it. Never blocks
        on I/O; the writer thread handles the actual file write.
        """
        with self._lock:
            if not self._active:
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

            auto = status.get("auto", {})
            auto_step = auto.get("step_name", "") if auto else ""
            auto_detected = auto.get("detected_state", "") if auto else ""
            auto_match = auto.get("match", "") if auto else ""

            row = [
                f"{elapsed:.6f}",
                mode, seq, step, f"{freq:.2f}",
                int(fets[0]), int(fets[1]), int(fets[2]), int(fets[3]),
                f"{_sv('P1', 'voltage'):.6f}", f"{_sv('P1', 'current'):.8f}",
                f"{_sv('P2', 'voltage'):.6f}", f"{_sv('P2', 'current'):.8f}",
                f"{_sv('N1', 'voltage'):.6f}", f"{_sv('N1', 'current'):.8f}",
                f"{_sv('N2', 'voltage'):.6f}", f"{_sv('N2', 'current'):.8f}",
                auto_step, auto_detected, auto_match,
            ]
            self._queue.put_nowait(row)
            self._sample_count += 1

            depth = self._queue.qsize()
            if depth > _DEPTH_WARN_THRESHOLD and not self._warned_backpressure:
                log.warning(
                    "PiRecorder writer backlog at %d rows — disk may be slow; "
                    "no samples dropped",
                    depth,
                )
                self._warned_backpressure = True

            hit_max = self._max_samples > 0 and self._sample_count >= self._max_samples
            if hit_max:
                # Signal writer to finish, but don't block the broadcast
                # loop on join(); stop() in the caller does that.
                self._active = False
                self._queue.put(_STOP_SENTINEL)
                return False
            return True

    def stop(self):
        """Stop recording: drain queue, close file, return path. Safe to
        call from any thread; blocks up to _STOP_JOIN_TIMEOUT on drain."""
        with self._lock:
            path = self._path
            thread = self._writer_thread
            was_active = self._active
            self._active = False
            self._writer_thread = None
            if was_active:
                self._queue.put(_STOP_SENTINEL)
            count = self._sample_count

        if thread is not None:
            thread.join(timeout=_STOP_JOIN_TIMEOUT)
            if thread.is_alive():
                log.warning(
                    "PiRecorder writer did not drain within %ss — rows may be queued",
                    _STOP_JOIN_TIMEOUT,
                )
            else:
                log.info("Pi recording stopped: %s (%d samples)", path, count)
        return str(path) if path else None

    def _writer_loop(self, path: Path, q: "queue.Queue") -> None:
        """Drain queue → CSV until stop sentinel. Runs on its own thread."""
        try:
            f = open(path, "w", newline="")
        except OSError as exc:
            log.error("PiRecorder failed to open %s: %s", path, exc)
            # Drain queue so producers don't see false backpressure if the
            # open failed after records were already queued.
            while True:
                item = q.get()
                if item is _STOP_SENTINEL:
                    return
        try:
            w = csv.writer(f)
            w.writerow([
                "elapsed_s",
                "mode", "sequence", "step", "frequency_hz",
                "p1_on", "p2_on", "n1_on", "n2_on",
                "p1_voltage", "p1_current_a",
                "p2_voltage", "p2_current_a",
                "n1_voltage", "n1_current_a",
                "n2_voltage", "n2_current_a",
                "auto_step", "auto_detected_state", "auto_match",
            ])
            rows_since_flush = 0
            while True:
                item = q.get()
                if item is _STOP_SENTINEL:
                    break
                try:
                    w.writerow(item)
                except (OSError, ValueError) as exc:
                    log.warning("PiRecorder write error (row dropped): %s", exc)
                    continue
                rows_since_flush += 1
                if rows_since_flush >= 50:
                    try:
                        f.flush()
                    except OSError as exc:
                        log.warning("PiRecorder flush error: %s", exc)
                    rows_since_flush = 0
        finally:
            try:
                f.flush()
                f.close()
            except OSError:
                pass
