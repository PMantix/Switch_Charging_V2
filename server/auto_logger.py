"""
Switching Circuit V2 - Auto Mode Event Logger.

Writes a JSON-lines event log for auto mode operations.  Each line is a
timestamped event recording step transitions, sense window results,
mismatches, cycle completions, and other notable events.

Writes happen on a daemon thread so the auto engine's emit path never
blocks on file I/O, matching the pattern in PiRecorder and DataLogger.
Event rate is low (not a hot path) but consistency keeps the offload
story uniform across the three logs.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / "SwitchingCircuitV2_logs"
_STOP_SENTINEL = object()
_STOP_JOIN_TIMEOUT = 5.0


class AutoLogger:
    """JSON-lines event logger for auto mode."""

    def __init__(self, log_dir: Optional[Path] = None):
        self._log_dir = log_dir or DEFAULT_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._path: Optional[Path] = None
        self._queue: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._active = False

    def start(self, schedule_name: str) -> Path:
        """Open a new log file for an auto mode session."""
        self.stop()  # close any existing
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = schedule_name.replace(" ", "_").replace("/", "_")
        self._path = self._log_dir / f"auto_{safe_name}_{ts}.jsonl"
        self._queue = queue.Queue()
        self._active = True
        self._thread = threading.Thread(
            target=self._writer_loop,
            args=(self._path, self._queue),
            name="AutoLogger-Writer",
            daemon=True,
        )
        self._thread.start()
        self.write_event("log_started", {"schedule": schedule_name})
        log.info("Auto logger started: %s", self._path)
        return self._path

    def stop(self):
        """Close the current log file."""
        if not self._active:
            return
        self.write_event("log_stopped", {})
        self._active = False
        self._queue.put(_STOP_SENTINEL)
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=_STOP_JOIN_TIMEOUT)
            if thread.is_alive():
                log.warning("AutoLogger did not drain within %ss", _STOP_JOIN_TIMEOUT)
            else:
                log.info("Auto logger stopped: %s", self._path)

    def write_event(self, event_type: str, data: dict):
        """Queue a single event line for the writer thread."""
        if not self._active:
            return
        record = {
            "ts": datetime.now().isoformat(),
            "event": event_type,
            **data,
        }
        try:
            line = json.dumps(record, default=str) + "\n"
        except (TypeError, ValueError) as exc:
            log.warning("Auto logger serialize error: %s", exc)
            return
        self._queue.put_nowait(line)

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def is_logging(self) -> bool:
        return self._active

    def _writer_loop(self, path: Path, q: "queue.Queue") -> None:
        try:
            f = open(path, "w")
        except OSError as exc:
            log.error("AutoLogger failed to open %s: %s", path, exc)
            while True:
                item = q.get()
                if item is _STOP_SENTINEL:
                    return
        try:
            while True:
                item = q.get()
                if item is _STOP_SENTINEL:
                    break
                try:
                    f.write(item)
                    f.flush()  # per-event flush preserves existing durability
                except OSError as exc:
                    log.warning("AutoLogger write error: %s", exc)
        finally:
            try:
                f.flush()
                f.close()
            except OSError:
                pass
