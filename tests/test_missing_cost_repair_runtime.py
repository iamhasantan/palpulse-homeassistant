"""Backup and crash regression tests for missing monetary Recorder gaps."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import importlib
import types
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import test_importer as base

runtime = importlib.import_module("custom_components.palpulse.missing_cost_repair")
tariffs = importlib.import_module("custom_components.palpulse.tariff_history")
reconcile = importlib.import_module("custom_components.palpulse.cost_reconcile_runtime")
METER = base.MAC
HOUR = 3600
ZONE = ZoneInfo("Australia/Melbourne")


class FakeStore:
    VALUES = {}
    def __init__(self, hass, version, key):
        self.key = key
    async def async_load(self):
        import copy
        return copy.deepcopy(self.VALUES.get(self.key))
    async def async_save(self, value):
        import copy
        self.VALUES[self.key] = copy.deepcopy(value)


class FakeRecorder:
    async def async_block_till_done(self):
        pass


class MissingCostRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeStore.VALUES.clear()
        self.start = int(datetime(2026, 11, 11, 8, tzinfo=ZONE).timestamp())
        self.energy_id = f"palpulse:powerpal_{METER}_energy"
        self.cost_id = f"palpulse:powerpal_{METER}_cost"
        self.energy = {}
        for i in range(-1, 5):
            stamp = self.start + i * HOUR
            self.energy[stamp] = {
                "start": stamp, "state": 100 + i, "sum": 200 + i,
                "change": 1.0,
            }
        self.cost = {
            self.start: {"start": self.start, "state": 2.0, "sum": 2.0, "change": .2024},
            self.start + HOUR: {"start": self.start + HOUR, "state": 2.2024, "sum": 2.2024, "change": .2024},
            self.start + 3*HOUR: {"start": self.start + 3*HOUR, "state": 2.2024, "sum": 2.2024, "change": 0.0},
            self.start + 4*HOUR: {"start": self.start + 4*HOUR, "state": 2.2024, "sum": 2.2024, "change": 0.0},
        }
        options = {
            "pricing_mode": "custom_time_of_use",
            "peak_cents_per_kwh": 34.936, "shoulder_cents_per_kwh": 0,
            "offpeak_cents_per_kwh": 20.24, "daily_supply_cents": 118.217,
            "daily_free_cap_kwh": 15, "free_excess_cents_per_kwh": 16.412,
            "weekday_free": "11:00-16:00", "weekend_free": "11:00-16:00",
            "weekday_peak": "16:00-21:00", "weekend_peak": "16:00-21:00",
            "weekday_shoulder": "", "weekend_shoulder": "",
        }
        versions = tariffs.upsert_tariff_version(
            [], "2026-11-11", options, ZONE.key,
            today=date(2026, 11, 14),
        )
        self.entry = types.SimpleNamespace(
            entry_id="test", options=dict(options, tariff_versions=versions),
        )
        self.importer = types.SimpleNamespace(
            mac=METER, statistic_id=self.energy_id, _lock=asyncio.Lock(),
        )
        self.writer = types.SimpleNamespace(
            statistic_id=self.cost_id, _lock=asyncio.Lock(),
            _last_hour=self.start+4*HOUR, _sum=2.2024,
            _pending=[], _unpriced_hours={self.start+2*HOUR}, _error="historical",
            _store=FakeStore(None, 1, "writer"),
        )
        self.writer._snapshot = lambda: {
            "sum": self.writer._sum,
            "last_hour": self.writer._last_hour,
        }
        self.manager = types.SimpleNamespace(
            busy=False,
            _backup=AsyncMock(return_value="backup-verified"),
            _verify_prior_backup=AsyncMock(),
        )
        self.hass = types.SimpleNamespace(
            config=types.SimpleNamespace(time_zone=ZONE.key),
            data={
                "palpulse": {"test": self.importer},
                "palpulse_cost": {"test": self.writer},
                "palpulse_migration": {"test": self.manager},
                "palpulse_cost_migration": {},
            },
        )
        self.fail_one_write = False
        self.write_calls = 0

        async def read(_hass, stat_ids, start, end):
            source = {self.energy_id: self.energy, self.cost_id: self.cost}
            return {
                key: [dict(x) for stamp, x in sorted(source[key].items())
                      if start <= stamp < end]
                for key in stat_ids
            }

        def write(_hass, metadata, rows):
            self.write_calls += 1
            for i, item in enumerate(rows):
                stamp = int(item["start"].timestamp())
                self.cost[stamp] = {
                    "start": stamp, "sum": item["sum"],
                    "state": item["state"], "change": 0.0,
                }
                if self.fail_one_write and i == 0:
                    self.fail_one_write = False
                    raise RuntimeError("Simulated Recorder crash after first upsert")

        self.patchers = [
            patch.object(runtime, "_read", read),
            patch.object(runtime, "Store", FakeStore),
            patch.object(runtime, "async_add_external_statistics", write),
            patch.object(runtime, "get_instance", lambda hass: FakeRecorder()),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    async def test_preview_does_not_modify_recorder(self):
        before = dict(self.cost)
        preview = await runtime.preview(self.hass, self.entry)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["missing_hours"], 1)
        self.assertAlmostEqual(preview["missing_charges"], .2024)
        self.assertEqual(before, self.cost)
        self.manager._backup.assert_not_awaited()

    async def test_verified_backup_before_write_and_preserved_cost_charges(self):
        report = await runtime.preview(self.hass, self.entry)
        result = await runtime.apply(
            self.hass, self.entry, confirm=True, digest=report["digest"],
        )
        self.assertTrue(result["applied"])
        self.assertEqual(self.manager._backup.await_count, 1)
        self.manager._verify_prior_backup.assert_awaited_once()
        self.assertAlmostEqual(self.cost[self.start+2*HOUR]["sum"], 2.4048)
        self.assertAlmostEqual(self.cost[self.start+3*HOUR]["sum"], 2.4048)
        self.assertAlmostEqual(self.writer._sum, 2.4048)
        self.assertEqual(self.writer._unpriced_hours, set())
        self.assertEqual((await runtime.preview(self.hass, self.entry))["status"], "complete")

    async def test_crash_resume_never_double_bills_or_repeats_backup(self):
        self.fail_one_write = True
        report = await runtime.preview(self.hass, self.entry)
        with self.assertRaisesRegex(RuntimeError, "Simulated"):
            await runtime.apply(self.hass, self.entry, confirm=True, digest=report["digest"])
        status = await runtime.preview(self.hass, self.entry)
        self.assertEqual(status["status"], "resume_required")
        result = await runtime.apply(self.hass, self.entry, confirm=True)
        self.assertTrue(result["applied"])
        self.assertEqual(self.manager._backup.await_count, 1)
        self.assertAlmostEqual(self.writer._sum, 2.4048)

    async def test_missing_backup_refuses_without_any_write(self):
        self.manager._backup.side_effect = ValueError("No verified Recorder backup")
        with self.assertRaisesRegex(ValueError, "backup"):
            await runtime.apply(self.hass, self.entry, confirm=True)
        self.assertEqual(self.write_calls, 0)

    async def test_changed_energy_during_backup_refuses_without_writes(self):
        async def mutate(reason):
            self.energy[self.start+2*HOUR]["state"] += 0.25
            return "backup-verified"
        self.manager._backup.side_effect = mutate
        with self.assertRaises(ValueError):
            await runtime.apply(self.hass, self.entry, confirm=True)
        self.assertEqual(self.write_calls, 0)

    async def test_saved_interrupted_legacy_journal_requires_manual_review(self):
        FakeStore.VALUES[f"palpulse_cost_reconcile_{METER}"] = {
            "status": "writing", "rows_committed": 12, "backup_id": "old",
        }
        with self.assertRaisesRegex(ValueError, "Manual approval"):
            await runtime.apply(self.hass, self.entry, confirm=True, automatic=True)
        self.assertEqual(self.write_calls, 0)
        result = await runtime.apply(self.hass, self.entry, confirm=True)
        self.assertTrue(result["applied"])
        old = FakeStore.VALUES[f"palpulse_cost_reconcile_{METER}"]
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["backup_id"], "old")


if __name__ == "__main__":
    unittest.main()
