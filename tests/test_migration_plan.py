"""Pure safety tests for experimental read-only planning and bounded undo."""

import importlib.util
from pathlib import Path
import sys
import unittest

module_path = Path(__file__).resolve().parents[1] / "custom_components/palpulse/migration_plan.py"
spec = importlib.util.spec_from_file_location("palpulse_migration_plan", module_path)
migration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = migration
spec.loader.exec_module(migration)

HOUR = 3600
CUTOVER = 1793757600  # aligned UTC


def row(ts, state, summation):
    return {"start": ts, "state": state, "sum": summation}


class MigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.old = [
            row(CUTOVER - 3 * HOUR, 30.0, 5.0),
            row(CUTOVER - 2 * HOUR, 30.8, 5.8),
            row(CUTOVER - HOUR, 31.4, 6.4),
            row(CUTOVER, 32.0, 7.0),  # Must never be copied
        ]
        self.new = [
            row(CUTOVER, 32.0, 0.0),  # Existing synthetic baseline
            row(CUTOVER + HOUR, 33.0, 1.0),
            row(CUTOVER + 2 * HOUR, 34.0, 2.0),
        ]

    def test_never_overlaps_or_modifies_existing_pal_pulse_hours(self):
        before = list(self.old), list(self.new)
        plan = migration.plan_migration(self.old, self.new)
        self.assertEqual(plan.count, 3)
        self.assertEqual(plan.cutover, CUTOVER)
        self.assertTrue(all(t < CUTOVER for t, _, _ in plan.hours))
        self.assertAlmostEqual(plan.hours[-1][2], 0.0)
        self.assertAlmostEqual(plan.hours[-2][2], -0.6)
        self.assertAlmostEqual(plan.hours[-3][2], -1.4)
        self.assertEqual((self.old, self.new), before)

    def test_same_plan_is_deterministic(self):
        a = migration.plan_migration(self.old, self.new)
        b = migration.plan_migration(list(reversed(self.old)), self.new)
        self.assertEqual(a.digest, b.digest)

    def test_rejects_negative_legacy_consumption_with_precise_diagnostic(self):
        self.old[2]["sum"] = 0.1
        with self.assertRaises(migration.LegacyDecreaseError) as caught:
            migration.plan_migration(self.old, self.new)
        issue = caught.exception.decreases[0]
        self.assertEqual(issue.earlier_hour, CUTOVER - 2 * HOUR)
        self.assertEqual(issue.later_hour, CUTOVER - HOUR)
        self.assertAlmostEqual(issue.before_sum, 5.8)
        self.assertAlmostEqual(issue.after_sum, 0.1)
        self.assertAlmostEqual(issue.difference, -5.7)
        self.assertIn("UTC", str(caught.exception))
        self.assertIn("Migration blocked", str(caught.exception))

    def test_diagnose_all_decreases_not_only_first(self):
        old = [
            row(CUTOVER - 5 * HOUR, 29.0, 9.0),
            row(CUTOVER - 4 * HOUR, 29.2, 7.0),
            row(CUTOVER - 3 * HOUR, 30.0, 9.5),
            row(CUTOVER - 2 * HOUR, 30.8, 5.8),
            row(CUTOVER - HOUR, 31.4, 6.4),
        ]
        rows = migration._read_rows(old, "Original")
        drops = migration.diagnose_decreases(rows)
        self.assertEqual(len(drops), 2)
        self.assertEqual(drops[0].later_hour, CUTOVER - 4 * HOUR)
        self.assertEqual(drops[1].later_hour, CUTOVER - 2 * HOUR)

    def test_rejects_changed_or_nonbaseline_cutover(self):
        self.new[0]["sum"] = 0.5
        with self.assertRaisesRegex(migration.MigrationValidationError, "zero-sum"):
            migration.plan_migration(self.old, self.new)

    def test_rejects_legacy_meter_ahead_of_new_meter(self):
        self.old[2]["state"] = 39.0
        with self.assertRaisesRegex(migration.MigrationValidationError, "exceeds"):
            migration.plan_migration(self.old, self.new)

    def test_detects_missing_hours_without_fabricating(self):
        del self.old[1]
        plan = migration.plan_migration(self.old, self.new)
        self.assertEqual(plan.gaps, 1)
        self.assertEqual(plan.count, 2)

    def test_undo_accepts_only_exact_journalled_rows(self):
        plan = migration.plan_migration(self.old, self.new)
        expected = [list(r) for r in plan.hours]
        actual = [row(*r) for r in expected]
        migration.validate_undo(expected, actual, plan.first_hour, plan.cutover)
        migration.validate_undo(expected, [], plan.first_hour, plan.cutover)

    def test_undo_refuses_foreign_or_modified_rows(self):
        plan = migration.plan_migration(self.old, self.new)
        expected = [list(r) for r in plan.hours]
        actual = [row(*r) for r in expected]
        actual[1]["sum"] += 0.1
        with self.assertRaisesRegex(migration.MigrationValidationError, "changed"):
            migration.validate_undo(expected, actual, plan.first_hour, plan.cutover)
        unknown = row(plan.first_hour + 100, 30, 5)
        with self.assertRaisesRegex(migration.MigrationValidationError, "Unexpected"):
            migration.validate_undo(expected, [unknown], plan.first_hour, plan.cutover)

    def test_invalid_nan_and_missing_stats_fail_closed(self):
        self.old[1]["sum"] = float("nan")
        with self.assertRaises(migration.MigrationValidationError):
            migration.plan_migration(self.old, self.new)
        self.old = []
        with self.assertRaises(migration.MigrationValidationError):
            migration.plan_migration(self.old, self.new)


if __name__ == "__main__":
    unittest.main()
