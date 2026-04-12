"""Tests for schedule parsing, validation, and semantic checks."""

import json
import os
import tempfile
import unittest

from server.schedule import (
    Schedule, ScheduleStep, load_schedule, load_schedule_inline,
    validate_schedule, validate_schedule_semantics,
    VALID_ON_TIMEOUT, VALID_SEMANTIC_PAIRS,
)
from server.cycler_detector import DetectionThresholds


class TestScheduleStepDefaults(unittest.TestCase):

    def test_default_on_timeout(self):
        s = ScheduleStep(name="t", expected_state="rest", circuit_action="idle", timeout_s=60)
        self.assertEqual(s.on_timeout, "wait")

    def test_default_grace_auto_calc(self):
        s = ScheduleStep(name="t", expected_state="rest", circuit_action="idle", timeout_s=100)
        self.assertEqual(s.effective_grace(), 20.0)  # 100 * 0.2

    def test_grace_capped_at_120(self):
        s = ScheduleStep(name="t", expected_state="rest", circuit_action="idle", timeout_s=1000)
        self.assertEqual(s.effective_grace(), 120.0)  # min(200, 120)

    def test_explicit_grace(self):
        s = ScheduleStep(name="t", expected_state="rest", circuit_action="idle",
                         timeout_s=100, timeout_grace_s=5)
        self.assertEqual(s.effective_grace(), 5.0)

    def test_expected_cycler_state(self):
        from server.cycler_detector import CyclerState
        s = ScheduleStep(name="t", expected_state="cc_charge", circuit_action="charge", timeout_s=60)
        self.assertEqual(s.expected_cycler_state(), CyclerState.CC_CHARGE)


class TestScheduleValidation(unittest.TestCase):

    def _make_schedule(self, **kwargs):
        defaults = dict(
            name="test",
            steps=[ScheduleStep(name="s1", expected_state="rest",
                                circuit_action="idle", timeout_s=60)],
        )
        defaults.update(kwargs)
        return Schedule(**defaults)

    def test_valid_schedule_no_errors(self):
        s = self._make_schedule()
        self.assertEqual(validate_schedule(s), [])

    def test_missing_name(self):
        s = self._make_schedule(name="")
        errors = validate_schedule(s)
        self.assertTrue(any("name" in e for e in errors))

    def test_empty_steps(self):
        s = self._make_schedule(steps=[])
        errors = validate_schedule(s)
        self.assertTrue(any("at least one step" in e for e in errors))

    def test_invalid_expected_state(self):
        s = self._make_schedule(steps=[
            ScheduleStep(name="bad", expected_state="bogus",
                         circuit_action="idle", timeout_s=60),
        ])
        errors = validate_schedule(s)
        self.assertTrue(any("expected_state" in e for e in errors))

    def test_invalid_circuit_action(self):
        s = self._make_schedule(steps=[
            ScheduleStep(name="bad", expected_state="rest",
                         circuit_action="bogus", timeout_s=60),
        ])
        errors = validate_schedule(s)
        self.assertTrue(any("circuit_action" in e for e in errors))

    def test_negative_timeout(self):
        s = self._make_schedule(steps=[
            ScheduleStep(name="bad", expected_state="rest",
                         circuit_action="idle", timeout_s=-1),
        ])
        errors = validate_schedule(s)
        self.assertTrue(any("timeout_s" in e for e in errors))

    def test_charge_sequence_out_of_range(self):
        s = self._make_schedule(steps=[
            ScheduleStep(name="bad", expected_state="cc_charge",
                         circuit_action="charge", timeout_s=60, sequence=99),
        ])
        errors = validate_schedule(s)
        self.assertTrue(any("sequence" in e for e in errors))

    def test_invalid_on_timeout(self):
        s = self._make_schedule(steps=[
            ScheduleStep(name="bad", expected_state="rest",
                         circuit_action="idle", timeout_s=60, on_timeout="explode"),
        ])
        errors = validate_schedule(s)
        self.assertTrue(any("on_timeout" in e for e in errors))

    def test_valid_on_timeout_values(self):
        for val in VALID_ON_TIMEOUT:
            s = self._make_schedule(steps=[
                ScheduleStep(name="ok", expected_state="rest",
                             circuit_action="idle", timeout_s=60, on_timeout=val),
            ])
            self.assertEqual(validate_schedule(s), [])


