"""
Switching Circuit V2 - Data Logger.

Two recording tiers:
  - Pi (≤ 10min): Records on Pi SD card at full stream rate, SCPs to Mac when done
  - Mac (> 10min): Records directly on Mac from TUI state stream
"""

import csv
import logging
import subprocess
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / "SwitchingCircuitV2_logs"
PI_MAX_SECONDS = 600  # 10 minutes


class RecordTier(Enum):
    PI = "pi"
    MAC = "mac"


def select_tier(duration_s: float) -> RecordTier:
    if duration_s <= PI_MAX_SECONDS:
        return RecordTier.PI
    return RecordTier.MAC


class DataLogger:
    def __init__(self, log_dir: Path = DEFAULT_LOG_DIR):
        self._log_dir = log_dir
        self._tier: Optional[RecordTier] = None
        self._duration_s: float = 10.0

        # Mac-side state
        self._file = None
        self._writer = None
        self._path: Optional[Path] = None
        self._sample_count = 0
        self._max_samples = 0
        self._start_time: Optional[datetime] = None

        # Pi recording state
        self._pi_recording = False
        self._pi_path: Optional[str] = None
        self._client = None

    @property
    def is_logging(self) -> bool:
        return self._file is not None or self._pi_recording

    @property
    def file_path(self) -> Optional[Path]:
        return self._path

    @property
    def sample_count(self) -> int:
        return self._sample_count

    @property
    def duration_s(self) -> float:
        return self._duration_s

    @duration_s.setter
    def duration_s(self, val: float):
        self._duration_s = max(1.0, val)

    @property
    def tier(self) -> Optional[RecordTier]:
        return self._tier

    @property
    def elapsed(self) -> float:
        if self._start_time:
            return (datetime.now() - self._start_time).total_seconds()
        return 0.0

    def start(self, mode: str = "idle", freq: float = 1.0,
              seq: int = 0, sensor_hz: float = 2.0,
              client=None) -> tuple[RecordTier, str]:
        if self.is_logging:
            self.stop()

        self._client = client
        self._tier = select_tier(self._duration_s)
        self._start_time = datetime.now()
        self._sample_count = 0
        total_samples = int(self._duration_s * sensor_hz)

        if self._tier == RecordTier.PI:
            return self._start_pi(total_samples, mode, freq, seq, sensor_hz)
        else:
            return self._start_mac(total_samples, mode, freq, seq, sensor_hz)

    def _start_pi(self, count, mode, freq, seq, sensor_hz):
        if self._client:
            # Build expected filename (matches server/recorder.py naming)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            freq_str = f"{freq:.1f}Hz".replace(".", "p")
            sensor_str = f"{sensor_hz:.0f}sps"
            expected_name = f"pi_{mode}_seq{seq+1}_{freq_str}_{sensor_str}_{ts}.csv"
            expected_path = f"/home/pi/SwitchingCircuitV2_logs/{expected_name}"

            self._client.send_command({
                "cmd": "pi_record_start",
                "max_samples": count,
                "rec_mode": mode,
                "rec_freq": freq,
                "rec_seq": seq,
                "rec_sensor_hz": sensor_hz,
            })
            self._pi_recording = True
            self._pi_path = expected_path
            self._max_samples = count
            dur = self._duration_s
            return RecordTier.PI, f"Pi: {count} samples ({dur:.0f}s)"

        # Fallback to Mac
        self._tier = RecordTier.MAC
        return self._start_mac(count, mode, freq, seq, sensor_hz)

    def _start_mac(self, count, mode, freq, seq, sensor_hz):
        self._log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        freq_str = f"{freq:.1f}Hz".replace(".", "p")
        sensor_str = f"{sensor_hz:.0f}sps"
        self._path = self._log_dir / f"{mode}_seq{seq+1}_{freq_str}_{sensor_str}_{ts}.csv"

        self._file = open(self._path, "w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow([
            "timestamp", "elapsed_s",
            "mode", "sequence", "step", "frequency_hz",
            "p1_on", "p2_on", "n1_on", "n2_on",
            "p1_voltage", "p1_current_a",
            "p2_voltage", "p2_current_a",
            "n1_voltage", "n1_current_a",
            "n2_voltage", "n2_current_a",
        ])
        self._max_samples = count
        return RecordTier.MAC, f"Mac: {count} samples -> {self._path.name}"

    def record(self, data: dict) -> bool:
        """Record one sample (Mac-side only). Returns False if auto-stopped."""
        if not self._writer:
            return True

        now = datetime.now()
        elapsed = (now - self._start_time).total_seconds() if self._start_time else 0.0
        sensors = data.get("sensors", {})

        def _sv(name, field):
            s = sensors.get(name, {})
            return s.get(field, 0.0) if isinstance(s, dict) and "error" not in s else 0.0

        fets = data.get("fet_states", [False] * 4)
        self._writer.writerow([
            now.isoformat(timespec="milliseconds"),
            f"{elapsed:.3f}",
            data.get("mode", ""), data.get("sequence", 0),
            data.get("step", 0), f"{data.get('frequency', 0.0):.2f}",
            int(fets[0]), int(fets[1]), int(fets[2]), int(fets[3]),
            f"{_sv('P1', 'voltage'):.6f}", f"{_sv('P1', 'current'):.8f}",
            f"{_sv('P2', 'voltage'):.6f}", f"{_sv('P2', 'current'):.8f}",
            f"{_sv('N1', 'voltage'):.6f}", f"{_sv('N1', 'current'):.8f}",
            f"{_sv('N2', 'voltage'):.6f}", f"{_sv('N2', 'current'):.8f}",
        ])
        self._sample_count += 1
        if self._sample_count % 50 == 0:
            self._file.flush()

        if self._max_samples > 0 and self._sample_count >= self._max_samples:
            return False
        return True

    def check_pi_done(self) -> bool:
        """Check if Pi recording has finished (elapsed > duration)."""
        if self._tier != RecordTier.PI or not self._pi_recording:
            return False
        return self.elapsed >= self._duration_s + 1

    def stop(self) -> tuple[Optional[RecordTier], str]:
        tier = self._tier

        if tier == RecordTier.PI and self._pi_recording:
            desc = self._stop_pi()
        elif self._file:
            desc = self._stop_mac()
        else:
            desc = "Not recording"

        self._tier = None
        self._pi_recording = False
        self._client = None
        return tier, desc

    def _stop_pi(self) -> str:
        # Tell Pi to stop recording
        if self._client:
            self._client.send_command({"cmd": "pi_record_stop"})

        # Brief pause to let Pi flush
        import time
        time.sleep(0.5)

        # Find the most recent pi_*.csv on the Pi
        try:
            result = subprocess.run(
                ["ssh", "pi@raspberrypi.local",
                 "ls -t ~/SwitchingCircuitV2_logs/pi_*.csv 2>/dev/null | head -1"],
                capture_output=True, text=True, timeout=10,
            )
            remote_path = result.stdout.strip()
        except Exception:
            remote_path = self._pi_path

        if not remote_path:
            return "No data on Pi"

        # SCP from Pi to Mac
        self._log_dir.mkdir(parents=True, exist_ok=True)
        local_name = Path(remote_path).name
        local_path = self._log_dir / local_name
        try:
            result = subprocess.run(
                ["scp", f"pi@raspberrypi.local:{remote_path}", str(local_path)],
                capture_output=True, timeout=30,
            )
            if result.returncode == 0:
                with open(local_path) as f:
                    samples = sum(1 for _ in f) - 1
                self._path = local_path
                self._sample_count = samples
                subprocess.run(
                    ["ssh", "pi@raspberrypi.local", f"rm {remote_path}"],
                    capture_output=True, timeout=10,
                )
                return f"{samples} samples -> {local_name}"
            else:
                err = result.stderr.decode(errors='replace').strip()
                return f"SCP failed: {err}"
        except Exception as e:
            log.warning("SCP failed: %s", e)
            return f"Transfer failed: {e}"

    def _stop_mac(self) -> str:
        path = self._path
        count = self._sample_count
        if self._file:
            self._file.flush()
            self._file.close()
        self._file = None
        self._writer = None
        return f"{count} samples -> {path.name}" if path else "No data"
