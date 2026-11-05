"""Crash and safe-resume tests with in-memory HA Recorder and backup doubles.

Exercises partial write before journal commit, conflicting source/target data,
backup verification, duplicate submits, and live stage notifications.
"""
from __future__ import annotations

import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_migration_runtime as runtime

migration = runtime.migration
TARGET = runtime.TARGET
SOURCE = runtime.SOURCE


class MigrationResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.MigrationRuntimeTests.asyncSetUp(self)

    def prewritten_journal(self, written=1):
        """Simulate an interrupted import with a stale progress journal."""
        self.manager.journal = {
            "version": 1,  # Verify compatibility with the deployed beta journal.
            "status": "applying",
            "source": SOURCE,
            "target": TARGET,
            "start": self.plan.first_hour,
            "cutover": self.plan.cutover,
            "rows": [list(row) for row in self.plan.hours],
            "digest": self.plan.digest,
            "confirmed_rows": 0,  # Crash after DB commit, before Store commit.
            "backup_before_migration": "valid-recorder-backup",
            "backup_before_undo": None,
        }
        self.rows.extend(
            {"start": ts, "state": state, "sum": summation}
            for ts, state, summation in self.plan.hours[:written]
        )

    async def restarted_manager(self):
        await self.manager._save()
        previous = self.manager
        new = migration.LegacyMigration(
            self.hass, types.SimpleNamespace(entry_id="test_migration"), TARGET
        )
        new._statistics = previous._statistics
        new._verify_prior_backup = AsyncMock(return_value=None)
        await new.async_load()
        self.manager = new
        return new

    async def test_restart_detects_previous_attempt_without_auto_writing(self):
        self.prewritten_journal(1)
        new = await self.restarted_manager()
        self.assertEqual(new.progress["stage"], "interrupted")
        self.assertTrue(new.progress["resume_available"])
        self.assertEqual(new.progress["completed_rows"], 0)
        self.assertEqual(len(self.rows), 1)

    async def test_resume_reconciles_real_recorder_not_stale_journal(self):
        self.prewritten_journal(1)
        mgr = await self.restarted_manager()
        stages = []
        unsubscribe = mgr.subscribe(lambda: stages.append(dict(mgr.progress)))
        submitted = []

        def record_import(hass, metadata, records):
            self.assertEqual(metadata["statistic_id"], TARGET)
            submitted.extend(records)
            self.rows.extend({
                "start": row["start"].timestamp(),
                "state": row["state"],
                "sum": row["sum"],
            } for row in records)

        with patch.object(migration, "async_add_external_statistics", record_import):
            await mgr.async_resume()
        unsubscribe()
        self.assertEqual(mgr.journal["status"], "applied")
        self.assertEqual(mgr.progress["stage"], "completed")
        self.assertEqual(mgr.progress["completed_rows"], 2)
        self.assertEqual(mgr.progress["total_rows"], 2)
        self.assertEqual(mgr.progress["percent"], 100)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["start"].timestamp(), self.plan.hours[1][0])
        self.assertEqual(len(self.rows), 2)
        self.assertTrue(any(v["stage"] == "reconciling" for v in stages))
        self.assertTrue(any(v["stage"] == "importing" and v["completed_rows"] == 1
                            for v in stages))
        self.assertEqual(len(self.old_rows), 2)
        mgr._verify_prior_backup.assert_awaited_once_with("valid-recorder-backup")

    async def test_import_button_also_resumes_without_new_preview_or_backup(self):
        self.prewritten_journal(1)
        mgr = await self.restarted_manager()
        mgr._backup = AsyncMock()
        def record_import(hass, metadata, records):
            self.rows.extend({
                "start": row["start"].timestamp(),
                "state": row["state"],
                "sum": row["sum"],
            } for row in records)
        with patch.object(migration, "async_add_external_statistics", record_import):
            await mgr.async_migrate()
        mgr._backup.assert_not_awaited()
        self.assertEqual(mgr.journal["status"], "applied")
        with self.assertRaisesRegex(runtime.HomeAssistantError, "No incomplete"):
            await mgr.async_resume()

    async def test_conflicting_existing_target_is_not_overwritten(self):
        self.prewritten_journal(1)
        self.rows[0]["sum"] += 0.25
        mgr = await self.restarted_manager()
        with patch.object(migration, "async_add_external_statistics") as writes:
            with self.assertRaisesRegex(runtime.plan_module.MigrationValidationError,
                                        "changed"):
                await mgr.async_resume()
        writes.assert_not_called()
        self.assertEqual(mgr.journal["status"], "failed")

    async def test_foreign_target_hour_blocks_resume(self):
        self.prewritten_journal(1)
        self.rows.append({
            "start": self.plan.first_hour + 1800, "state": 30.5, "sum": 0.5,
        })
        mgr = await self.restarted_manager()
        with patch.object(migration, "async_add_external_statistics") as writes:
            with self.assertRaisesRegex(runtime.plan_module.MigrationValidationError,
                                        "Unexpected"):
                await mgr.async_resume()
        writes.assert_not_called()

    async def test_source_changes_block_resume_before_writes(self):
        self.prewritten_journal(1)
        self.old_rows[0]["state"] += 1.0
        mgr = await self.restarted_manager()
        with patch.object(migration, "async_add_external_statistics") as writes:
            with self.assertRaisesRegex(runtime.plan_module.MigrationValidationError,
                                        "changed"):
                await mgr.async_resume()
        writes.assert_not_called()

    async def test_missing_backup_blocks_resume_before_writes(self):
        self.prewritten_journal(1)
        mgr = await self.restarted_manager()
        mgr._verify_prior_backup = AsyncMock(
            side_effect=runtime.HomeAssistantError("Original backup missing")
        )
        with patch.object(migration, "async_add_external_statistics") as writes:
            with self.assertRaisesRegex(runtime.HomeAssistantError,
                                        "Original backup missing"):
                await mgr.async_resume()
        writes.assert_not_called()

    async def test_many_batches_resume_from_committed_partial_prefix(self):
        # 500 hourly rows with 250 already in Recorder, although the durable
        # journal still says zero because HA exited before its Store write.
        n = 500
        first = runtime.CUTOVER - n * runtime.HOUR
        self.old_rows[:] = [
            {"start": first + i * runtime.HOUR,
             "state": 20.0 + (i + 1) / 10,
             "sum": 100.0 + (i + 1) / 10}
            for i in range(n)
        ]
        self.new_rows[:] = [
            {"start": runtime.CUTOVER, "state": 70.0, "sum": 0.0}
        ]
        expected = [
            [x["start"], x["state"], round(x["sum"] - 150.0, 10)]
            for x in self.old_rows
        ]
        self.manager.journal = {
            "version": 1,
            "status": "applying",
            "source": SOURCE,
            "target": TARGET,
            "start": first,
            "cutover": runtime.CUTOVER,
            "rows": expected,
            "confirmed_rows": 0,
            "backup_before_migration": "valid-recorder-backup",
        }
        self.rows.extend(
            {"start": ts, "state": state, "sum": amount}
            for ts, state, amount in expected[:250]
        )
        mgr = await self.restarted_manager()
        batches = []
        def record_import(hass, metadata, records):
            batches.append(len(records))
            self.rows.extend({
                "start": row["start"].timestamp(),
                "state": row["state"],
                "sum": row["sum"],
            } for row in records)
        with patch.object(migration, "async_add_external_statistics", record_import):
            await mgr.async_resume()
        self.assertEqual(batches, [240, 10])
        self.assertEqual(len(self.rows), 500)
        self.assertEqual(len({r["start"] for r in self.rows}), 500)
        self.assertEqual(mgr.journal["status"], "applied")
        self.assertEqual(mgr.journal["confirmed_rows"], 500)
        self.assertEqual(mgr.progress["percent"], 100)

    async def test_duplicate_journal_timestamp_rejected_before_writes(self):
        self.prewritten_journal(1)
        self.manager.journal["rows"].append(list(self.plan.hours[1]))
        mgr = await self.restarted_manager()
        with patch.object(migration, "async_add_external_statistics") as writes:
            with self.assertRaisesRegex(runtime.HomeAssistantError,
                                        "duplicated"):
                await mgr.async_resume()
        writes.assert_not_called()

    async def test_all_rows_committed_before_crash_completes_without_rewriting(self):
        self.prewritten_journal(2)
        mgr = await self.restarted_manager()
        with patch.object(migration, "async_add_external_statistics") as writes:
            await mgr.async_resume()
        writes.assert_not_called()
        self.assertEqual(mgr.journal["status"], "applied")
        self.assertEqual(mgr.progress["percent"], 100)
        self.assertEqual(len(self.rows), 2)


if __name__ == "__main__":
    unittest.main()
