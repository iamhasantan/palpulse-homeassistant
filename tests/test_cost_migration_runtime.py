"""Exercise the cost-migration coordinator with the existing HA test doubles."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import asyncio
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import test_migration_runtime as base  # noqa: E402
cost_mod = importlib.import_module("custom_components.palpulse.cost_migration")
plan_mod = importlib.import_module("custom_components.palpulse.migration_plan")

HOUR = 3600
SOURCE = "sensor.electricity_cost_today"
TARGET = "palpulse:powerpal_d8a5d6d91964_cost"
ZONE = ZoneInfo("Australia/Melbourne")


def daily_rows(day):
    start = int(datetime.combine(day, time.min, tzinfo=ZONE)
                .astimezone(timezone.utc).timestamp())
    end = int(datetime.combine(day + timedelta(days=1), time.min, tzinfo=ZONE)
              .astimezone(timezone.utc).timestamp())
    return [
        {"start": stamp, "state": float(i + 1)}
        for i, stamp in enumerate(range(start, end, HOUR))
    ]


class CostMigrationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.source = daily_rows(date(2026, 11, 5))
        self.source += daily_rows(date(2026, 11, 6))
        self.cutover = daily_rows(date(2026, 11, 7))[0]["start"]
        self.target = [
            {"start": self.cutover, "state": 0.0, "sum": 0.0},
            {"start": self.cutover + HOUR, "state": 0.25, "sum": 0.25},
        ]
        self.hass = types.SimpleNamespace(
            config=types.SimpleNamespace(time_zone="Australia/Melbourne",currency="AUD"),
            async_add_executor_job=lambda callback: asyncio.to_thread(callback),
        )
        self.entry = types.SimpleNamespace(
            entry_id="daily_cost_test",
            options={
                cost_mod.CONF_LEGACY_COST_STATISTIC_ID: SOURCE,
                cost_mod.CONF_LEGACY_COST_SOURCE_MODE: cost_mod.SOURCE_DAILY_RESET,
            },
        )
        self.manager = cost_mod.LegacyCostMigration(self.hass, self.entry, TARGET)
        await self.manager.async_load()
        self.reads = []
        self.meta = [
            {"statistic_id": SOURCE, "has_sum": False,
             "statistics_unit_of_measurement": "AUD", "unit_class": "monetary"},
            {"statistic_id": TARGET, "has_sum": True,
             "statistics_unit_of_measurement": None, "unit_class": None},
        ]

        async def read(_instance, stat_id, start, end, period="hour"):
            self.reads.append((stat_id, start.timestamp(), end.timestamp(), period))
            if period == "5minute":
                return []
            rows = self.source if stat_id == SOURCE else self.target
            return [dict(row) for row in rows
                    if start.timestamp() <= row["start"] < end.timestamp()]
        self.read_patcher = patch.object(base.migration.LegacyMigration, "_statistics", read)
        self.meta_patcher = patch.object(
            cost_mod, "async_list_statistic_ids", AsyncMock(return_value=self.meta)
        )
        self.read_patcher.start()
        self.meta_patcher.start()
        self.addCleanup(self.read_patcher.stop)
        self.addCleanup(self.meta_patcher.stop)

    async def test_daily_source_plan_uses_complete_days_and_live_cost_baseline(self):
        plan = await self.manager._plan()
        self.assertEqual(plan.count, 48)
        self.assertEqual(plan.cutover, self.cutover)
        self.assertEqual(self.manager._daily_diagnostics.imported_days, 2)
        self.assertEqual(self.manager._daily_diagnostics.observed_resets, 1)
        self.assertAlmostEqual(plan.hours[-1][2], 0.0)
        self.assertTrue(all(row["start"] < plan.cutover for row in self.source))

    async def test_resume_uses_original_cutover_even_with_imported_earlier_rows(self):
        plan = await self.manager._plan()
        original_virtual = await self.manager._statistics(
            SOURCE,
            datetime.fromtimestamp(plan.first_hour, timezone.utc),
            datetime.fromtimestamp(plan.cutover, timezone.utc),
        )
        self.manager.journal = {
            "cutover": plan.cutover, "source": SOURCE,
            "target": TARGET, "status": "prepared",
        }
        self.target = [
            {"start": plan.first_hour, "state": -47, "sum": -47},
            *self.target,
        ]
        second_read = await self.manager._statistics(
            SOURCE,
            datetime.fromtimestamp(plan.first_hour, timezone.utc),
            datetime.fromtimestamp(plan.cutover, timezone.utc),
        )
        self.assertEqual(second_read, original_virtual)
        plan_mod.validate_original_snapshot(
            [list(x) for x in plan.hours], second_read
        )

    async def test_readiness_reports_missing_live_target_without_creating_data(self):
        self.entry.options["pricing_mode"] = "custom_time_of_use"
        self.hass.data = {
            "palpulse_cost": {
                self.entry.entry_id: types.SimpleNamespace(status={
                    "last_costed_hour": None,
                    "last_error": None,
                }),
            },
        }
        self.meta.pop()  # Update the list already held by the mocked Recorder.  # Previous daily cost exists; PalPulse cost is absent.
        original = [dict(row) for row in self.source]
        before = dict(self.manager.journal)
        check = await self.manager.async_readiness()
        self.assertEqual(check["status"], "waiting_for_cost")
        self.assertEqual(check["source"], SOURCE)
        self.assertEqual(check["target"], TARGET)
        self.assertIn("complete", check["guidance"])
        self.assertEqual(self.manager.journal, before)
        self.assertEqual(self.source, original)
        self.assertEqual(self.reads, [])

    async def test_readiness_points_to_pricing_when_disabled(self):
        self.meta.pop()  # Update the list already held by the mocked Recorder.
        self.hass.data = {}
        result = await self.manager.async_readiness()
        self.assertEqual(result["status"], "pricing_disabled")
        self.assertIn("Configure", result["guidance"])

    async def test_readiness_shows_cost_writer_errors_not_fake_success(self):
        self.entry.options["pricing_mode"] = "custom_time_of_use"
        self.hass.data = {
            "palpulse_cost": {
                self.entry.entry_id: types.SimpleNamespace(status={
                    "last_costed_hour": None,
                    "last_error": "Free allowance cannot be established after missing hour",
                }),
            },
        }
        self.meta.pop()  # Update the list already held by the mocked Recorder.
        result = await self.manager.async_readiness()
        self.assertEqual(result["status"], "cost_writer_error")
        self.assertIn("missing hour", result["guidance"])
        self.assertNotIn("ready", result["status"])

    async def test_readiness_checks_real_target_rows_before_allowing_preview(self):
        self.entry.options["pricing_mode"] = "custom_time_of_use"
        self.hass.data = {
            "palpulse_cost": {
                self.entry.entry_id: types.SimpleNamespace(status={
                    "last_costed_hour": self.cutover,
                    "last_error": None,
                }),
            },
        }
        result = await self.manager.async_readiness()
        self.assertEqual(result["status"], "ready")
        self.assertIn("2 cost rows", result["guidance"])
        self.assertEqual(self.manager.journal, {})
        self.assertEqual(self.reads[0][0], TARGET)

    async def test_blank_source_blocks_preview_without_changes(self):
        self.manager.source_id = ""
        with self.assertRaisesRegex(
            plan_mod.MigrationValidationError, "No historical electricity cost source"
        ):
            await self.manager._plan()

    async def test_source_with_no_real_hourly_state_is_rejected(self):
        self.source[0].pop("state")
        with self.assertRaisesRegex(
            plan_mod.MigrationValidationError, "hourly closing states"
        ):
            await self.manager._plan()

    async def test_cumulative_source_still_uses_existing_sum_path(self):
        self.manager.source_mode = cost_mod.SOURCE_CUMULATIVE
        self.source = [
            {"start": self.cutover - 2 * HOUR, "state": 18.0, "sum": 102.0},
            {"start": self.cutover - HOUR, "state": 18.5, "sum": 102.5},
        ]
        self.meta[0]["has_sum"] = True
        plan = await self.manager._plan()
        self.assertEqual(plan.count, 2)
        self.assertEqual(plan.hours[-1][2], 0)


if __name__ == "__main__":
    unittest.main()
