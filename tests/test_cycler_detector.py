"""Tests for the cycler state detector — classification, debouncing, CV detection, hysteresis."""

import unittest
from time import sleep
from server.cycler_detector import (
    CyclerDetector, CyclerState, DetectionThresholds, DetectionResult,
)


def _sensor(v=3.5, i=0.0):
    """Build a sensor_data dict with uniform readings across all 4 channels."""
    return {
        "P1": {"voltage": v, "current": i},
        "P2": {"voltage": v, "current": i},
        "N1": {"voltage": v, "current": i},
        "N2": {"voltage": v, "current": i},
    }


def _feed_n(det, sensor_data, n=10):
    """Feed n identical samples and return the last result."""
    r = None
    for _ in range(n):
        r = det.feed(sensor_data)
    return r


class TestBasicClassification(unittest.TestCase):
    """Test that each cycler state is correctly classified from current/voltage."""

    def setUp(self):
        self.det = CyclerDetector(DetectionThresholds(debounce_count=3))

    def test_rest_detected_near_zero_current(self):
        r = _feed_n(self.det, _sensor(i=0.001), 10)
        self.assertEqual(r.state, CyclerState.REST)

    def test_rest_detected_negative_small_current(self):
        r = _feed_n(self.det, _sensor(i=-0.002), 10)
        self.assertEqual(r.state, CyclerState.REST)

    def test_cc_charge_detected_positive_current(self):
        r = _feed_n(self.det, _sensor(i=0.100), 10)
        self.assertEqual(r.state, CyclerState.CC_CHARGE)

    def test_discharge_detected_negative_current(self):
        r = _feed_n(self.det, _sensor(i=-0.100), 10)
        self.assertEqual(r.state, CyclerState.DISCHARGE)

    def test_unknown_in_dead_zone(self):
        """Current between rest_threshold and charge_min → UNKNOWN."""
        det = CyclerDetector(DetectionThresholds(
            rest_threshold=0.005, charge_min=0.010, debounce_count=3,
        ))
        r = _feed_n(det, _sensor(i=0.007), 10)
        self.assertEqual(r.state, CyclerState.UNKNOWN)

    def test_zero_voltage_sensors_excluded(self):
        """Sensors with voltage < 0.01 are ignored (FET path inactive)."""
        data = {
            "P1": {"voltage": 3.5, "current": 0.100},
            "P2": {"voltage": 0.0, "current": 0.0},  # inactive
            "N1": {"voltage": 3.5, "current": 0.100},
            "N2": {"voltage": 0.0, "current": 0.0},  # inactive
        }
        r = _feed_n(self.det, data, 10)
        self.assertEqual(r.state, CyclerState.CC_CHARGE)
        self.assertAlmostEqual(r.avg_current, 0.100, places=3)

    def test_empty_sensor_data(self):
        r = _feed_n(self.det, {}, 10)
        # No data → zero current → REST (or UNKNOWN depending on init)
        self.assertIn(r.state, (CyclerState.REST, CyclerState.UNKNOWN))

    def test_sensor_with_error_excluded(self):
        data = {
            "P1": {"voltage": 3.5, "current": 0.100},
            "P2": {"error": "timeout"},
            "N1": {"voltage": 3.5, "current": 0.100},
            "N2": {"error": "timeout"},
        }
        r = _feed_n(self.det, data, 10)
        self.assertEqual(r.state, CyclerState.CC_CHARGE)


