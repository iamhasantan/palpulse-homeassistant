"""Pure regression tests for conservative missing-cost reconstruction."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import importlib
import sys
import types
import unittest

root = Path(__file__).parents[1] / "custom_components" / "palpulse"
pkg = types.ModuleType("missing_cost_test_pkg")
pkg.__path__ = [str(root)]
sys.modules[pkg.__name__] = pkg
plans = importlib.import_module("missing_cost_test_pkg.missing_cost_plan")
tariffs = importlib.import_module("missing_cost_test_pkg.tariff_history")
ZONE = ZoneInfo("Australia/Melbourne")
H = 3600


def tariff():
    return {
        "pricing_mode": "custom_time_of_use",
        "peak_cents_per_kwh": 34.936, "shoulder_cents_per_kwh": 0,
        "offpeak_cents_per_kwh": 20.24, "daily_supply_cents": 118.217,
        "daily_free_cap_kwh": 15, "free_excess_cents_per_kwh": 16.412,
        "weekday_free": "11:00-16:00", "weekend_free": "11:00-16:00",
        "weekday_peak": "16:00-21:00", "weekend_peak": "16:00-21:00",
        "weekday_shoulder": "", "weekend_shoulder": "",
    }


class MissingCostPlanTests(unittest.TestCase):
    def setUp(self):
        self.origin = int(datetime(2026, 11, 11, tzinfo=ZONE).timestamp())
        self.versions = tariffs.upsert_tariff_version(
            [], "2026-11-11", tariff(), ZONE.key,
            today=date(2026, 11, 14),
        )
        self.energy = []
        for i in range(-1, 60):
            t = self.origin + i*H
            self.energy.append({
                "start": t, "state": 100 + i, "sum": 200 + i,
                "change": 1.0,
            })
        self.cost = []
        current_sum = 0.0
        # 11 Nov 00..09 already costed, including supply at midnight.
        for i in range(10):
            charge = .2024 + (1.18217 if i == 0 else 0)
            current_sum += charge
            self.cost.append({
                "start": self.origin + i*H, "sum": current_sum,
                "state": current_sum, "change": charge,
            })
        # Missing 11 Nov 10 through 12 Nov 08. The next costed row
        # (12 Nov 09) already carries the daily supply fee.
        self.after_gap = self.origin + 33*H
        current_sum += .2024 + 1.18217
        self.cost.append({
            "start": self.after_gap, "sum": current_sum,
            "state": current_sum, "change": .2024 + 1.18217,
        })
        for i in (34, 35, 36, 37):
            charge = 0.0 if i <= 35 else .0
            self.cost.append({
                "start": self.origin + i*H,
                "sum": current_sum + charge, "state": current_sum + charge,
                "change": charge,
            })

    def plan(self):
        return plans.build_missing_cost_plan(
            self.energy, self.cost, self.versions, ZONE.key,
            last_costed_hour=self.origin + 37*H,
        )

    def test_missing_cost_is_priced_without_zero_assumptions(self):
        result = self.plan()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["missing_hours"], 23)
        rows = {row["start"]: row for row in result["rows"]}
        self.assertAlmostEqual(rows[self.origin+10*H]["charge"], .2024)
        self.assertEqual(rows[self.origin+11*H]["charge"], 0.0)
        self.assertAlmostEqual(rows[self.origin+16*H]["charge"], .34936)
        self.assertEqual(rows[self.after_gap]["source"], "original_charge_retained")
        self.assertAlmostEqual(rows[self.after_gap]["charge"], .2024+1.18217)
        self.assertAlmostEqual(
            rows[self.after_gap]["new_sum"] - rows[self.after_gap-H]["new_sum"],
            .2024 + 1.18217,
        )
        self.assertGreater(rows[self.after_gap]["new_sum"], self.cost[10]["sum"])

    def test_original_change_must_agree_with_original_monetary_sum(self):
        self.cost[-1]["change"] = .25
        with self.assertRaisesRegex(ValueError, "disagrees"):
            self.plan()

    def test_no_adjacent_cost_anchor_refuses(self):
        self.cost = [row for row in self.cost
                     if row["start"] != self.origin + 9*H]
        self.energy = [row for row in self.energy
                       if row["start"] != self.origin + 9*H]
        with self.assertRaisesRegex(ValueError, "adjacent cost anchor"):
            self.plan()

    def test_energy_gap_refuses_instead_of_guessing(self):
        self.energy = [row for row in self.energy
                       if row["start"] != self.origin + 13*H]
        with self.assertRaisesRegex(ValueError, "verified kWh"):
            self.plan()

    def test_unverified_free_window_prior_hour_blocks(self):
        # The missing hour at 13:00 requires independently known free energy
        # at 11:00 and 12:00 for the shared 15kWh cap.
        self.energy = [row for row in self.energy
                       if row["start"] != self.origin + 12*H]
        with self.assertRaises(ValueError):
            self.plan()

    def test_negative_original_change_refused(self):
        self.cost[-1]["change"] = -0.01
        with self.assertRaisesRegex(ValueError, "original hourly"):
            self.plan()

    def test_no_missing_cost_is_noop(self):
        self.cost = [{
            "start": self.origin + i*H, "sum": i*0.2, "state": i*0.2,
            "change": 0.2,
        } for i in range(38)]
        self.assertEqual(self.plan()["status"], "nothing_to_repair")

    def test_stale_recorder_snapshot_changes_digest(self):
        before = self.plan()["digest"]
        for row in self.cost[10:]:
            row["sum"] += .1
            row["state"] += .1
        after = self.plan()["digest"]
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
