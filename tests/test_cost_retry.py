"""Verify independent, durable cost replay after an HA Recorder interruption."""

from __future__ import annotations
import asyncio
import importlib
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
        self.addCleanup(self.patch_read.stop)
        self.addCleanup(self.patch_write.stop)
        await self.recorder.async_load()

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
