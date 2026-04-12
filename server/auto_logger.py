"""
Switching Circuit V2 - Auto Mode Event Logger.

Writes a JSON-lines event log for auto mode operations.  Each line is a
timestamped event recording step transitions, sense window results,
mismatches, cycle completions, and other notable events.

The log is written alongside the regular CSV data log and is intended
for post-experiment analysis.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / "SwitchingCircuitV2_logs"


class AutoLogger:
    """JSON-lines event logger for auto mode."""

    def __init__(self, log_dir: Optional[Path] = None):
        self._log_dir = log_dir or DEFAULT_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._path: Optional[Path] = None

    def start(self, schedule_name: str) -> Path:
        """Open a new log file for an auto mode session."""
        self.stop()  # close any existing
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = schedule_name.replace(" ", "_").replace("/", "_")
        self._path = self._log_dir / f"auto_{safe_name}_{ts}.jsonl"
        self._file = open(self._path, "w")
        self.write_event("log_started", {"schedule": schedule_name})
        log.info("Auto logger started: %s", self._path)
        return self._path

    def stop(self):
        """Close the current log file."""
        if self._file:
            self.write_event("log_stopped", {})
            self._file.close()
            self._file = None
            log.info("Auto logger stopped: %s", self._path)

    def write_event(self, event_type: str, data: dict):
        """Write a single event line to the log."""
        if not self._file:
            return
        record = {
            "ts": datetime.now().isoformat(),
            "event": event_type,
            **data,
        }
        try:
            self._file.write(json.dumps(record, default=str) + "\n")
            self._file.flush()
        except (OSError, ValueError) as e:
            log.warning("Auto logger write error: %s", e)

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def is_logging(self) -> bool:
        return self._file is not None