class TestDebouncing(unittest.TestCase):
    """Test that state changes require N consecutive agreeing samples."""

    def test_debounce_prevents_premature_transition(self):
        det = CyclerDetector(DetectionThresholds(debounce_count=5))
        # Start at REST
        _feed_n(det, _sensor(i=0.001), 10)
        self.assertEqual(det.get_state().state, CyclerState.REST)

        # Feed only 3 CC_CHARGE samples (below debounce_count=5)
        for _ in range(3):
            det.feed(_sensor(i=0.100))
        self.assertEqual(det.get_state().state, CyclerState.REST)  # still REST

    def test_debounce_allows_transition_after_count(self):
        det = CyclerDetector(DetectionThresholds(debounce_count=5))
        _feed_n(det, _sensor(i=0.001), 10)
        self.assertEqual(det.get_state().state, CyclerState.REST)

        _feed_n(det, _sensor(i=0.100), 6)
        self.assertEqual(det.get_state().state, CyclerState.CC_CHARGE)

    def test_noise_spike_does_not_flip_state(self):
        det = CyclerDetector(DetectionThresholds(debounce_count=5))
        # Use rising voltage so CV plateau check doesn't falsely trigger
        for i in range(10):
            det.feed(_sensor(v=3.5 + i * 0.02, i=0.100))
        self.assertEqual(det.get_state().state, CyclerState.CC_CHARGE)

        # Single noise spike to REST
        det.feed(_sensor(v=3.7, i=0.001))
        self.assertEqual(det.get_state().state, CyclerState.CC_CHARGE)

        # Resume normal
        for i in range(5):
            det.feed(_sensor(v=3.7 + i * 0.02, i=0.100))
        self.assertEqual(det.get_state().state, CyclerState.CC_CHARGE)


class TestCVDetection(unittest.TestCase):
    """Test the multi-criteria CV detection and hysteresis."""

    def _make_detector(self):
        return CyclerDetector(DetectionThresholds(
            debounce_count=3,
            cv_window_s=1.0,
            cv_split_window_s=0.5,
            cv_decline_rate=-0.0005,
            cv_voltage_plateau_range=0.020,
            cv_current_drop_ratio=0.15,
            cv_hysteresis_exit_slope=0.002,
        ))

    def test_steady_current_is_cc_not_cv(self):
        """Constant current + rising voltage → CC_CHARGE, not CV."""
        det = self._make_detector()
        for i in range(30):
            det.feed(_sensor(v=3.5 + i * 0.01, i=0.500))
            sleep(0.04)
        self.assertEqual(det.get_state().state, CyclerState.CC_CHARGE)

    def test_declining_current_stable_voltage_is_cv(self):
        """Declining current + stable voltage → CV_CHARGE."""
        det = self._make_detector()
        # Start with CC
        for i in range(20):
            det.feed(_sensor(v=4.20, i=0.500))
            sleep(0.04)
        self.assertEqual(det.get_state().state, CyclerState.CC_CHARGE)

        # Transition: current declines, voltage stable at 4.20
        for i in range(30):
            current = 0.500 - i * 0.015  # drops from 0.5 to ~0.05
            det.feed(_sensor(v=4.20, i=max(0.01, current)))
            sleep(0.04)
        self.assertEqual(det.get_state().state, CyclerState.CV_CHARGE)

    def test_step_change_in_current_detected_as_cv(self):
        """Abrupt current drop (step-change) with stable voltage → CV via drop ratio."""
        det = self._make_detector()
        # CC phase at 0.500A
        for _ in range(20):
            det.feed(_sensor(v=4.20, i=0.500))
            sleep(0.04)
        self.assertEqual(det.get_state().state, CyclerState.CC_CHARGE)

        # Abrupt step to 0.200A (60% drop) with stable voltage
        for _ in range(20):
            det.feed(_sensor(v=4.20, i=0.200))
            sleep(0.04)
        self.assertEqual(det.get_state().state, CyclerState.CV_CHARGE)

    def test_cv_hysteresis_resists_noise(self):
        """Once in CV, brief current increase should NOT flip back to CC."""
        det = self._make_detector()
        # Get into CV state
        for _ in range(15):
            det.feed(_sensor(v=4.20, i=0.500))
            sleep(0.04)
        for i in range(20):
            det.feed(_sensor(v=4.20, i=max(0.01, 0.500 - i * 0.020)))
            sleep(0.04)
        self.assertEqual(det.get_state().state, CyclerState.CV_CHARGE)
        self.assertTrue(det._in_cv_phase)

        # Brief noise: current increases slightly
        for _ in range(2):
            det.feed(_sensor(v=4.20, i=0.120))
            sleep(0.04)
        self.assertEqual(det.get_state().state, CyclerState.CV_CHARGE)  # still CV

    def test_reset_clears_hysteresis(self):
        det = self._make_detector()
        det._in_cv_phase = True
        det.reset()
        self.assertFalse(det._in_cv_phase)
        self.assertEqual(det.get_state().state, CyclerState.UNKNOWN)


