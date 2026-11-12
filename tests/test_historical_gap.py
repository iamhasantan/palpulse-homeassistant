"""Regression checks for historical energy gap preview, no HA runtime needed."""
import unittest

from custom_components.palpulse.historical_gap import plan_recoverable_gaps


def row(hour, state, total):
    return {"start": hour * 3600, "state": state, "sum": total}


class HistoricalGapTests(unittest.TestCase):
    def test_isolated_lost_hour_is_counter_verified(self):
        rows = [
            row(0, 10, 100), row(1, 11, 101),
            # Hour 2 missing. Hour 3 only added its own 1 kWh to Recorder.
            row(3, 15, 102), row(4, 16, 103),
        ]
        plans = plan_recoverable_gaps(rows)
        self.assertEqual(len(plans), 1)
        p = plans[0]
        self.assertEqual(p.gap_start, 2 * 3600)
        self.assertAlmostEqual(p.missing_kwh, 3)
        self.assertAlmostEqual(p.inferred_gap_state, 14)
        self.assertAlmostEqual(p.inferred_gap_sum, 104)
        self.assertAlmostEqual(p.adjusted_following_sum, 105)

    def test_refuse_multiple_missing_hours(self):
        self.assertEqual(plan_recoverable_gaps([
            row(0, 10, 100), row(1, 11, 101), row(4, 16, 102),
            row(5, 17, 103),
        ]), [])

    def test_refuse_counter_reset(self):
        self.assertEqual(plan_recoverable_gaps([
            row(0, 10, 100), row(1, 11, 101), row(3, 2, 102),
            row(4, 3, 103),
        ]), [])

    def test_refuse_nonconforming_neighbour(self):
        self.assertEqual(plan_recoverable_gaps([
            row(0, 10, 100), row(1, 11, 101), row(3, 15, 102),
            row(4, 16, 104),
        ]), [])

    def test_already_corrected_skipped_hour_not_repaired_twice(self):
        self.assertEqual(plan_recoverable_gaps([
            row(0, 10, 100), row(1, 11, 101), row(3, 15, 105),
            row(4, 16, 106),
        ]), [])

    def test_refuse_negative_or_nonfinite_sum(self):
        self.assertEqual(plan_recoverable_gaps([
            row(0, 10, 100), row(1, 11, 101), row(3, 15, float("nan")),
            row(4, 16, 103),
        ]), [])
        self.assertEqual(plan_recoverable_gaps([
            row(0, 10, 100), row(1, 11, 101), row(3, 15, 100),
            row(4, 16, 102),
        ]), [])

    def test_reject_duplicate_timestamps(self):
        self.assertEqual(plan_recoverable_gaps([
            row(0, 10, 100), row(1, 11, 101), row(3, 15, 102),
            row(3, 15, 102), row(4, 16, 103),
        ]), [])


if __name__ == "__main__":
    unittest.main()
