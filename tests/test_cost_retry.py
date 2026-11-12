"""Verify independent, durable cost replay after an HA Recorder interruption."""

from __future__ import annotations
import asyncio
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_importer as base

mod = importlib.import_module("custom_components.palpulse.cost_recorder")
history = importlib.import_module("custom_components.palpulse.history")
MAC = base.MAC
BASE = base.BASE


def build_hour(start: int, total_start: int):
    rows = {
        start + i * 60: history.Record(
            start + i * 60, 10, total_start + (i + 1) * 10, base.CAL,
        )
        for i in range(60)
    }
    return history.complete_hour(rows, start), rows


class CostRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        base.FakeStore.VALUES.clear()
        self.rows = {}
        self.hass = types.SimpleNamespace(
            config=types.SimpleNamespace(time_zone="Australia/Melbourne"),
            async_add_executor_job=lambda callback: asyncio.to_thread(callback),
        )
        self.entry = types.SimpleNamespace(options={
            "pricing_mode": "custom_flat",
            "flat_cents_per_kwh": 20.0,
            "daily_supply_cents": 0.0,
        })
        self.recorder = mod.PalPulseCostRecorder(self.hass, self.entry, MAC)

        def read(hass, start, end, ids, period, units, fields):
            result = [
                dict(row) for stamp, row in sorted(self.rows.items())
                if start.timestamp() <= stamp < end.timestamp()
            ]
            return {next(iter(ids)): result}

        def write(hass, metadata, entries):
            for row in entries:
                self.rows[int(row["start"].timestamp())] = {
                    "start": int(row["start"].timestamp()),
                    "state": row["state"], "sum": row["sum"],
                }

        self.patch_read = patch.object(mod, "statistics_during_period", read)
        self.patch_write = patch.object(mod, "async_add_external_statistics", write)
        self.patch_read.start()
        self.patch_write.start()
        self.patch_recorder = patch.object(mod, "get_instance", lambda hass: base.FakeRecorder())
        self.patch_recorder.start()
        self.addCleanup(self.patch_recorder.stop)
        self.addCleanup(self.patch_read.stop)
        self.addCleanup(self.patch_write.stop)
        await self.recorder.async_load()

    async def test_pending_cost_recovers_at_startup_without_new_meter_data(self):
        hour, samples = build_hour(BASE, 40000)
        with patch.object(
            self.recorder, "_write_hour",
            AsyncMock(side_effect=RuntimeError("Recorder temporarily unavailable")),
        ):
            await self.recorder.async_import_verified_hour(hour, samples, base.CAL)
        self.assertEqual(self.recorder.status["pending_cost_hours"], 1)

        # Simulate HA starting after a crash. No Powerpal minute events arrive.
        restarted = mod.PalPulseCostRecorder(self.hass, self.entry, MAC)
        await restarted.async_load()
        events = []
        stopped = []

        timer = types.ModuleType("homeassistant.helpers.event")
        def track_interval(_hass, callback, period):
            self.assertEqual(period.total_seconds(), 300)
            events.append(callback)
            return lambda: stopped.append(True)
        timer.async_track_time_interval = track_interval
        with patch.dict(sys.modules, {"homeassistant.helpers.event": timer}):
            await restarted.async_start()
        self.assertEqual(restarted.status["pending_cost_hours"], 0)
        self.assertAlmostEqual(self.rows[BASE]["sum"], 0.0375)
        self.assertGreaterEqual(restarted.status["recovery_attempts"], 1)
        await restarted.async_flush()
        self.assertEqual(stopped, [True])

    async def test_periodic_cost_retry_does_not_need_new_energy_hour(self):
        hour, samples = build_hour(BASE, 40000)
        with patch.object(
            self.recorder, "_write_hour", AsyncMock(side_effect=RuntimeError("Transient failure"))
        ):
            await self.recorder.async_import_verified_hour(hour, samples, base.CAL)
        self.assertEqual(self.recorder.status["pending_cost_hours"], 1)

        registered = []
        timer = types.ModuleType("homeassistant.helpers.event")
        timer.async_track_time_interval = lambda _hass, callback, _period: (
            registered.append(callback) or (lambda: None)
        )
        # Energy dashboard check is not relevant to timer scheduling.
        with patch.dict(sys.modules, {"homeassistant.helpers.event": timer}):
            with patch.object(self.recorder, "async_refresh_cost_link", AsyncMock()):
                await self.recorder.async_start()
                self.assertEqual(self.recorder.status["pending_cost_hours"], 0)
                self.assertEqual(len(registered), 1)
                await registered[0](None)
        await self.recorder.async_flush()

    async def test_unjournalled_existing_hour_must_not_silently_disappear(self):
        hour, samples = build_hour(BASE, 40000)
        with patch.object(
            self.recorder, "_write_hour", AsyncMock(side_effect=RuntimeError("Recorder down"))
        ):
            await self.recorder.async_import_verified_hour(hour, samples, base.CAL)
        self.recorder._last_hour = BASE
        # Simulate a lost Recorder row despite the Store cursor.
        await self.recorder.async_recover_pending()
        self.assertEqual(self.recorder.status["pending_cost_hours"], 1)
        self.assertIn("missing", self.recorder.status["last_error"].lower())

    async def test_foreign_recorder_cost_hour_is_diagnosed_not_overwritten(self):
        first, records = build_hour(BASE, 40000)
        await self.recorder.async_import_verified_hour(first, records, base.CAL)
        first_total = self.rows[BASE]["sum"]

        # A row created by another source, or by a version with lost Store
        # state, is not proof of correct tariff and supply-charge accounting.
        foreign_start = BASE + 3600
        self.rows[foreign_start] = {
            "start": foreign_start,
            "state": first_total + 50.0,
            "sum": first_total + 50.0,
        }
        later, later_records = build_hour(BASE + 7200, 41200)
        await self.recorder.async_import_verified_hour(
            later, later_records, base.CAL,
        )
        status = self.recorder.status
        self.assertEqual(status["pending_cost_hours"], 1)
        self.assertEqual(status["recorder_conflict"], {
            "checkpoint_utc_hour": BASE,
            "first_unjournalled_utc_hour": foreign_start,
            "last_unjournalled_utc_hour": foreign_start,
            "existing_hour_count": 1,
            "queued_utc_hour": BASE + 7200,
        })
        self.assertIn("existing cost hour", status["last_error"])
        self.assertEqual(self.rows[foreign_start]["sum"], first_total + 50.0)
        self.assertNotIn(BASE + 7200, self.rows)

        restarted = mod.PalPulseCostRecorder(self.hass, self.entry, MAC)
        await restarted.async_load()
        self.assertEqual(restarted.status["recorder_conflict"], status["recorder_conflict"])
        self.assertEqual(restarted.status["pending_cost_hours"], 1)
        await restarted.async_recover_pending()
        self.assertEqual(self.rows[foreign_start]["sum"], first_total + 50.0)
        self.assertEqual(restarted.status["pending_cost_hours"], 1)

    async def test_link_setting_change_is_not_a_tariff_transition(self):
        self.assertEqual(len(self.recorder._tariff_profiles), 1)
        # The user's consent to maintain an Energy dashboard link, changes to
        # the ESPHome ACK action or historical migration source are not prices.
        self.entry.options["auto_link_energy_cost"] = True
        self.entry.options["history_ack_service"] = "esphome.test_ack"
        self.entry.options["legacy_energy_statistic_id"] = "sensor.previous"
        recovered = mod.PalPulseCostRecorder(self.hass, self.entry, MAC)
        await recovered.async_load()
        self.assertEqual(len(recovered._tariff_profiles), 1)
        self.assertEqual(recovered.status["tariff_transition_hours"], [])

        # Genuine tariff updates still create a protected transition hour.
        self.entry.options["flat_cents_per_kwh"] = 24.0
        changed = mod.PalPulseCostRecorder(self.hass, self.entry, MAC)
        await changed.async_load()
        self.assertEqual(len(changed._tariff_profiles), 2)
        self.assertEqual(len(changed.status["tariff_transition_hours"]), 1)

    async def test_failure_persists_cost_hour_and_replays_after_restart(self):
        hour, samples = build_hour(BASE, 40000)
        with patch.object(self.recorder, "_write_hour",
                          AsyncMock(side_effect=RuntimeError("Recorder busy"))):
            await self.recorder.async_import_verified_hour(hour, samples, base.CAL)
        self.assertEqual(self.recorder.status["pending_cost_hours"], 1)
        self.assertIn("Recorder busy", self.recorder.status["last_error"])
        self.assertEqual(self.rows, {})

        restored = mod.PalPulseCostRecorder(self.hass, self.entry, MAC)
        await restored.async_load()
        self.assertEqual(restored.status["pending_cost_hours"], 1)
        hour2, samples2 = build_hour(BASE + 3600, 40600)
        await restored.async_import_verified_hour(hour2, samples2, base.CAL)
        self.assertEqual(restored.status["pending_cost_hours"], 0)
        self.assertIsNone(restored.status["last_error"])
        self.assertAlmostEqual(self.rows[BASE]["sum"], 0.0375)
        self.assertAlmostEqual(self.rows[BASE + 3600]["sum"], 0.075)
