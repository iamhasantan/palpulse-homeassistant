"""Crash, backup, overwrite and checkpoint tests for historical cost correction."""
from __future__ import annotations

import asyncio
import importlib
import time
import types
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import test_importer as base

runtime = importlib.import_module("custom_components.palpulse.cost_reconcile_runtime")
tariffs = importlib.import_module("custom_components.palpulse.tariff_history")
billing = importlib.import_module("custom_components.palpulse.cost_reconcile_plan")
MAC = base.MAC
ZONE = ZoneInfo("Australia/Melbourne")


class FakeRecorder:
    async def async_add_executor_job(self, fn):
        return fn()

    async def async_block_till_done(self):
        return None


class MockStore:
    VALUES = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return self.VALUES.get(self.key)

    async def async_save(self, snapshot):
        import copy
        self.VALUES[self.key] = copy.deepcopy(snapshot)


class CostReconciliationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        MockStore.VALUES.clear()
        self.energy_id = f"palpulse:powerpal_{MAC}_energy"
        self.cost_id = f"palpulse:powerpal_{MAC}_cost"
        self.start, self.end = billing.billing_range(
            "2026-11-09", "2026-11-09", ZONE.key,
            today=date(2026, 11, 12),
        )
        self.energy_rows = {
            self.start - 3600: {
                "start": self.start - 3600, "state": 99., "sum": 900.,
                "change": 0.,
            },
        }
        for h, stamp in enumerate(range(self.start, self.end, 3600)):
            self.energy_rows[stamp] = {
                "start": stamp,
                "state": 99. + (h + 1) * 2.,
                "sum": 900. + (h + 1) * 2.,
                "change": 2.0,
            }
        # The live cost was incorrectly recorded for the last hour only.
        self.cost_rows = {
            self.end - 3600: {
                "start": self.end - 3600, "sum": 2.65,
                "state": 2.65, "change": 2.65,
            },
        }
        plan = {
            "pricing_mode": "custom_time_of_use",
            "peak_cents_per_kwh": 34.936,
            "shoulder_cents_per_kwh": 0.,
            "offpeak_cents_per_kwh": 20.24,
            "daily_supply_cents": 118.217,
            "daily_free_cap_kwh": 15.,
            "free_excess_cents_per_kwh": 16.412,
            "weekday_free": "11:00-16:00",
            "weekend_free": "11:00-16:00",
            "weekday_peak": "16:00-21:00",
            "weekend_peak": "16:00-21:00",
            "weekday_shoulder": "",
            "weekend_shoulder": "",
        }
        versions = tariffs.upsert_tariff_version(
            [], "2026-11-09", plan, ZONE.key,
            today=date(2026, 11, 12),
        )
        self.entry = types.SimpleNamespace(
            entry_id="meter-one", data={"meter_mac": MAC},
            options=dict(plan, tariff_plan_start_date="2026-11-09",
                         tariff_versions=versions),
        )
        self.importer = types.SimpleNamespace(
            mac=MAC, statistic_id=self.energy_id, _lock=asyncio.Lock(),
        )
        self.writer = types.SimpleNamespace(
            statistic_id=self.cost_id, _lock=asyncio.Lock(),
            _last_hour=self.end - 3600, _sum=2.65,
            _pending=[], _last_supply_date="2026-11-09",
            _free_used={}, _free_uncertain_dates=set(),
            _unpriced_hours=set(), _error=None,
            _store=MockStore(None, 1, "writer"),
        )
        self.writer._snapshot = lambda: {
            "last_hour": self.writer._last_hour,
            "sum": self.writer._sum,
        }
        self.manager = types.SimpleNamespace(
            busy=False, _backup=AsyncMock(return_value="verified-backup"),
            _verify_prior_backup=AsyncMock(),
        )
        self.hass = types.SimpleNamespace(
            config=types.SimpleNamespace(time_zone=ZONE.key),
            data={
                "palpulse": {"meter-one": self.importer},
                "palpulse_cost": {"meter-one": self.writer},
                "palpulse_migration": {"meter-one": self.manager},
                "palpulse_cost_migration": {},
            },
        )
        def read(hass, start, end, statistic_ids, period, units, types):
            lookup = {self.energy_id: self.energy_rows, self.cost_id: self.cost_rows}
            return {
                sid: [
                    dict(row) for stamp, row in sorted(lookup[sid].items())
                    if start.timestamp() <= stamp < end.timestamp()
                ] for sid in statistic_ids
            }
        self.write_calls = 0
        self.fail_on_call = None
        def write(hass, metadata, rows):
            self.write_calls += 1
            if self.write_calls == self.fail_on_call:
                raise RuntimeError("Synthetic Recorder interruption")
            for item in rows:
                stamp = int(item["start"].timestamp())
                self.cost_rows[stamp] = {
                    "start": stamp,
                    "state": item["state"], "sum": item["sum"],
                    "change": None,
                }
        self.patches = [
            patch.object(runtime, "statistics_during_period", read),
            patch.object(runtime, "async_add_external_statistics", write),
            patch.object(runtime, "get_instance", lambda hass: FakeRecorder()),
            patch.object(runtime, "Store", MockStore),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def preview(self):
        return await runtime.preview_cost_reconciliation(
            self.hass, self.entry, "2026-11-09", "2026-11-09",
        )

    async def apply(self, digest):
        return await runtime.apply_cost_reconciliation(
            self.hass, self.entry,
            "2026-11-09", "2026-11-09", digest,
        )

    async def test_rebuilds_both_state_and_sum_after_backup(self):
        plan = await self.preview()
        self.assertAlmostEqual(plan["last_sum"], 10.34297, places=5)
        result = await self.apply(plan["digest"])
        self.assertTrue(result["applied"])
        self.manager._backup.assert_awaited_once()
        self.assertAlmostEqual(self.cost_rows[self.end - 3600]["sum"], 10.34297, places=5)
        self.assertEqual(self.cost_rows[self.end - 3600]["state"],
                         self.cost_rows[self.end - 3600]["sum"])
        self.assertAlmostEqual(self.writer._sum, 10.34297, places=5)
        self.assertEqual(
            MockStore.VALUES[f"palpulse_cost_reconcile_{MAC}"]["status"],
            "complete",
        )

    async def test_proven_pending_job_retires_only_after_verified_write(self):
        self.writer._pending = [{"start": self.end - 3600, "samples": []}]
        plan = await self.preview()
        result = await self.apply(plan["digest"])
        self.assertTrue(result["applied"])
        self.assertEqual(self.writer._pending, [])
        self.assertAlmostEqual(self.cost_rows[self.end - 3600]["sum"], 10.34297, places=5)

    async def test_unproven_pending_job_blocks_without_backup(self):
        # A pending charge for a truly missing hour must remain journalled.
        self.writer._pending = [{"start": self.end - 25 * 3600, "samples": []}]
        plan = await self.preview()
        with self.assertRaisesRegex(ValueError, "Queued cost jobs"):
            await self.apply(plan["digest"])
        self.manager._backup.assert_not_awaited()
        self.assertEqual(len(self.writer._pending), 1)

    async def test_invalid_earlier_cost_baseline_is_read_only(self):
        self.cost_rows[self.start - 3600] = {
            "start": self.start - 3600,
            "sum": float("nan"), "state": float("nan"), "change": 0.0,
        }
        plan = await self.preview()
        self.assertFalse(plan["baseline_verified"])
        with self.assertRaisesRegex(ValueError, "monetary baseline"):
            await self.apply(plan["digest"])
        self.manager._backup.assert_not_awaited()
        self.assertEqual(self.cost_rows[self.end - 3600]["sum"], 2.65)

    async def test_cost_counter_rebase_is_correctable_when_energy_proves_hour(self):
        prior, reset = self.start + 6 * 3600, self.start + 7 * 3600
        self.cost_rows[prior] = {
            "start": prior, "state": 5.0, "sum": 5.0,
        }
        self.cost_rows[reset] = {
            "start": reset, "state": 2.0, "sum": 2.0,
        }
        plan = await self.preview()
        self.assertTrue(plan["repair_safe"])
        self.assertEqual(
            len([x for x in plan["cost_counter_conflicts"]
                 if x["kind"] == "counter_decrease"]), 1,
        )
        result = await self.apply(plan["digest"])
        self.assertTrue(result["applied"])
        self.assertAlmostEqual(self.cost_rows[self.end - 3600]["sum"],
                               plan["last_sum"], places=5)
        self.assertAlmostEqual(self.writer._sum, plan["last_sum"], places=5)
        self.manager._backup.assert_awaited_once()

    async def test_unverifiable_reset_hour_blocks_historical_mutation(self):
        prior, reset = self.start + 6 * 3600, self.start + 7 * 3600
        self.cost_rows[prior] = {
            "start": prior, "state": 5.0, "sum": 5.0,
        }
        self.cost_rows[reset] = {
            "start": reset, "state": 2.0, "sum": 2.0,
        }
        del self.energy_rows[reset]
        plan = await self.preview()
        self.assertFalse(plan["repair_safe"])
        with self.assertRaisesRegex(ValueError, "cost counter conflict"):
            await self.apply(plan["digest"])
        self.manager._backup.assert_not_awaited()
        self.assertEqual(self.cost_rows[reset]["sum"], 2.0)

    async def test_changed_plan_refused_without_backup_or_writer_changes(self):
        plan = await self.preview()
        self.energy_rows[self.start]["sum"] += 0.75
        with self.assertRaisesRegex(ValueError, "Preview again"):
            await self.apply(plan["digest"])
        self.manager._backup.assert_not_awaited()
        self.assertEqual(self.cost_rows[self.end - 3600]["sum"], 2.65)

    async def test_stale_backup_plan_aborts_before_recorder_mutation(self):
        plan = await self.preview()
        async def alter_source(*args):
            self.energy_rows[self.start]["sum"] += 0.75
            return "verified-backup"
        self.manager._backup.side_effect = alter_source
        with self.assertRaisesRegex(ValueError, "changed during backup"):
            await self.apply(plan["digest"])
        self.assertEqual(
            MockStore.VALUES[f"palpulse_cost_reconcile_{MAC}"]["status"],
            "aborted",
        )
        self.assertEqual(self.cost_rows[self.end - 3600]["sum"], 2.65)

    async def test_resume_refuses_changed_energy_after_partial_cost_writes(self):
        plan = await self.preview()
        self.fail_on_call = 2
        with self.assertRaisesRegex(RuntimeError, "Synthetic Recorder"):
            await self.apply(plan["digest"])
        saved = MockStore.VALUES[f"palpulse_cost_reconcile_{MAC}"]
        self.assertEqual(saved["status"], "writing")
        self.energy_rows[self.start]["state"] += 0.2
        self.fail_on_call = None
        with self.assertRaisesRegex(ValueError, "Meter energy changed"):
            await self.apply(plan["digest"])
        self.assertEqual(
            MockStore.VALUES[f"palpulse_cost_reconcile_{MAC}"]["status"],
            "writing",
        )

    async def test_resumes_interrupted_batched_writes_without_double_charge(self):
        plan = await self.preview()
        self.fail_on_call = 2
        with self.assertRaisesRegex(RuntimeError, "Synthetic Recorder"):
            await self.apply(plan["digest"])
        journal = MockStore.VALUES[f"palpulse_cost_reconcile_{MAC}"]
        self.assertEqual(journal["status"], "writing")
        self.assertEqual(journal["rows_committed"], 12)
        self.fail_on_call = None
        result = await self.apply(plan["digest"])
        self.assertTrue(result["applied"])
        self.assertAlmostEqual(self.writer._sum, 10.34297, places=5)
        self.manager._backup.assert_awaited_once()
        self.manager._verify_prior_backup.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