class TestStateTransitionSequence(unittest.TestCase):
    """Test a full REST → CC → CV → REST → DISCHARGE → REST cycle."""

    def test_full_cycle(self):
        det = CyclerDetector(DetectionThresholds(
            debounce_count=3,
            cv_window_s=0.8,
            cv_split_window_s=0.4,
            cv_current_drop_ratio=0.20,
            cv_voltage_plateau_range=0.020,
        ))
        states_seen = []

        def record():
            s = det.get_state().state
            if not states_seen or states_seen[-1] != s:
                states_seen.append(s)

        # REST (0 current)
        for _ in range(15):
            det.feed(_sensor(v=0.0, i=0.0))
            record()
            sleep(0.03)

        # CC CHARGE (steady 200mA, voltage climbing)
        for i in range(20):
            det.feed(_sensor(v=3.5 + i * 0.03, i=0.200))
            record()
            sleep(0.03)

        # CV CHARGE (current declines, voltage stable)
        for i in range(25):
            det.feed(_sensor(v=4.20, i=max(0.010, 0.200 - i * 0.008)))
            record()
            sleep(0.03)

        # REST
        for _ in range(15):
            det.feed(_sensor(v=4.15, i=0.001))
            record()
            sleep(0.03)

        # DISCHARGE (negative current)
        for _ in range(15):
            det.feed(_sensor(v=3.8, i=-0.200))
            record()
            sleep(0.03)

        # REST
        for _ in range(15):
            det.feed(_sensor(v=3.5, i=0.001))
            record()
            sleep(0.03)

        # Verify we saw all expected transitions in order
        self.assertIn(CyclerState.REST, states_seen)
        self.assertIn(CyclerState.CC_CHARGE, states_seen)
        self.assertIn(CyclerState.CV_CHARGE, states_seen)
        self.assertIn(CyclerState.DISCHARGE, states_seen)

        # Check ordering: CC before CV, CV before DISCHARGE
        cc_idx = states_seen.index(CyclerState.CC_CHARGE)
        cv_idx = states_seen.index(CyclerState.CV_CHARGE)
        dis_idx = states_seen.index(CyclerState.DISCHARGE)
        self.assertLess(cc_idx, cv_idx)
        self.assertLess(cv_idx, dis_idx)


class TestConfidence(unittest.TestCase):

    def test_confidence_is_high_during_steady_state(self):
        det = CyclerDetector(DetectionThresholds(debounce_count=3, window_size=10))
        r = _feed_n(det, _sensor(i=0.100), 15)
        self.assertGreaterEqual(r.confidence, 0.8)

    def test_confidence_drops_during_transition(self):
        det = CyclerDetector(DetectionThresholds(debounce_count=3, window_size=10))
        _feed_n(det, _sensor(i=0.001), 10)  # REST
        # Feed a mix
        det.feed(_sensor(i=0.100))  # CC
        det.feed(_sensor(i=0.100))  # CC
        r = det.feed(_sensor(i=0.001))  # REST
        # Confidence for REST should be reduced since window has mixed samples
        self.assertLess(r.confidence, 1.0)


class TestDetectionResult(unittest.TestCase):

    def test_result_fields(self):
        det = CyclerDetector()
        r = det.feed(_sensor(v=12.5, i=0.150))
        self.assertIsInstance(r, DetectionResult)
        self.assertAlmostEqual(r.avg_current, 0.150, places=3)
        self.assertAlmostEqual(r.avg_voltage, 12.5, places=1)
        self.assertGreater(r.timestamp, 0)

    def test_get_state_returns_last(self):
        det = CyclerDetector()
        r1 = det.feed(_sensor(i=0.100))
        r2 = det.get_state()
        self.assertEqual(r1.state, r2.state)
        self.assertEqual(r1.timestamp, r2.timestamp)


if __name__ == "__main__":
    unittest.main()
