"""
Switching Circuit V2 - Schedule Format and Parser.

Defines, loads, and validates JSON schedule files that describe an expected
Arbin cycler test program.  The auto-mode engine uses these schedules to
know what circuit action to apply at each step and what sensor state to
expect from the external cycler.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from server.cycler_detector import CyclerState, DetectionThresholds

log = logging.getLogger(__name__)

# Valid values for circuit_action (maps to Mode enum values)
VALID_CIRCUIT_ACTIONS = ("idle", "charge", "discharge", "pulse_charge")

# Valid values for expected_state (maps to CyclerState enum values)
VALID_EXPECTED_STATES = tuple(s.value for s in CyclerState if s != CyclerState.UNKNOWN)

# Valid on_timeout behaviours
VALID_ON_TIMEOUT = ("wait", "advance", "abort")

# Sensible pairings: expected_state -> set of compatible circuit_actions
VALID_SEMANTIC_PAIRS = {
    "cc_charge": {"charge", "discharge"},
    "cv_charge": {"charge", "discharge"},
    "rest": {"idle", "discharge"},
    "discharge": {"discharge"},
}


@dataclass
class ScheduleStep:
    """A single step in a cycler test schedule."""
    name: str
    expected_state: str          # CyclerState value: cc_charge, cv_charge, rest, discharge
    circuit_action: str          # Mode value: idle, charge, discharge, pulse_charge
    timeout_s: float             # max expected duration for this step
    sequence: int = 1            # switching sequence index (only for circuit_action=charge)
    frequency: float = 1.0       # switching frequency Hz (only for circuit_action=charge)
    on_timeout: str = "wait"     # "wait", "advance", or "abort"
    timeout_grace_s: float = -1  # -1 = auto: min(timeout_s * 0.2, 120)

    def expected_cycler_state(self) -> CyclerState:
        return CyclerState(self.expected_state)

    def effective_grace(self) -> float:
        if self.timeout_grace_s >= 0:
            return self.timeout_grace_s
        return min(self.timeout_s * 0.2, 120.0)


@dataclass
class Schedule:
    """A complete cycler test schedule."""
    name: str
    steps: list[ScheduleStep]
    repeat: int = 1
    description: str = ""
    detection_thresholds: DetectionThresholds = field(default_factory=DetectionThresholds)
    default_on_timeout: str = "wait"
    default_timeout_grace_s: float = -1

    @property
    def total_steps_per_cycle(self) -> int:
        return len(self.steps)

    @property
    def total_steps(self) -> int:
        return len(self.steps) * self.repeat


def load_schedule(path: Union[str, Path]) -> Schedule:
    """
    Load and parse a schedule JSON file.

    Raises FileNotFoundError if the file doesn't exist.
    Raises ValueError if the JSON is malformed or fails validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Schedule file not found: {path}")

    with open(path) as f:
        raw = json.load(f)

    schedule = _parse_schedule(raw)
    errors = validate_schedule(schedule)
    if errors:
        raise ValueError(
            f"Schedule validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    warnings = validate_schedule_semantics(schedule)
    for w in warnings:
        log.warning("Schedule warning: %s", w)

    log.info("Loaded schedule %r from %s (%d steps, %d repeats)",
             schedule.name, path, len(schedule.steps), schedule.repeat)
    return schedule


def load_schedule_inline(raw: dict) -> Schedule:
    """
    Parse a schedule from a dict (e.g., from an inline JSON command).

    Raises ValueError if validation fails.
    """
    schedule = _parse_schedule(raw)
    errors = validate_schedule(schedule)
    if errors:
        raise ValueError(
            f"Schedule validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    warnings = validate_schedule_semantics(schedule)
    for w in warnings:
        log.warning("Schedule warning: %s", w)
    return schedule


def validate_schedule(schedule: Schedule) -> list[str]:
    """
    Validate a parsed schedule. Returns a list of error strings (empty = valid).
    """
    errors = []

    if not schedule.name:
        errors.append("Schedule 'name' is required")

    if not schedule.steps:
        errors.append("Schedule must have at least one step")

    if schedule.repeat < 1:
        errors.append(f"'repeat' must be >= 1, got {schedule.repeat}")

    for i, step in enumerate(schedule.steps):
        prefix = f"Step {i} ({step.name!r})"

        if not step.name:
            errors.append(f"Step {i}: 'name' is required")

        if step.expected_state not in VALID_EXPECTED_STATES:
            errors.append(
                f"{prefix}: invalid expected_state {step.expected_state!r}, "
                f"must be one of {VALID_EXPECTED_STATES}"
            )

        if step.circuit_action not in VALID_CIRCUIT_ACTIONS:
            errors.append(
                f"{prefix}: invalid circuit_action {step.circuit_action!r}, "
                f"must be one of {VALID_CIRCUIT_ACTIONS}"
            )

        if step.timeout_s <= 0:
            errors.append(f"{prefix}: timeout_s must be > 0, got {step.timeout_s}")

        if step.circuit_action == "charge":
            if not (0 <= step.sequence <= 7):
                errors.append(f"{prefix}: sequence must be 0-7, got {step.sequence}")
            if step.frequency <= 0:
                errors.append(f"{prefix}: frequency must be > 0, got {step.frequency}")

        if step.on_timeout not in VALID_ON_TIMEOUT:
            errors.append(
                f"{prefix}: invalid on_timeout {step.on_timeout!r}, "
                f"must be one of {VALID_ON_TIMEOUT}"
            )

    if schedule.default_on_timeout not in VALID_ON_TIMEOUT:
        errors.append(
            f"'default_on_timeout' must be one of {VALID_ON_TIMEOUT}, "
            f"got {schedule.default_on_timeout!r}"
        )

    return errors


def validate_schedule_semantics(schedule: Schedule) -> list[str]:
    """Check semantic consistency. Returns warnings (not errors)."""
    warnings = []
    for i, step in enumerate(schedule.steps):
        prefix = f"Step {i} ({step.name!r})"
        valid_actions = VALID_SEMANTIC_PAIRS.get(step.expected_state)
        if valid_actions and step.circuit_action not in valid_actions:
            warnings.append(
                f"{prefix}: expected_state={step.expected_state!r} with "
                f"circuit_action={step.circuit_action!r} may be contradictory "
                f"(expected one of {sorted(valid_actions)})"
            )

    for i in range(len(schedule.steps) - 1):
        if schedule.steps[i].expected_state == schedule.steps[i + 1].expected_state:
            warnings.append(
                f"Steps {i} and {i+1} both expect {schedule.steps[i].expected_state!r} "
                f"\u2014 auto engine may not detect a transition between them"
            )

    return warnings


def _parse_schedule(raw: dict) -> Schedule:
    """Parse a raw dict into a Schedule. Raises ValueError on missing fields."""
    if not isinstance(raw, dict):
        raise ValueError("Schedule must be a JSON object")

    name = raw.get("name", "")
    description = raw.get("description", "")
    repeat = int(raw.get("repeat", 1))
    default_on_timeout = str(raw.get("default_on_timeout", "wait"))
    default_timeout_grace_s = float(raw.get("default_timeout_grace_s", -1))

    # Parse detection thresholds (optional, with defaults)
    thresh_raw = raw.get("detection_thresholds", {})
    thresholds = DetectionThresholds.from_dict(thresh_raw)

    # Parse steps
    raw_steps = raw.get("steps")
    if raw_steps is None:
        raise ValueError("Schedule must contain a 'steps' array")
    if not isinstance(raw_steps, list):
        raise ValueError("'steps' must be an array")

    steps = []
    for i, rs in enumerate(raw_steps):
        if not isinstance(rs, dict):
            raise ValueError(f"Step {i} must be a JSON object")
        try:
            step = ScheduleStep(
                name=str(rs.get("name", f"Step {i}")),
                expected_state=str(rs.get("expected_state", "")),
                circuit_action=str(rs.get("circuit_action", "")),
                timeout_s=float(rs.get("timeout_s", 0)),
                sequence=int(rs.get("sequence", 1)),
                frequency=float(rs.get("frequency", 1.0)),
                on_timeout=str(rs.get("on_timeout", default_on_timeout)),
                timeout_grace_s=float(rs.get("timeout_grace_s", default_timeout_grace_s)),
            )
        except (TypeError, ValueError) as e:
            raise ValueError(f"Step {i}: invalid field value — {e}")
        steps.append(step)

    return Schedule(
        name=name,
        steps=steps,
        repeat=repeat,
        description=description,
        detection_thresholds=thresholds,
        default_on_timeout=default_on_timeout,
        default_timeout_grace_s=default_timeout_grace_s,
    )
