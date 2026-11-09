"""Validate opt-in historical daily cost conversion without HA runtime."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import importlib
import sys
import types
import unittest

folder = Path(__file__).resolve().parents[1] / "custom_components" / "palpulse"
pkg = types.ModuleType("isolated_palpulse_daily_cost")
pkg.__path__ = [str(folder)]
sys.modules[pkg.__name__] = pkg
mod = importlib.import_module("isolated_palpulse_daily_cost.daily_cost_plan")
errors = importlib.import_module("isolated_palpulse_daily_cost.migration_plan")
zone = ZoneInfo("Australia/Melbourne")
HOUR = 3600


def day_rows(day: date, cost_each_hour=1.0):
    start = int(datetime.combine(
        day, time.min, tzinfo=zone
    ).astimezone(timezone.utc).timestamp())
    end = int(datetime.combine(
        day + timedelta(days=1), time.min, tzinfo=zone
    ).astimezone(timezone.utc).timestamp())
    return [
        {"start": stamp, "state": round((i + 1) * cost_each_hour, 5)}
        for i, stamp in enumerate(range(start, end, HOUR))
    ]


def cutoff(day: date):
    return day_rows(day)[0]["start"]


class DailyCostPlanTests(unittest.TestCase):
    def test_full_local_days_join_to_stable_cumulative_history(self):
        source = day_rows(date(2026, 11, 8)) + day_rows(date(2026, 11, 9))
        converted = mod.convert_daily_reset_costs(
            source, cutoff(date(2026, 11, 10)), "Australia/Melbourne"
        )
        self.assertEqual(converted.imported_days, 2)
        self.assertEqual(converted.skipped_days, 0)
        self.assertEqual(converted.observed_resets, 1)
        self.assertEqual(len(converted.rows), 48)
        self.assertAlmostEqual(converted.total_cost, 48.0)
        self.assertEqual(converted.rows[0]["sum"], 1.0)
        self.assertEqual(converted.rows[-1]["sum"], 48.0)
        # Original data is never altered.
        self.assertNotIn("sum", source[0])

    def test_dst_spring_and_autumn_have_correct_hour_counts(self):
        for dst_day, expected in (
            (date(2026, 10, 4), 23),
            (date(2026, 4, 5), 25),
        ):
            with self.subTest(dst_day=dst_day):
                source = day_rows(dst_day)
                source += day_rows(dst_day + timedelta(days=1))
                converted = mod.convert_daily_reset_costs(
                    source, cutoff(dst_day + timedelta(days=2)),
                    "Australia/Melbourne"
                )
                self.assertEqual(converted.imported_days, 2)
                self.assertEqual(len(converted.rows), expected + 24)
                self.assertEqual(converted.observed_resets, 1)

    def test_missing_hour_skips_entire_day_without_made_up_cost(self):
        bad = day_rows(date(2026, 11, 8))
        bad.pop(9)
        source = (
            bad
            + day_rows(date(2026, 11, 9))
            + day_rows(date(2026, 11, 10))
        )
        converted = mod.convert_daily_reset_costs(
            source, cutoff(date(2026, 11, 11)), "Australia/Melbourne"
        )
        self.assertEqual(converted.skipped_days, 1)
        self.assertEqual(converted.imported_days, 2)
        self.assertEqual(len(converted.rows), 48)
        self.assertEqual(converted.rows[0]["start"], source[len(bad)]["start"])

    def test_partial_cutover_day_is_skipped(self):
        source = day_rows(date(2026, 11, 8))
        source += day_rows(date(2026, 11, 9))[:6]
        converted = mod.convert_daily_reset_costs(
            source, source[-1]["start"] + HOUR, "Australia/Melbourne"
        )
        self.assertEqual(converted.imported_days, 1)
        self.assertEqual(converted.skipped_days, 1)
        self.assertEqual(len(converted.rows), 24)

    def test_lifetime_cost_does_not_masquerade_as_daily_sensor(self):
        source = day_rows(date(2026, 11, 8))
        next_day = day_rows(date(2026, 11, 9))
        for row in next_day:
            row["state"] += 24
        source += next_day
        with self.assertRaisesRegex(
            errors.MigrationValidationError, "No midnight reset"
        ):
            mod.convert_daily_reset_costs(
                source, cutoff(date(2026, 11, 10)), "Australia/Melbourne"
            )

    def test_mean_only_or_missing_state_is_rejected(self):
        source = day_rows(date(2026, 11, 8))
        source[0].pop("state")
        source += day_rows(date(2026, 11, 9))
        with self.assertRaisesRegex(
            errors.MigrationValidationError, "hourly closing states"
        ):
            mod.convert_daily_reset_costs(
                source, cutoff(date(2026, 11, 10)), "Australia/Melbourne"
            )

    def test_negative_within_day_skips_ambiguous_day(self):
        source = day_rows(date(2026, 11, 8))
        bad = day_rows(date(2026, 11, 9))
        bad[15]["state"] = 0.5
        source += bad + day_rows(date(2026, 11, 10))
        converted = mod.convert_daily_reset_costs(
            source, cutoff(date(2026, 11, 11)), "Australia/Melbourne"
        )
        self.assertEqual(converted.skipped_days, 1)
        self.assertEqual(converted.imported_days, 2)


if __name__ == "__main__":
    unittest.main()
