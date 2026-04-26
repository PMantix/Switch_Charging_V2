#!/usr/bin/env python3
"""
Switching Circuit V2 - Neware XML → schedule JSON converter.

Translates a Neware BTS Step File XML into the schedule.py JSON format
consumed by ScheduleMonitor. The output is just text — review the JSON
before loading it onto a Pi to ensure step boundaries, expected states,
and timeouts match what the cycler is actually programmed to do.

Step type mapping (verified against a 270 mAh formation schedule):
    1  CC charge        → expected_state = "cc_charge"
    2  CC discharge     → expected_state = "discharge"
    4  Rest             → expected_state = "rest"
    6  End              → drops out of step list (schedule terminator)
    7  CCCV charge      → expected_state = "cc_charge" (single PLAN
                          step; CV phase will surface in the monitor's
                          OBSERVED column as a divergence)

Unit scaling:
    Time:      ms       → seconds         (×0.001)
    Volt:      0.1 mV   → V               (×0.0001)
    Curr:      mA       → A               (×0.001, only in metadata)

Untouched / not modeled:
    Cap (capacity-transfer cap), Whole_Prt protection limits,
    Stop_Volt and Stop_Curr — preserved in step `neware_meta` for
    traceability but not used by the monitor.

Usage:
    python tools/neware_to_json.py path/to/input.xml [path/to/output.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# Neware step type → (expected_state, default_name)
STEP_TYPE_MAP = {
    1: ("cc_charge", "CC Charge"),
    2: ("discharge", "CC Discharge"),
    4: ("rest", "Rest"),
    7: ("cc_charge", "CCCV Charge"),
}
TERMINATOR_STEP_TYPES = {6}


@dataclass
class _NewareStep:
    step_id: int
    step_type: int
    time_ms: Optional[int] = None
    curr_ma: Optional[float] = None
    volt_01mV: Optional[int] = None
    stop_volt_01mV: Optional[int] = None
    stop_curr_ma: Optional[float] = None
    cap: Optional[int] = None


def parse_neware_xml(path: Path) -> list[_NewareStep]:
    """Parse the Step_Info section of a Neware Step File XML."""
    tree = ET.parse(path)
    root = tree.getroot()

    config = root.find("config")
    step_info = config.find("Step_Info") if config is not None else None
    if step_info is None:
        raise ValueError(f"No <Step_Info> section in {path}")

    steps: list[_NewareStep] = []
    for child in step_info:
        if not child.tag.startswith("Step"):
            continue
        try:
            step_id = int(child.get("Step_ID", "0"))
            step_type = int(child.get("Step_Type", "0"))
        except (TypeError, ValueError):
            continue

        ns = _NewareStep(step_id=step_id, step_type=step_type)
        main = child.find("Limit/Main")
        if main is not None:
            ns.time_ms = _opt_int(main, "Time")
            ns.curr_ma = _opt_float(main, "Curr")
            ns.volt_01mV = _opt_int(main, "Volt")
            ns.stop_volt_01mV = _opt_int(main, "Stop_Volt")
            ns.stop_curr_ma = _opt_float(main, "Stop_Curr")
            ns.cap = _opt_int(main, "Cap")

        steps.append(ns)
    return steps


def _opt_int(parent: ET.Element, tag: str) -> Optional[int]:
    el = parent.find(tag)
    if el is None or el.get("Value") is None:
        return None
    try:
        return int(el.get("Value"))
    except ValueError:
        return None


def _opt_float(parent: ET.Element, tag: str) -> Optional[float]:
    el = parent.find(tag)
    if el is None or el.get("Value") is None:
        return None
    try:
        return float(el.get("Value"))
    except ValueError:
        return None


def convert_steps(neware_steps: list[_NewareStep], warnings: list[str]) -> list[dict]:
    """Translate Neware steps into schedule.py-format step dicts."""
    out: list[dict] = []
    for ns in neware_steps:
        if ns.step_type in TERMINATOR_STEP_TYPES:
            break
        if ns.step_type not in STEP_TYPE_MAP:
            warnings.append(
                f"Step {ns.step_id}: unknown Step_Type={ns.step_type}, skipped"
            )
            continue

        expected_state, default_name = STEP_TYPE_MAP[ns.step_type]

        if ns.time_ms is None or ns.time_ms <= 0:
            warnings.append(
                f"Step {ns.step_id}: missing/zero Time, defaulting to 600 s"
            )
            timeout_s = 600.0
        else:
            timeout_s = ns.time_ms / 1000.0

        step = {
            "name": _build_step_name(ns, default_name),
            "expected_state": expected_state,
            "circuit_action": _circuit_action_for_state(expected_state),
            "timeout_s": round(timeout_s, 3),
        }
        if step["circuit_action"] == "charge":
            step["sequence"] = 1
            step["frequency"] = 10.0

        meta = _build_meta(ns)
        if meta:
            step["neware_meta"] = meta

        out.append(step)
    return out


def _build_step_name(ns: _NewareStep, default: str) -> str:
    """Construct a descriptive human-readable step name."""
    parts = [default]
    if ns.curr_ma is not None and ns.step_type in (1, 2, 7):
        parts.append(f"@{ns.curr_ma:.2f}mA")
    if ns.step_type == 7 and ns.volt_01mV is not None:
        parts.append(f"CV@{ns.volt_01mV/10000.0:.2f}V")
    if ns.stop_volt_01mV is not None and ns.step_type in (1, 2):
        parts.append(f"→{ns.stop_volt_01mV/10000.0:.2f}V")
    if ns.step_type == 4 and ns.time_ms is not None:
        mins = ns.time_ms / 60000.0
        parts.append(f"({_fmt_dur(mins)})")
    return " ".join(parts)


def _fmt_dur(minutes: float) -> str:
    if minutes >= 60:
        h = minutes / 60.0
        return f"{h:g}h" if h == int(h) else f"{h:.1f}h"
    return f"{minutes:g}min"


def _build_meta(ns: _NewareStep) -> dict:
    meta: dict = {}
    if ns.curr_ma is not None:
        meta["neware_curr_a"] = round(ns.curr_ma / 1000.0, 6)
    if ns.stop_volt_01mV is not None:
        meta["neware_stop_volt_v"] = round(ns.stop_volt_01mV / 10000.0, 4)
    if ns.stop_curr_ma is not None:
        meta["neware_stop_curr_a"] = round(ns.stop_curr_ma / 1000.0, 6)
    if ns.step_type == 7 and ns.volt_01mV is not None:
        meta["neware_cv_target_v"] = round(ns.volt_01mV / 10000.0, 4)
    return meta


def _circuit_action_for_state(state: str) -> str:
    """Pick a circuit_action that satisfies the schedule validator. The
    monitor doesn't actually use circuit_action — these are vestigial
    AutoEngine fields kept for back-compat with the schema."""
    return {
        "cc_charge": "charge",
        "cv_charge": "charge",
        "discharge": "discharge",
        "rest": "idle",
    }.get(state, "idle")


def neware_to_schedule_json(
    xml_path: Path,
    name: Optional[str] = None,
) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    neware_steps = parse_neware_xml(xml_path)
    if not neware_steps:
        raise ValueError(f"No steps parsed from {xml_path}")

    json_steps = convert_steps(neware_steps, warnings)
    if not json_steps:
        raise ValueError(f"No convertible steps in {xml_path}")

    sched = {
        "name": name or xml_path.stem,
        "description": f"Imported from Neware XML: {xml_path.name}",
        "steps": json_steps,
        "repeat": 1,
    }
    return sched, warnings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Convert Neware BTS Step File XML to schedule JSON",
    )
    p.add_argument("input_xml", type=Path, help="Input Neware XML file")
    p.add_argument("output_json", type=Path, nargs="?", default=None,
                   help="Output JSON (default: schedules/<input_stem>.json)")
    p.add_argument("--name", default=None,
                   help="Schedule name (default: input filename stem)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Verbose output")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.input_xml.exists():
        log.error("Input file not found: %s", args.input_xml)
        return 1

    output = args.output_json
    if output is None:
        output = Path("schedules") / f"{args.input_xml.stem}.json"

    try:
        sched, warnings = neware_to_schedule_json(args.input_xml, name=args.name)
    except Exception as e:
        log.error("Conversion failed: %s", e)
        return 1

    for w in warnings:
        log.warning(w)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(sched, f, indent=2)

    log.info("Wrote %d-step schedule to %s", len(sched["steps"]), output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
