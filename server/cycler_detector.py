"""
Switching Circuit V2 - Cycler State Detector.

Classifies the external cycler/potentiostat state (CC_CHARGE, CV_CHARGE,
REST, DISCHARGE) from INA226 sensor readings.  Operates on a rolling window
of samples with configurable thresholds and debouncing.

Designed to be standalone — no dependency on mode controller or schedule.
Feed it sensor snapshots and it returns the classified state.
"""

from __future__ import annotations

import enum
import logging
from collections import deque
from dataclasses import dataclass, field
from time import monotonic
from typing import Optional

log = logging.getLogger(__name__)

SENSOR_NAMES = ("P1", "P2", "N1", "N2")


class CyclerState(enum.Enum):
    UNKNOWN = "unknown"
    CC_CHARGE = "cc_charge"
    CV_CHARGE = "cv_charge"
    REST = "rest"
    DISCHARGE = "discharge"


@dataclass
class DetectionThresholds:
    """Configurable thresholds for cycler state detection."""
    rest_threshold: float = 0.005           # A — below this magnitude = REST
    charge_min: float = 0.008               # A — above this (positive) = charging
    discharge_min: float = 0.008            # A — below -this (negative) = discharging
    cv_decline_rate: float = -0.0005        # A/s — current slope indicating CV taper
    cv_voltage_stability: float = 0.015     # V — voltage range for single-criterion (legacy)
    cv_voltage_plateau_range: float = 0.020 # V — voltage range for plateau criterion
    cv_current_drop_ratio: float = 0.20     # fraction — current must drop 20% from peak
    cv_hysteresis_exit_slope: float = 0.002 # A/s — positive slope needed to exit CV
    cv_split_window_s: float = 2.0          # seconds — recent window for slope calc
    debounce_count: int = 5                 # consecutive samples before state change
    window_size: int = 10                   # rolling window size for averaging
    cv_window_s: float = 5.0                # seconds of history for CV detection

    @classmethod
    def from_dict(cls, d: dict) -> "DetectionThresholds":
        """Create from a dict, ignoring unknown keys."""
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class DetectionResult:
    """Output of the cycler state detector."""
    state: CyclerState
    confidence: float       # 0.0–1.0 fraction of window agreeing
    avg_current: float      # A — mean of all sensor currents
    avg_voltage: float      # V — mean of all sensor voltages
    timestamp: float = field(default_factory=monotonic)


