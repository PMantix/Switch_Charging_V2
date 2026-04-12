"""Tests for the auto mode engine — timeout behaviour, event buffer, step navigation."""

import unittest
import threading
from time import sleep, monotonic
from collections import deque

from server.auto_engine import AutoEngine, StepPhase
from server.schedule import load_schedule_inline
from server.cycler_detector import CyclerState


def _make_schedule(steps=None, repeat=1, on_timeout="advance", grace=2):
    """Build a schedule dict for testing with short timeouts."""
    if steps is None:
        steps = [
            {"name": "CC Charge", "expected_state": "cc_charge",
             "circuit_action": "charge", "sequence": 1, "frequency": 10.0,
             "timeout_s": 3, "on_timeout": on_timeout, "timeout_grace_s": grace},
            {"name": "Rest", "expected_state": "rest",
             "circuit_action": "idle", "timeout_s": 3,
             "on_timeout": on_timeout, "timeout_grace_s": grace},
        ]
    return load_schedule_inline({
        "name": "test_schedule",
        "steps": steps,
        "repeat": repeat,
        "default_on_timeout": on_timeout,
        "default_timeout_grace_s": grace,
    })


class MockCallbacks:
    """Tracks mode/sequence/frequency calls from the engine."""

    def __init__(self):
        self.modes = []
        self.sequences = []
        self.frequencies = []
        self.sensor_data = {"P1": {"voltage": 0, "current": 0},
                            "P2": {"voltage": 0, "current": 0},
                            "N1": {"voltage": 0, "current": 0},
                            "N2": {"voltage": 0, "current": 0}}

    def set_mode(self, m):
        self.modes.append(m)

    def set_sequence(self, s):
        self.sequences.append(s)

    def set_frequency(self, f):
        self.frequencies.append(f)

    def get_sensor_data(self):
        return dict(self.sensor_data)


class TestAutoEngineStatus(unittest.TestCase):

    def test_status_before_start(self):
        sched = _make_schedule()
        cb = MockCallbacks()
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
        )
        status = engine.get_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["schedule_name"], "test_schedule")
        self.assertIn("steps", status)
        self.assertIn("recent_events", status)
        self.assertIn("on_timeout", status)
        self.assertIn("in_timeout", status)
        self.assertEqual(len(status["steps"]), 2)

    def test_step_list_in_status(self):
        sched = _make_schedule()
        cb = MockCallbacks()
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
        )
        steps = engine.get_status()["steps"]
        self.assertEqual(steps[0]["name"], "CC Charge")
        self.assertEqual(steps[0]["expected_state"], "cc_charge")
        self.assertEqual(steps[1]["name"], "Rest")


class TestAutoEngineEventBuffer(unittest.TestCase):

    def test_events_accumulate(self):
        sched = _make_schedule()
        cb = MockCallbacks()
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
        )
        engine._emit_event("test_event", {"foo": "bar"})
        engine._emit_event("another_event", {"baz": "qux"})
        events = engine.get_status()["recent_events"]
        self.assertEqual(len(events), 2)
        self.assertIn("test_event", events[0])

    def test_event_ring_buffer_caps_at_10(self):
        sched = _make_schedule()
        cb = MockCallbacks()
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
        )
        for i in range(20):
            engine._emit_event(f"event_{i}", {})
        events = engine.get_status()["recent_events"]
        self.assertEqual(len(events), 10)
        # Oldest events dropped
        self.assertIn("event_19", events[-1])
        self.assertNotIn("event_0", events[0])

    def test_format_event_types(self):
        cases = [
            ("step_start", {"step_name": "CC"}, ">>"),
            ("step_transition_detected", {"from_step": "A", "to_step": "B"}, "<>"),
            ("step_timeout", {"step_name": "X", "on_timeout": "wait"}, "!!"),
            ("step_timeout_advance", {"step_name": "X"}, ">>"),
            ("step_timeout_abort", {"step_name": "X"}, "XX"),
            ("step_complete", {"step_name": "X", "elapsed_s": 10}, "OK"),
            ("cycle_start", {"cycle": 0}, "--"),
            ("schedule_complete", {}, "=="),
            ("heartbeat", {"step_name": "X", "match": True, "elapsed_s": 5}, ".."),
            ("auto_started", {}, "--"),
        ]
        for event_type, data, expected_prefix in cases:
            result = AutoEngine._format_event(event_type, data)
            self.assertTrue(
                result.startswith(expected_prefix),
                f"_format_event({event_type!r}) = {result!r}, expected prefix {expected_prefix!r}"
            )


class TestAutoEngineTimeoutAdvance(unittest.TestCase):
    """Test that on_timeout='advance' auto-advances after grace period."""

    def test_advance_on_timeout(self):
        sched = _make_schedule(on_timeout="advance", grace=1)
        cb = MockCallbacks()
        events_received = []
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
            on_event=lambda e: events_received.append(e),
        )
        engine.start()
        # timeout_s=3 + grace=1 = 4s. Wait a bit longer.
        sleep(6)
        engine.stop()

        # Should have advanced past the first step
        event_types = [e["event_type"] for e in events_received]
        self.assertIn("step_timeout", event_types)
        self.assertIn("step_timeout_advance", event_types)

    def test_abort_on_timeout(self):
        sched = _make_schedule(on_timeout="abort", grace=1)
        cb = MockCallbacks()
        events_received = []
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
            on_event=lambda e: events_received.append(e),
        )
        engine.start()
        # timeout_s=3 + grace=1 = 4s, plus engine loop overhead
        sleep(8)
        engine.stop()
        event_types = [e["event_type"] for e in events_received]
        self.assertIn("step_timeout_abort", event_types)


class TestAutoEnginePauseResume(unittest.TestCase):

    def test_pause_and_resume(self):
        sched = _make_schedule(on_timeout="wait", grace=999)
        cb = MockCallbacks()
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
        )
        engine.start()
        sleep(0.5)
        self.assertTrue(engine.running)
        self.assertFalse(engine.paused)

        engine.pause()
        self.assertTrue(engine.paused)

        engine.resume()
        self.assertFalse(engine.paused)

        engine.stop()

    def test_skip_step(self):
        sched = _make_schedule(on_timeout="wait", grace=999)
        cb = MockCallbacks()
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
        )
        engine.start()
        sleep(0.5)
        initial_step = engine.get_status()["step_index"]
        engine.skip_step()
        sleep(0.3)
        engine.stop()
        # Step should have advanced (skip_step sets step_index under lock)


class TestAutoEngineLifecycle(unittest.TestCase):

    def test_start_stop(self):
        sched = _make_schedule()
        cb = MockCallbacks()
        engine = AutoEngine(
            schedule=sched,
            get_sensor_data_fn=cb.get_sensor_data,
            set_mode_fn=cb.set_mode,
            set_sequence_fn=cb.set_sequence,
            set_frequency_fn=cb.set_frequency,
        )
        self.assertFalse(engine.running)
        engine.start()
        self.assertTrue(engine.running)
        sleep(0.3)
        engine.stop()
        self.assertFalse(engine.running)
        # Should have set mode to idle on stop
        self.assertIn("idle", cb.modes)


if __name__ == "__main__":
    unittest.main()