class TestSemanticValidation(unittest.TestCase):

    def test_contradictory_state_action_warns(self):
        s = Schedule(name="test", steps=[
            ScheduleStep(name="bad", expected_state="cc_charge",
                         circuit_action="idle", timeout_s=60),
        ])
        warnings = validate_schedule_semantics(s)
        self.assertEqual(len(warnings), 1)
        self.assertIn("contradictory", warnings[0])

    def test_valid_pairs_no_warnings(self):
        for state, actions in VALID_SEMANTIC_PAIRS.items():
            for action in actions:
                s = Schedule(name="test", steps=[
                    ScheduleStep(name="ok", expected_state=state,
                                 circuit_action=action, timeout_s=60),
                ])
                warnings = validate_schedule_semantics(s)
                self.assertEqual(warnings, [],
                                 f"{state}+{action} should not warn")

    def test_adjacent_duplicate_states_warn(self):
        s = Schedule(name="test", steps=[
            ScheduleStep(name="s1", expected_state="rest",
                         circuit_action="idle", timeout_s=60),
            ScheduleStep(name="s2", expected_state="rest",
                         circuit_action="idle", timeout_s=60),
        ])
        warnings = validate_schedule_semantics(s)
        self.assertEqual(len(warnings), 1)
        self.assertIn("both expect", warnings[0])

    def test_non_adjacent_duplicates_ok(self):
        s = Schedule(name="test", steps=[
            ScheduleStep(name="s1", expected_state="rest",
                         circuit_action="idle", timeout_s=60),
            ScheduleStep(name="s2", expected_state="cc_charge",
                         circuit_action="charge", timeout_s=60),
            ScheduleStep(name="s3", expected_state="rest",
                         circuit_action="idle", timeout_s=60),
        ])
        warnings = validate_schedule_semantics(s)
        self.assertEqual(warnings, [])


class TestScheduleLoading(unittest.TestCase):

    def test_load_example_cccv(self):
        s = load_schedule("schedules/example_cccv.json")
        self.assertEqual(s.name, "CCCV_cycle_test")
        self.assertEqual(len(s.steps), 5)
        self.assertEqual(s.repeat, 50)
        self.assertEqual(s.default_on_timeout, "advance")
        self.assertEqual(s.default_timeout_grace_s, 60)
        # Steps inherit schedule-level on_timeout
        self.assertEqual(s.steps[0].on_timeout, "advance")

    def test_load_example_short(self):
        s = load_schedule("schedules/example_short_test.json")
        self.assertEqual(s.name, "short_bench_test")
        self.assertEqual(len(s.steps), 4)
        self.assertEqual(s.repeat, 3)

    def test_load_example_multistage(self):
        s = load_schedule("schedules/example_multistage.json")
        self.assertEqual(s.name, "multistage_formation")
        self.assertEqual(len(s.steps), 11)
        self.assertEqual(s.repeat, 5)
        # Pulse Discharge has per-step grace override
        pulse_step = s.steps[4]
        self.assertEqual(pulse_step.name, "Pulse Discharge")
        self.assertEqual(pulse_step.timeout_grace_s, 10)
        self.assertEqual(pulse_step.effective_grace(), 10)

    def test_load_inline(self):
        raw = {
            "name": "inline_test",
            "steps": [
                {"name": "Rest", "expected_state": "rest",
                 "circuit_action": "idle", "timeout_s": 30},
            ],
        }
        s = load_schedule_inline(raw)
        self.assertEqual(s.name, "inline_test")
        self.assertEqual(len(s.steps), 1)

    def test_load_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_schedule("nonexistent.json")

    def test_load_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            f.flush()
            try:
                with self.assertRaises(Exception):
                    load_schedule(f.name)
            finally:
                os.unlink(f.name)

    def test_custom_detection_thresholds(self):
        s = load_schedule("schedules/example_cccv.json")
        self.assertEqual(s.detection_thresholds.rest_threshold, 0.005)
        self.assertEqual(s.detection_thresholds.charge_min, 0.008)


class TestScheduleDefaults(unittest.TestCase):

    def test_schedule_level_on_timeout_propagates(self):
        raw = {
            "name": "test",
            "default_on_timeout": "abort",
            "default_timeout_grace_s": 45,
            "steps": [
                {"name": "s1", "expected_state": "rest",
                 "circuit_action": "idle", "timeout_s": 60},
            ],
        }
        s = load_schedule_inline(raw)
        self.assertEqual(s.steps[0].on_timeout, "abort")
        self.assertEqual(s.steps[0].timeout_grace_s, 45)

    def test_per_step_overrides_schedule_default(self):
        raw = {
            "name": "test",
            "default_on_timeout": "abort",
            "steps": [
                {"name": "s1", "expected_state": "rest",
                 "circuit_action": "idle", "timeout_s": 60,
                 "on_timeout": "wait"},
            ],
        }
        s = load_schedule_inline(raw)
        self.assertEqual(s.steps[0].on_timeout, "wait")


if __name__ == "__main__":
    unittest.main()