class CyclerDetector:
    """
    Classifies the external cycler state from INA226 sensor snapshots.

    Usage:
        detector = CyclerDetector()
        for snapshot in stream:
            result = detector.feed(snapshot)
            print(result.state)
    """

    def __init__(self, thresholds: Optional[DetectionThresholds] = None):
        self._thresholds = thresholds or DetectionThresholds()
        self._window: deque[tuple[float, float, float]] = deque(
            maxlen=self._thresholds.window_size,
        )  # (timestamp, avg_current, avg_voltage)
        self._cv_window: deque[tuple[float, float, float]] = deque(
            maxlen=300,
        )  # longer history for CV slope: (timestamp, avg_current, avg_voltage)
        self._debounce_counter = 0
        self._candidate_state = CyclerState.UNKNOWN
        self._confirmed_state = CyclerState.UNKNOWN
        self._in_cv_phase = False
        self._last_result = DetectionResult(
            state=CyclerState.UNKNOWN, confidence=0.0,
            avg_current=0.0, avg_voltage=0.0,
        )

    @property
    def thresholds(self) -> DetectionThresholds:
        return self._thresholds

    @thresholds.setter
    def thresholds(self, value: DetectionThresholds):
        self._thresholds = value

    def feed(self, sensor_data: dict) -> DetectionResult:
        """
        Feed one sensor snapshot and return the current classification.

        sensor_data: dict like {"P1": {"voltage": V, "current": A}, ...}
        """
        now = monotonic()
        avg_i, avg_v = self._extract_averages(sensor_data)

        self._window.append((now, avg_i, avg_v))
        self._cv_window.append((now, avg_i, avg_v))

        candidate = self._classify(avg_i, avg_v, now)
        self._update_debounce(candidate)

        # Compute confidence: fraction of recent window agreeing with confirmed
        if self._window:
            agreeing = sum(
                1 for _, i, v in self._window
                if self._classify(i, v, now) == self._confirmed_state
            )
            confidence = agreeing / len(self._window)
        else:
            confidence = 0.0

        self._last_result = DetectionResult(
            state=self._confirmed_state,
            confidence=confidence,
            avg_current=avg_i,
            avg_voltage=avg_v,
            timestamp=now,
        )
        return self._last_result

    def get_state(self) -> DetectionResult:
        """Return the current classification without feeding new data."""
        return self._last_result

    def reset(self):
        """Clear all history and reset to UNKNOWN."""
        self._window.clear()
        self._cv_window.clear()
        self._debounce_counter = 0
        self._candidate_state = CyclerState.UNKNOWN
        self._confirmed_state = CyclerState.UNKNOWN
        self._in_cv_phase = False
        self._last_result = DetectionResult(
            state=CyclerState.UNKNOWN, confidence=0.0,
            avg_current=0.0, avg_voltage=0.0,
        )

    # -- internals -----------------------------------------------------------

    def _extract_averages(self, sensor_data: dict) -> tuple[float, float]:
        """Estimate cycler current via KCL on the HV bus.

        I_HV+ = I_P1 + I_P2 (current entering through high-side FETs)
        I_GND = I_N1 + I_N2 (current exiting through low-side FETs)
        These must be equal in steady state; averaging the two sides is
        invariant to which gates are switched on at any instant, so the
        estimate is continuous across switching transitions and does not
        require interrupting the pattern with a transparent sense window.
        """
        p_sum = 0.0
        n_sum = 0.0
        voltages = []
        for name in SENSOR_NAMES:
            s = sensor_data.get(name)
            if not s or "error" in s:
                continue
            i = s.get("current", 0.0)
            v = s.get("voltage", 0.0)
            if name.startswith("P"):
                p_sum += i
            elif name.startswith("N"):
                n_sum += i
            # Voltage averaging: only include active (non-floating) sensors.
            if v > 0.01:
                voltages.append(v)
        avg_i = (p_sum + n_sum) / 2.0
        avg_v = sum(voltages) / len(voltages) if voltages else 0.0
        return avg_i, avg_v

    def _classify(self, avg_i: float, avg_v: float, now: float) -> CyclerState:
        """Classify a single sample based on thresholds, with CV hysteresis."""
        th = self._thresholds

        # REST: current magnitude below threshold
        if abs(avg_i) < th.rest_threshold:
            return CyclerState.REST

        # DISCHARGE: negative current
        if avg_i < -th.discharge_min:
            return CyclerState.DISCHARGE

        # CHARGING: positive current — distinguish CC vs CV
        if avg_i > th.charge_min:
            if self._in_cv_phase:
                # Hysteresis: once in CV, require strong positive slope to exit
                if self._should_exit_cv(now):
                    return CyclerState.CC_CHARGE
                return CyclerState.CV_CHARGE
            if self._is_cv_phase(now):
                return CyclerState.CV_CHARGE
            return CyclerState.CC_CHARGE

        return CyclerState.UNKNOWN

    def _is_cv_phase(self, now: float) -> bool:
        """Detect CV phase using multi-criteria (2 of 3 required):
        1. Current slope declining over recent split window
        2. Current dropped 20%+ from peak in full window
        3. Voltage at a plateau (within cv_voltage_plateau_range)
        """
        th = self._thresholds
        cutoff = now - th.cv_window_s
        recent = [(t, i, v) for t, i, v in self._cv_window if t >= cutoff]

        if len(recent) < 5:
            return False

        voltages = [v for _, _, v in recent]
        all_currents = [i for _, i, _ in recent]
        v_range = max(voltages) - min(voltages)

        signals = 0

        # Signal 1: Current declining in the recent split window
        split_cutoff = now - th.cv_split_window_s
        split_recent = [(t, i, v) for t, i, v in recent if t >= split_cutoff]
        if len(split_recent) >= 3:
            t0 = split_recent[0][0]
            times = [t - t0 for t, _, _ in split_recent]
            currents = [i for _, i, _ in split_recent]
            i_slope = self._linear_slope(times, currents)
            if i_slope < th.cv_decline_rate:
                signals += 1

        # Signal 2: Current has dropped from peak (handles step-changes)
        peak_i = max(all_currents)
        current_i = all_currents[-1]
        if peak_i > 0 and (1 - current_i / peak_i) >= th.cv_current_drop_ratio:
            signals += 1

        # Signal 3: Voltage is at a plateau
        if v_range < th.cv_voltage_plateau_range:
            signals += 1

        return signals >= 2

    def _should_exit_cv(self, now: float) -> bool:
        """Check if conditions warrant leaving CV back to CC.
        Requires a clearly positive current slope (current increasing)."""
        th = self._thresholds
        cutoff = now - th.cv_split_window_s
        recent = [(t, i, v) for t, i, v in self._cv_window if t >= cutoff]
        if len(recent) < 5:
            return False
        t0 = recent[0][0]
        times = [t - t0 for t, _, _ in recent]
        currents = [i for _, i, _ in recent]
        i_slope = self._linear_slope(times, currents)
        return i_slope > th.cv_hysteresis_exit_slope

    @staticmethod
    def _linear_slope(xs: list[float], ys: list[float]) -> float:
        """Compute slope of a simple linear regression."""
        n = len(xs)
        if n < 2:
            return 0.0
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        if den == 0:
            return 0.0
        return num / den

    def _update_debounce(self, candidate: CyclerState):
        """Update debounce counter and confirmed state."""
        if candidate == self._candidate_state:
            self._debounce_counter += 1
        else:
            self._candidate_state = candidate
            self._debounce_counter = 1

        if self._debounce_counter >= self._thresholds.debounce_count:
            if candidate != self._confirmed_state:
                log.info(
                    "Cycler state transition: %s -> %s (debounced %d samples)",
                    self._confirmed_state.value, candidate.value,
                    self._debounce_counter,
                )
                self._confirmed_state = candidate
                # Manage CV hysteresis flag
                if candidate == CyclerState.CV_CHARGE:
                    self._in_cv_phase = True
                elif self._in_cv_phase:
                    self._in_cv_phase = False
