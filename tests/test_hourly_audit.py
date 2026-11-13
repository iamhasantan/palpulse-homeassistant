"""Regression tests for read-only forward Recorder completeness audits."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "palpulse"))
from hourly_audit import HOUR, SETTLE_SECONDS, summarize_recent_hours  # noqa: E402


class RecentRecorderAuditTests(unittest.TestCase):
    def audit(self, energy, costs, *, due=12, last=12, enabled=True, unpriced=(), pending=()):
        return summarize_recent_hours(
            [{"start": h * HOUR} for h in energy],
            [{"start": h * HOUR} for h in costs],
            now=due * HOUR + SETTLE_SECONDS,
            last_completed_hour=None if last is None else last * HOUR,
            cost_enabled=enabled,
            unpriced_hours=[h * HOUR for h in unpriced],
            pending_cost_hours=[h * HOUR for h in pending],
        )

    def test_does_not_invent_preinstall_hours(self):
        result = self.audit([10, 11, 12], [10, 11, 12], last=12)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["checked_hours"], 3)
        self.assertEqual(result["missing_energy_count"], 0)
        self.assertEqual(result["window_start_utc_hour"], 10 * HOUR)

    def test_missing_middle_energy_hour_is_visible_not_zero(self):
        result = self.audit([8, 9, 11, 12], [8, 9, 11, 12])
        self.assertEqual(result["missing_energy_hours"], [10 * HOUR])
        self.assertEqual(result["missing_energy_count"], 1)
        self.assertEqual(result["status"], "attention")
        self.assertNotIn(10 * HOUR, result["missing_cost_hours"])

    def test_missing_tail_is_reported_after_settle_grace(self):
        result = self.audit([8, 9], [8, 9], last=9, due=12)
        self.assertEqual(result["missing_energy_hours"], [10 * HOUR, 11 * HOUR, 12 * HOUR])

    def test_pending_and_unpriceable_cost_are_not_silently_complete(self):
        result = self.audit([9, 10, 11, 12], [9], unpriced=[11], pending=[10])
        self.assertEqual(result["queued_cost_hours"], [10 * HOUR])
        self.assertEqual(result["unpriceable_cost_hours"], [11 * HOUR])
        self.assertEqual(result["missing_cost_hours"], [12 * HOUR])
        self.assertEqual(result["status"], "attention")

    def test_queued_hours_are_recovering_not_missing(self):
        result = self.audit([10, 11, 12], [10, 11], pending=[12])
        self.assertEqual(result["status"], "recovering")
        self.assertEqual(result["missing_cost_count"], 0)

    def test_disabled_cost_does_not_report_cost_gaps(self):
        result = self.audit([10, 11, 12], [], enabled=False)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["missing_cost_count"], 0)

    def test_no_meter_evidence_is_collecting_not_missing(self):
        result = self.audit([], [], last=None)
        self.assertEqual(result["status"], "collecting")
        self.assertEqual(result["checked_hours"], 0)

    def test_unsettled_recent_hour_is_not_classed_missing(self):
        now = 13 * HOUR + SETTLE_SECONDS - 1
        result = summarize_recent_hours(
            [{"start": 11 * HOUR}, {"start": 12 * HOUR}],
            [], now=now, last_completed_hour=12 * HOUR, cost_enabled=False,
        )
        self.assertEqual(result["expected_through_utc_hour"], 12 * HOUR)
        self.assertEqual(result["missing_energy_count"], 0)

    def test_window_is_bounded_after_long_outage(self):
        result = self.audit([], [], due=1000, last=10, enabled=False)
        self.assertEqual(result["checked_hours"], 72)
        self.assertEqual(result["missing_energy_count"], 72)
        self.assertEqual(len(result["missing_energy_hours"]), 24)


if __name__ == "__main__":
    unittest.main()
