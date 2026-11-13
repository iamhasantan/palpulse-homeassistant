"""Pure tariff versioning and November cost reconciliation regression tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import importlib
import sys
import types
import unittest
from zoneinfo import ZoneInfo

folder = Path(__file__).resolve().parents[1] / "custom_components" / "palpulse"
pkg = types.ModuleType("palpulse_tariff_reconciliation_test")
pkg.__path__ = [str(folder)]
sys.modules[pkg.__name__] = pkg
history = importlib.import_module("palpulse_tariff_reconciliation_test.tariff_history")
reconcile = importlib.import_module("palpulse_tariff_reconciliation_test.cost_reconcile_plan")
settings = importlib.import_module("palpulse_tariff_reconciliation_test.panel_settings")
MELB = ZoneInfo("Australia/Melbourne")


def tariff():
    return {
        "pricing_mode": "custom_time_of_use",
        "peak_cents_per_kwh": 34.936,
        "shoulder_cents_per_kwh": 0.0,
        "offpeak_cents_per_kwh": 20.240,
        "daily_supply_cents": 118.217,
        "daily_free_cap_kwh": 15.0,
        "free_excess_cents_per_kwh": 16.412,
        "weekday_free": "11:00-16:00",
        "weekend_free": "11:00-16:00",
        "weekday_peak": "16:00-21:00",
        "weekend_peak": "16:00-21:00",
        "weekday_shoulder": "",
        "weekend_shoulder": "",
    }


def range_for_day(iso):
    return reconcile.billing_range(iso, iso, MELB.key, today=date(2026, 11, 12))


def day_rows(iso, per_hour=2.0, omit=()):
    start, end = range_for_day(iso)
    # The prior UTC hour gives the first billing hour its actual kWh delta.
    rows = [{
        "start": start - 3600, "state": 99.0, "sum": 900.0,
        "change": 0.0,
    }]
    for i, t in enumerate(range(start, end, 3600)):
        if i in omit:
            continue
        rows.append({
            "start": t,
            "state": 99.0 + per_hour * (i + 1),
            "sum": 900.0 + per_hour * (i + 1),
            "change": per_hour,
        })
    return rows


class TariffHistoryTests(unittest.TestCase):
    def test_dated_profile_selection_and_backdate(self):
        versions = history.upsert_tariff_version(
            [], "2026-11-09", tariff(), MELB.key,
            today=date(2026, 11, 12),
        )
        day9, _ = range_for_day("2026-11-09")
        self.assertIsNone(history.effective_tariff(versions, day9 - 3600))
        self.assertEqual(
            history.effective_tariff(versions, day9)["effective_from"],
            "2026-11-09",
        )
        altered = dict(tariff(), peak_cents_per_kwh=40.0)
        versions = history.upsert_tariff_version(
            versions, "2026-11-11", altered, MELB.key,
            today=date(2026, 11, 12),
        )
        day11, _ = range_for_day("2026-11-11")
        self.assertEqual(
            history.effective_tariff(versions, day11)["options"]["peak_cents_per_kwh"],
            40.0,
        )
        self.assertEqual(
            history.effective_tariff(versions, day9)["options"]["peak_cents_per_kwh"],
            34.936,
        )

    def test_start_date_saved_by_sidebar_without_rewriting_existing_other_settings(self):
        original = dict(tariff(), other_option="keep")
        saved = settings.normalize_settings(
            original, {"tariff_plan_start_date": "2026-11-09"},
            meter="d8a5d6d91964", timezone_name=MELB.key,
        )
        self.assertEqual(saved["other_option"], "keep")
        self.assertEqual(
            saved["tariff_versions"][0]["effective_from"], "2026-11-09",
        )
        self.assertEqual(saved["tariff_versions"][0]["options"]["peak_cents_per_kwh"], 34.936)
        self.assertNotIn("tariff_versions", settings.EDITABLE)

    def test_reject_invalid_date_or_tariff(self):
        for value in ("2026-11-31", "11/09/2026", "2027-01-01", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                history.validate_start_date(value, today=date(2026, 11, 12))
        with self.assertRaises(ValueError):
            history.validate_versions([{"effective_from": "2026-11-09", "options": {"pricing_mode": "bad"}}])


class CostReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.versions = history.upsert_tariff_version(
            [], "2026-11-09", tariff(), MELB.key,
            today=date(2026, 11, 12),
        )

    def plan(self, iso, *, kwh=2.0, omit=(), existing=None):
        start, end = range_for_day(iso)
        return reconcile.build_cost_plan(
            day_rows(iso, kwh, omit), existing or [],
            self.versions, start, end, zone_name=MELB.key,
        )

    def test_full_saved_contract_range_not_limited_to_fourteen_days(self):
        start, end = reconcile.billing_range(
            "2026-09-15", "2026-11-12", MELB.key,
            today=date(2026, 11, 13),
        )
        self.assertGreater((end-start) // 3600, 14 * 24)
        self.assertEqual(
            datetime.fromtimestamp(start, timezone.utc).astimezone(MELB).date(),
            date(2026, 9, 15),
        )
        self.assertEqual(
            datetime.fromtimestamp(end, timezone.utc).astimezone(MELB).date(),
            date(2026, 11, 13),
        )

    def test_november_9_free_and_peak_are_not_offpeak(self):
        p = self.plan("2026-11-09")
        self.assertEqual(p["verified_hours"], 24)
        self.assertEqual(p["missing_energy_hours"], 0)
        # Free 11-16: 10 kWh <= 15; peak 16-21 10 kWh;
        # remaining 14 offpeak hours 28 kWh; plus supply fee.
        self.assertAlmostEqual(p["last_sum"], 10.34297, places=5)
        self.assertEqual(p["day_totals"][0]["verified_hours"], 24)

    def test_peak_starts_at_4_pm_not_at_9_pm(self):
        p = self.plan("2026-11-09")
        row_3pm = next(r for r in p["rows"] if datetime.fromtimestamp(
            r["start"], timezone.utc).astimezone(MELB).hour == 15)
        row_4pm = next(r for r in p["rows"] if datetime.fromtimestamp(
            r["start"], timezone.utc).astimezone(MELB).hour == 16)
        self.assertEqual(row_3pm["increment"], 0.0)
        self.assertAlmostEqual(row_4pm["increment"], 0.69872, places=5)

    def test_free_cap_excess_uses_distinct_remaining_rate(self):
        p = self.plan("2026-11-09", kwh=4.0)
        self.assertAlmostEqual(
            sum(r["increment"] for r in p["rows"]
                if 11 <= datetime.fromtimestamp(
                    r["start"], timezone.utc).astimezone(MELB).hour < 16),
            5 * 0.16412, places=5,
        )

    def test_missing_free_hour_does_not_stop_paid_peaks(self):
        p = self.plan("2026-11-09", omit=(12,))
        self.assertIn("2026-11-09", p["free_uncertain_dates"])
        at_4pm = next(r for r in p["rows"] if datetime.fromtimestamp(
            r["start"], timezone.utc).astimezone(MELB).hour == 16)
        self.assertAlmostEqual(at_4pm["increment"], 0.69872, places=5)
        self.assertGreater(p["missing_energy_hours"], 0)

    def test_old_wrong_cost_is_recomputed_not_silently_preserved(self):
        start, _ = range_for_day("2026-11-09")
        old = [
            {"start": start + i * 3600, "sum": 2.65 * (i + 1) / 24,
             "state": 2.65 * (i + 1) / 24}
            for i in range(24)
        ]
        p = self.plan("2026-11-09", existing=old)
        self.assertAlmostEqual(p["day_totals"][0]["recorded"], 2.65, places=5)
        self.assertAlmostEqual(p["day_totals"][0]["recomputed"], 10.34297, places=5)
        self.assertNotEqual(p["rows"][-1]["old_sum"], p["rows"][-1]["new_sum"])
        self.assertEqual(len(p["digest"]), 64)

    def test_negative_finite_historical_cost_baseline_is_preserved(self):
        start, _ = range_for_day("2026-11-09")
        p = self.plan("2026-11-09", existing=[{
            "start": start - 3600, "sum": -0.75, "state": -0.75,
        }])
        self.assertTrue(p["baseline_verified"])
        self.assertEqual(p["anchor_sum"], -0.75)
        self.assertAlmostEqual(p["last_sum"], 10.34297 - 0.75, places=5)
        self.assertIn("negative", p["baseline_issues"][0])

    def test_corrupt_latest_anchor_previews_but_blocks_writes(self):
        start, _ = range_for_day("2026-11-09")
        p = self.plan("2026-11-09", existing=[
            {"start": start - 7200, "sum": 4.0, "state": 4.0},
            {"start": start - 3600, "sum": float("nan"), "state": float("nan")},
        ])
        self.assertFalse(p["baseline_verified"])
        self.assertEqual(p["anchor_sum"], 4.0)
        self.assertTrue(p["baseline_issues"])
        self.assertEqual(len(p["digest"]), 64)

    def test_no_prior_cost_row_starts_with_zero_baseline(self):
        p = self.plan("2026-11-09")
        self.assertTrue(p["baseline_verified"])
        self.assertEqual(p["anchor_sum"], 0.0)

    def test_november_11_seven_am_negative_cost_step_is_previewed(self):
        start, _ = range_for_day("2026-11-11")
        old = [
            {"start": start + i * 3600,
             "sum": 12.0 + i * 0.25,
             "state": 12.0 + i * 0.25}
            for i in range(24)
        ]
        # 7 am Melbourne is an actual discontinuity of old cumulative costs.
        old[7]["sum"] = 7.0
        old[7]["state"] = 7.0
        for index in range(8, 24):
            old[index]["sum"] = 7.0 + (index - 7) * 0.25
            old[index]["state"] = old[index]["sum"]
        p = self.plan("2026-11-11", existing=old)
        self.assertEqual(len(
            [x for x in p["cost_counter_conflicts"]
             if x["kind"] == "counter_decrease"]), 1)
        conflict = next(
            x for x in p["cost_counter_conflicts"]
            if x["kind"] == "counter_decrease"
        )
        self.assertEqual(conflict["start"], start + 7 * 3600)
        self.assertFalse(p["day_totals"][0]["recorded_complete"])
        self.assertTrue(p["repair_safe"])
        self.assertFalse(p["blocking_conflicts"])
        self.assertAlmostEqual(p["rows"][7]["old_sum"], 7.0)

    def test_missing_energy_at_cost_counter_reset_blocks_apply_without_zero_charge(self):
        start, _ = range_for_day("2026-11-11")
        energy = day_rows("2026-11-11", omit=(7,))
        old = [
            {"start": start + i * 3600,
             "sum": 12.0 + i * 0.25,
             "state": 12.0 + i * 0.25}
            for i in range(24)
        ]
        old[7]["sum"] = 7.0
        old[7]["state"] = 7.0
        for index in range(8, 24):
            old[index]["sum"] = 7.0 + (index - 7) * 0.25
            old[index]["state"] = old[index]["sum"]
        _, end = range_for_day("2026-11-11")
        p = reconcile.build_cost_plan(
            energy, old, self.versions, start, end, zone_name=MELB.key,
        )
        self.assertFalse(p["repair_safe"])
        self.assertTrue(any(
            x["start"] == start + 7 * 3600
            for x in p["blocking_conflicts"]
        ))
        self.assertFalse(any(
            r["start"] == start + 7 * 3600
            for r in p["rows"]
        ))

    def test_nonfinite_cost_row_is_never_rewritten_on_guess(self):
        start, _ = range_for_day("2026-11-11")
        old = [{
            "start": start + 7 * 3600,
            "sum": float("nan"), "state": float("nan"),
        }]
        p = self.plan("2026-11-11", existing=old)
        self.assertFalse(p["repair_safe"])
        self.assertEqual(p["blocking_conflicts"][0]["kind"], "invalid_value")
        self.assertEqual(len(p["digest"]), 64)

    def test_unknown_previous_plan_is_not_assumed(self):
        start, end = range_for_day("2026-11-09")
        late = history.upsert_tariff_version(
            [], "2026-11-10", tariff(), MELB.key,
            today=date(2026, 11, 12),
        )
        with self.assertRaisesRegex(ValueError, "No dated tariff"):
            reconcile.build_cost_plan(day_rows("2026-11-09"), [], late,
                                      start, end, zone_name=MELB.key)


if __name__ == "__main__":
    unittest.main()
