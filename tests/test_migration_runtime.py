"""Prove backups gate writes and Undo only targets journalled PalPulse hours.

These are narrow HA API doubles, not full Home Assistant system tests.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

# Load the existing 2026.11 event/Store test doubles.
sys.path.insert(0, str(Path(__file__).parent))
import test_importer  # noqa: E402

components = sys.modules["homeassistant.components"]
statistics = sys.modules["homeassistant.components.recorder.statistics"]
statistics.async_list_statistic_ids = AsyncMock(return_value=[])
statistics.statistics_during_period = lambda *args, **kwargs: {}
backup = types.ModuleType("homeassistant.components.backup")
notifications = types.ModuleType("homeassistant.components.persistent_notification")
helpers_recorder = types.ModuleType("homeassistant.helpers.recorder")
RECORDER_INSTANCE = [None]
helpers_recorder.get_instance = lambda hass: RECORDER_INSTANCE[0]
exceptions = types.ModuleType("homeassistant.exceptions")


class HomeAssistantError(Exception):
    pass


exceptions.HomeAssistantError = HomeAssistantError
notifications.async_create = lambda *args, **kwargs: None
sys.modules[backup.__name__] = backup
sys.modules[notifications.__name__] = notifications
sys.modules[helpers_recorder.__name__] = helpers_recorder
sys.modules[exceptions.__name__] = exceptions
components.backup = backup
components.persistent_notification = notifications
migration = importlib.import_module("custom_components.palpulse.migration")
plan_module = importlib.import_module("custom_components.palpulse.migration_plan")

HOUR = 3600
CUTOVER = 1793757600
SOURCE = "sensor.powerpal_proxy_powerpal_total_energy"
TARGET = "palpulse:powerpal_d8a5d6d91964_energy"


class FakeRecorder:
    def __init__(self, rows):
        self.rows = rows
        self.clears = []

    async def async_block_till_done(self):
        return None

    def async_clear_statistics(self, ids, *, start_time, end_time, on_done):
        self.clears.append((ids, start_time, end_time))
        self.rows[:] = [
            x for x in self.rows
            if not start_time.timestamp() <= x["start"] < end_time.timestamp()
        ]
        on_done()


class MigrationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        test_importer.FakeStore.VALUES.clear()
        self.messages = []
        notifications.async_create = (
            lambda hass, message, **kwargs: self.messages.append(message)
        )
        self.rows = []
        self.recorder = FakeRecorder(self.rows)
        RECORDER_INSTANCE[0] = self.recorder
        self.hass = types.SimpleNamespace(
            loop=asyncio.get_running_loop(),
            async_add_executor_job=lambda callback: asyncio.to_thread(callback),
        )
        entry = types.SimpleNamespace(entry_id="test_migration")
        self.manager = migration.LegacyMigration(self.hass, entry, TARGET)
        await self.manager.async_load()
        old = [
            {"start": CUTOVER - 2 * HOUR, "state": 30.0, "sum": 8.0},
            {"start": CUTOVER - HOUR, "state": 31.0, "sum": 9.0},
        ]
        new = [
            {"start": CUTOVER, "state": 31.5, "sum": 0.0},
            {"start": CUTOVER + HOUR, "state": 32.5, "sum": 1.0},
        ]
        self.plan = plan_module.plan_migration(old, new)
        self.old_rows = old
        self.new_rows = new

        async def read(statistic_id, start, end, period="hour"):
            if period == "5minute":
                return []
            if statistic_id == TARGET:
                return [
                    row for row in [*self.rows, *self.new_rows]
                    if start.timestamp() <= row["start"] < end.timestamp()
                ]
            return [
                row for row in self.old_rows
                if start.timestamp() <= row["start"] < end.timestamp()
            ]

        self.manager._statistics = read
        self.manager._plan = AsyncMock(return_value=self.plan)
        self.manager._verify_prior_backup = AsyncMock(return_value=None)

    async def test_bad_legacy_sum_preview_warns_without_writes(self):
        decrease = plan_module.LegacyDecrease(
            earlier_hour=CUTOVER - 2 * HOUR,
            later_hour=CUTOVER - HOUR,
            before_sum=8.0,
            after_sum=3.5,
            before_state=30.0,
            after_state=31.0,
        )
        self.manager._plan = AsyncMock(return_value=plan_module.MigrationPlan(
            cutover=self.plan.cutover,
            first_hour=self.plan.first_hour,
            hours=self.plan.hours,
            digest=self.plan.digest,
            gaps=0,
            large_steps=0,
            decreases=(decrease,),
            anchor_state=self.plan.anchor_state,
            anchor_sum=self.plan.anchor_sum,
        ))
        self.manager._backup = AsyncMock()
        await self.manager.async_preview()
        self.assertIsNotNone(self.manager._preview)
        self.assertEqual(self.manager.progress["stage"], "ready")
        message = self.messages[-1]
        self.assertIn("REVIEW BEFORE IMPORT", message)
        self.assertIn("cumulative sum 8.00000 -> 3.50000", message)
        self.assertIn("raw meter state 30.00000 -> 31.00000", message)
        self.manager._backup.assert_not_awaited()
        self.assertEqual(self.rows, [])

    async def test_no_write_until_completed_backup_and_preview(self):
        with self.assertRaisesRegex(HomeAssistantError, "Preview"):
            await self.manager.async_migrate()
        self.assertEqual(self.rows, [])

        await self.manager.async_preview()

        async def fail_backup(reason):
            raise HomeAssistantError("Backup failed")

        self.manager._backup = fail_backup
        with self.assertRaisesRegex(HomeAssistantError, "Backup failed"):
            await self.manager.async_migrate()
        self.assertEqual(self.rows, [])
        self.assertFalse(self.manager.journal)

    async def test_apply_and_undo_leave_original_data_untouched(self):
        backups = []

        async def backup_made(reason):
            backups.append(reason)
            if reason == "before migration":
                self.assertEqual(self.rows, [])
            return "verified-backup-" + str(len(backups))

        self.manager._backup = backup_made

        def record_import(hass, metadata, records):
            self.assertEqual(metadata["statistic_id"], TARGET)
            self.assertEqual(backups, ["before migration"])
            for row in records:
                self.rows.append({
                    "start": row["start"].timestamp(),
                    "state": row["state"],
                    "sum": row["sum"],
                })

        await self.manager.async_preview()
        with patch.object(migration, "async_add_external_statistics", record_import):
            await self.manager.async_migrate()
        self.assertEqual(len(self.rows), 2)
        self.assertEqual(self.manager.journal["status"], "applied")
        self.assertEqual(len(self.old_rows), 2)
        self.assertEqual(backups, ["before migration"])

        await self.manager.async_undo()
        self.assertEqual(self.rows, [])
        self.assertEqual(len(self.old_rows), 2)
        self.assertEqual(backups, ["before migration", "before undo"])
        self.assertEqual(len(self.recorder.clears), 1)
        ids, start, end = self.recorder.clears[0]
        self.assertEqual(ids, [TARGET])
        self.assertEqual(end.timestamp(), CUTOVER)
        self.assertEqual(self.manager.journal["status"], "undone")

    async def test_undo_refuses_changed_rows_before_backup_or_clear(self):
        async def backup_made(reason):
            return "verified-backup"

        self.manager._backup = backup_made
        await self.manager.async_preview()

        def record_import(hass, metadata, records):
            self.rows.extend({
                "start": row["start"].timestamp(),
                "state": row["state"],
                "sum": row["sum"],
            } for row in records)

        with patch.object(migration, "async_add_external_statistics", record_import):
            await self.manager.async_migrate()
        self.rows[0]["sum"] += 3.0
        with patch.object(self.manager, "_backup", AsyncMock()) as backup_mock:
            with self.assertRaisesRegex(HomeAssistantError, "changed data"):
                await self.manager.async_undo()
        backup_mock.assert_not_awaited()
        self.assertFalse(self.recorder.clears)

    async def test_undo_retains_existing_target_hour(self):
        async def backup_made(reason):
            return "verified-backup"

        self.manager._backup = backup_made
        await self.manager.async_preview()

        def record_import(hass, metadata, records):
            self.rows.extend({
                "start": row["start"].timestamp(),
                "state": row["state"],
                "sum": row["sum"],
            } for row in records)

        with patch.object(migration, "async_add_external_statistics", record_import):
            await self.manager.async_migrate()
        original_target = {"start": CUTOVER, "state": 31.5, "sum": 0}
        self.rows.append(original_target.copy())
        await self.manager.async_undo()
        self.assertEqual(self.rows, [original_target])

    async def test_backup_requires_stored_database_copy(self):
        """Supervisor job IDs and actual saved backup slugs may differ."""
        settings = types.SimpleNamespace(
            agent_ids=["backup.local"], password=None
        )
        saved = {}
        stored_slug = "real-backup-slug-456"
        backup_info = types.SimpleNamespace(
            name=None, database_included=True, homeassistant_included=True,
            agents={"backup.local": object()},
        )

        async def generate(**kwargs):
            self.assertTrue(kwargs["include_database"])
            self.assertTrue(kwargs["include_homeassistant"])
            self.assertFalse(kwargs["with_automatic_settings"])
            self.assertIn("PalPulse test", kwargs["name"])
            backup_info.name = kwargs["name"]
            saved[stored_slug] = backup_info
            # The job ID is NOT the backup slug on Home Assistant OS.
            return types.SimpleNamespace(backup_job_id="supervisor-job-123")

        async def get_backups():
            return saved, {}

        async def get_backup(backup_id):
            self.assertEqual(backup_id, stored_slug)
            return backup_info, {}

        fake_backup_manager = types.SimpleNamespace(
            config=types.SimpleNamespace(
                data=types.SimpleNamespace(create_backup=settings)
            ),
            local_backup_agents={"backup.local": object()},
            async_create_backup=generate,
            async_get_backups=get_backups,
            async_get_backup=AsyncMock(side_effect=get_backup),
        )
        backup.async_get_manager = lambda hass: fake_backup_manager
        self.assertEqual(
            await self.manager._backup("test"), stored_slug
        )
        fake_backup_manager.async_get_backup.assert_awaited_once_with(stored_slug)

        # A backup without the Recorder database cannot authorize a migration.
        backup_info.database_included = False
        with self.assertRaisesRegex(HomeAssistantError, "Recorder database"):
            await self.manager._backup("test")

        backup_info.database_included = True
        async def missing_stored_backup():
            return {}, {}
        fake_backup_manager.async_get_backups = missing_stored_backup
        with self.assertRaisesRegex(HomeAssistantError, "could not"):
            await self.manager._backup("test")

    async def test_backup_uses_local_destination_when_none_configured(self):
        settings = types.SimpleNamespace(agent_ids=[], password=None)
        captured = []
        async def create(**kwargs):
            captured.extend(kwargs["agent_ids"])
            return types.SimpleNamespace(backup_job_id="job-1")
        async def list_stored():
            return {}, {}
        fake_backup_manager = types.SimpleNamespace(
            config=types.SimpleNamespace(data=types.SimpleNamespace(
                create_backup=settings)),
            local_backup_agents={"backup.local": object()},
            async_create_backup=create,
            async_get_backups=list_stored,
        )
        backup.async_get_manager = lambda hass: fake_backup_manager
        with self.assertRaisesRegex(HomeAssistantError, "could not"):
            await self.manager._backup("test")
        self.assertEqual(captured, ["backup.local"])


if __name__ == "__main__":
    unittest.main()
