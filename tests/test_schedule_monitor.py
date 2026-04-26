"""Tests for the passive ScheduleMonitor (PLAN clock + OBSERVED tracker)."""

import unittest
from time import monotonic
from unittest.mock import patch

from server.schedule import Schedule, ScheduleStep
from server.schedule_monitor import ScheduleMonitor


def _sensor(i_a: float = 0.0, v: float = 3.7):
    return {
        "P1": {"voltage": v, "current": i_a},
        "P2": {"voltage": 0.0, "current": 0.0},
        "N1": {"voltage": v, "current": i_a},
        "N2": {"voltage": 0.0, "current": 0.0},
    }


def _two_step_schedule(repeat: int = 1):
    return Schedule(
        name="test",
        steps=[
            ScheduleStep("Charge", "cc_charge", "charge", timeout_s=10.0),
            ScheduleStep("Rest",   "rest",      "idle",   timeout_s=5.0),
        ],
        repeat=repeat,
    )


class TestScheduleMonitorIdle(unittest.TestCase):

    def test_no_schedule_returns_unloaded_status(self):
        mon = ScheduleMonitor(get_sensor_data_fn=lambda: {})
        s = mon.get_status()
        self.assertFalse(s["loaded"])
        self.assertFalse(s["running"])

    def test_load_starts_plan_clock(self):
        mon = ScheduleMonitor(get_sensor_data_fn=lambda: {})
        mon.load_schedule(_two_step_schedule())
        s = mon.get_status()
        self.assertTrue(s["loaded"])
        self.assertTrue(s["running"])
        self.assertEqual(s["plan"]["step_index"], 0)


class TestScheduleMonitorPlanClock(unittest.TestCase):

    def setUp(self):
        self.mon = ScheduleMonitor(get_sensor_data_fn=lambda: {})
        self.mon.load_schedule(_two_step_schedule(repeat=2))

    def _at(self, dt: float) -> dict:
        """Return status as if `dt` seconds have elapsed since start."""
        with patch("server.schedule_monitor.monotonic",
                   return_value=self.mon._start_time + dt):
            return self.mon.get_status()

    def test_step0_at_t0(self):
        s = self._at(0.0)
        self.assertEqual(s["plan"]["step_index"], 0)
        self.assertEqual(s["plan"]["expected_state"], "cc_charge")
        self.assertEqual(s["plan"]["cycle"], 0)
        self.assertFalse(s["plan"]["schedule_complete"])

    def test_still_step0_just_before_boundary(self):
        s = self._at(9.5)
        self.assertEqual(s["plan"]["step_index"], 0)

    def test_step1_after_first_timeout(self):
        s = self._at(10.5)
        self.assertEqual(s["plan"]["step_index"], 1)
        self.assertEqual(s["plan"]["expected_state"], "rest")
        self.assertEqual(s["plan"]["cycle"], 0)

    def test_cycle_advances(self):
        # Cycle duration is 15s (10+5). At t=16 we're 1s into step 0 of cycle 1.
        s = self._at(16.0)
        self.assertEqual(s["plan"]["cycle"], 1)
        self.assertEqual(s["plan"]["step_index"], 0)
        self.assertAlmostEqual(s["plan"]["step_elapsed_s"], 1.0, places=1)

    def test_complete_after_total(self):
        s = self._at(31.0)  # past total 30s
        self.assertTrue(s["plan"]["schedule_complete"])

    def test_restart_resets_clock(self):
        # Advance to step 1
        with patch("server.schedule_monitor.monotonic",
                   return_value=self.mon._start_time + 12.0):
            s = self.mon.get_status()
            self.assertEqual(s["plan"]["step_index"], 1)
        # Restart and re-check
        self.mon.restart()
        s = self.mon.get_status()
        self.assertEqual(s["plan"]["step_index"], 0)

    def test_stop_freezes_clock_at_step0(self):
        self.mon.stop()
        s = self.mon.get_status()
        self.assertFalse(s["running"])
        self.assertEqual(s["plan"]["step_index"], 0)


class TestScheduleMonitorObserved(unittest.TestCase):

    def test_observed_updates_via_tick(self):
        sensor = {"data": _sensor(0.0)}
        mon = ScheduleMonitor(get_sensor_data_fn=lambda: sensor["data"])
        mon.load_schedule(_two_step_schedule())

        # Feed enough samples that the detector debounces to a state
        sensor["data"] = _sensor(0.100)
        for _ in range(10):
            mon._tick()
        s = mon.get_status()
        self.assertEqual(s["observed"]["state"], "cc_charge")
        self.assertAlmostEqual(s["observed"]["current_a"], 0.100, places=3)


class TestScheduleMonitorDivergence(unittest.TestCase):

    def test_match_when_observed_equals_expected(self):
        mon = ScheduleMonitor(get_sensor_data_fn=lambda: _sensor(0.100))
        mon.load_schedule(_two_step_schedule())
        for _ in range(10):
            mon._tick()
        s = mon.get_status()
        self.assertEqual(s["divergence"], "match")  # cc_charge expected, cc_charge observed

    def test_mismatch_when_observed_differs(self):
        mon = ScheduleMonitor(get_sensor_data_fn=lambda: _sensor(-0.100))
        mon.load_schedule(_two_step_schedule())
        for _ in range(10):
            mon._tick()
        s = mon.get_status()
        # Step 0 expects cc_charge, but cycler is discharging
        self.assertEqual(s["divergence"], "mismatch")

    def test_unknown_when_observed_is_unknown(self):
        mon = ScheduleMonitor(get_sensor_data_fn=lambda: _sensor(0.006))  # in dead zone
        mon.load_schedule(_two_step_schedule())
        for _ in range(10):
            mon._tick()
        s = mon.get_status()
        self.assertEqual(s["divergence"], "unknown")


if __name__ == "__main__":
    unittest.main()
