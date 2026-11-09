"""Exact November 9 Powerpal negative sum regression, read-only."""

from pathlib import Path
import importlib.util
import sys
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components/palpulse/energy_integrity.py"
)
spec = importlib.util.spec_from_file_location("palpulse_energy_integrity", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class EnergyIntegrityTests(unittest.TestCase):
    def test_november_9_2026_sum_reset_is_detected_exactly(self):
        # 1-2pm and 2-3pm Melbourne: 02:00 and 03:00 UTC.
        hourly = [
            {"start": 1794189600, "state": 153.36625, "sum": 11295.4219000096},
            {"start": 1794193200, "state": 156.606875, "sum": 119.565},
            {"start": 1794196800, "state": 160.358125, "sum": 123.31625},
        ]
        issues = module.detect_large_energy_resets(hourly)
        self.assertEqual(len(issues), 1)
        item = issues[0]
        self.assertEqual(item.hour_start, 1794193200)
        self.assertAlmostEqual(item.recorded_delta, -11175.8569000096)
        self.assertAlmostEqual(item.meter_delta, 3.240625)
        self.assertAlmostEqual(item.correction, 11179.0975250096)

    def test_normal_gaps_and_small_legacy_negative_steps_are_not_targeted(self):
        rows = [
            {"start": 100 * 3600, "state": 100.0, "sum": 25},
            {"start": 101 * 3600, "state": 101.0, "sum": 24.99},
            {"start": 103 * 3600, "state": 104.0, "sum": 25.1},
        ]
        self.assertFalse(module.detect_large_energy_resets(rows))

    def test_corrupt_nan_and_actual_counter_reset_are_not_automatically_flagged(self):
        rows = [
            {"start": 100 * 3600, "state": 10.0, "sum": 1000.0},
            {"start": 101 * 3600, "state": float("nan"), "sum": 0.0},
            {"start": 102 * 3600, "state": 5.0, "sum": -1000},
            {"start": 103 * 3600, "state": 4.0, "sum": -3000},
        ]
        self.assertFalse(module.detect_large_energy_resets(rows))


if __name__ == "__main__":
    unittest.main()
