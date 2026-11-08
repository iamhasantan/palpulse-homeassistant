"""Safe historical cost planning tests. No Home Assistant runtime required."""
import importlib
from pathlib import Path
import sys
import types
import unittest

folder = Path(__file__).resolve().parents[1] / "custom_components" / "palpulse"
pkg = types.ModuleType("isolated_palpulse_cost_migration")
pkg.__path__ = [str(folder)]
sys.modules[pkg.__name__] = pkg
mod = importlib.import_module("isolated_palpulse_cost_migration.cost_migration_plan")
helpers = importlib.import_module("isolated_palpulse_cost_migration.migration_plan")
HOUR = 3600
CUTOVER = 1793757600


def row(time, state, summation):
    return {"start": time, "state": state, "sum": summation}


class CostMigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.legacy = [
            row(CUTOVER - 3 * HOUR, 12.50, 101.50),
            row(CUTOVER - 2 * HOUR, 12.80, 101.80),
            row(CUTOVER - HOUR, 13.25, 102.25),
        ]
        self.new = [
            row(CUTOVER, 0, 0),
            row(CUTOVER + HOUR, .15, .15),
        ]

    def test_copy_existing_costs_not_reprice(self):
        original = list(self.legacy), list(self.new)
        plan = mod.plan_cost_migration(self.legacy, self.new)
        self.assertEqual(plan.count, 3)
        self.assertEqual(plan.hours[-1][2], 0)
        self.assertAlmostEqual(plan.hours[1][2], -0.45)
        self.assertAlmostEqual(plan.hours[0][2], -0.75)
        self.assertEqual((self.legacy, self.new), original)

    def test_no_overlap_existing_live_cost(self):
        self.legacy.append(row(CUTOVER, 15, 103))
        plan = mod.plan_cost_migration(self.legacy, self.new)
        self.assertTrue(all(time < CUTOVER for time, _, _ in plan.hours))

    def test_reject_non_zero_existing_cost_anchor(self):
        self.new[0]["sum"] = 0.25
        with self.assertRaises(helpers.MigrationValidationError):
            mod.plan_cost_migration(self.legacy, self.new)

    def test_missing_cost_hour_is_not_filled(self):
        self.legacy.pop(1)
        plan = mod.plan_cost_migration(self.legacy, self.new)
        self.assertEqual(plan.count, 2)
        self.assertEqual(plan.gaps, 1)

    def test_undo_only_imported_rows(self):
        plan = mod.plan_cost_migration(self.legacy, self.new)
        expected = [list(r) for r in plan.hours]
        actual = [row(*r) for r in expected]
        helpers.validate_undo(expected, actual, plan.first_hour, plan.cutover)
        actual[0]["sum"] += .25
        with self.assertRaises(helpers.MigrationValidationError):
            helpers.validate_undo(expected, actual, plan.first_hour, plan.cutover)

    def test_source_change_blocks_resume(self):
        plan = mod.plan_cost_migration(self.legacy, self.new)
        expected = [list(r) for r in plan.hours]
        altered = [dict(r) for r in self.legacy]
        altered[1]["sum"] += 0.10
        with self.assertRaises(helpers.MigrationValidationError):
            helpers.validate_original_snapshot(expected, altered)


if __name__ == "__main__":
    unittest.main()
