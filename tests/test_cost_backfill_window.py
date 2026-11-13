"""The cost recovery cursor must never jump over an old backlog."""
from __future__ import annotations

import importlib
import types
import unittest
from unittest.mock import AsyncMock

import test_importer as base

writer_module = importlib.import_module("custom_components.palpulse.cost_recorder")
HOUR = 3600


class ForwardCostRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_week_plus_outage_reads_from_saved_checkpoint(self):
        start = base.BASE
        latest = start + 300 * HOUR
        hass = types.SimpleNamespace(
            data={}, config=types.SimpleNamespace(time_zone="Australia/Melbourne"),
        )
        entry = types.SimpleNamespace(entry_id="meter-one", options={})
        writer = writer_module.PalPulseCostRecorder(hass, entry, base.MAC)
        writer._last_hour = start
        writer.energy_importer = types.SimpleNamespace(
            _calibration=base.CAL,
            health={"last_completed_utc_hour": latest},
        )
        writer._read = AsyncMock(return_value=[])
        await writer._recover_from_energy_statistics()
        writer._read.assert_awaited_once_with(
            start, start + 169 * HOUR,
            statistic_id=f"palpulse:powerpal_{base.MAC}_energy",
        )

    async def test_short_outage_reads_through_latest_hour(self):
        start = base.BASE
        latest = start + 12 * HOUR
        hass = types.SimpleNamespace(
            data={}, config=types.SimpleNamespace(time_zone="Australia/Melbourne"),
        )
        entry = types.SimpleNamespace(entry_id="meter-one", options={})
        writer = writer_module.PalPulseCostRecorder(hass, entry, base.MAC)
        writer._last_hour = start
        writer.energy_importer = types.SimpleNamespace(
            _calibration=base.CAL,
            health={"last_completed_utc_hour": latest},
        )
        writer._read = AsyncMock(return_value=[])
        await writer._recover_from_energy_statistics()
        writer._read.assert_awaited_once_with(
            start, latest + HOUR,
            statistic_id=f"palpulse:powerpal_{base.MAC}_energy",
        )


if __name__ == "__main__":
    unittest.main()
