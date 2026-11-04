"""Exercise the HACS importer with narrow Home Assistant API test doubles.

This checks Recorder submissions, UTC-hour alignment, deduplication, gaps and
persisted restart state without booting an actual Home Assistant instance.
Full Home Assistant and ESPHome integration testing is still required.
"""

from __future__ import annotations

import asyncio
from enum import IntEnum
import importlib
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


def _module(name: str, package: bool = False):
    value = types.ModuleType(name)
    if package:
        value.__path__ = []
    sys.modules[name] = value
    return value


homeassistant = _module("homeassistant", True)
ha_components = _module("homeassistant.components", True)
ha_recorder = _module("homeassistant.components.recorder", True)
ha_models = _module("homeassistant.components.recorder.models")
ha_statistics = _module("homeassistant.components.recorder.statistics")
ha_core = _module("homeassistant.core")
ha_const = _module("homeassistant.const")
ha_helpers = _module("homeassistant.helpers", True)
ha_storage = _module("homeassistant.helpers.storage")


class StatisticMeanType(IntEnum):
    NONE = 0


ha_models.StatisticMeanType = StatisticMeanType
RECORDER_IMPORTS = []


def async_add_external_statistics(hass, metadata, statistics):
    RECORDER_IMPORTS.append((metadata, list(statistics)))


ha_statistics.async_add_external_statistics = async_add_external_statistics
ha_core.Event = type("Event", (), {})
ha_core.HomeAssistant = type("HomeAssistant", (), {})


def callback(func):
    """Model Home Assistant's event-loop-safe callback annotation."""
    func.__ha_callback = True
    return func


ha_core.callback = callback
ha_const.Platform = types.SimpleNamespace(BUTTON="button")


class FakeStore:
    VALUES = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return self.VALUES.get(self.key)

    async def async_save(self, value):
        self.VALUES[self.key] = value

    def async_delay_save(self, callback, delay):
        self.VALUES[self.key] = callback()


ha_storage.Store = FakeStore
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
palpulse = importlib.import_module("custom_components.palpulse")

BASE = 1793728800  # UTC hour aligned
MAC = "d8a5d6d91964"
CAL = 3200.0


def record_event(timestamp, pulses, total, calibration=CAL):
    return {
        "meter": "D8:A5:D6:D9:19:64",
        "timestamp": str(timestamp),
        "pulses": str(pulses),
        "total_pulses": str(total),
        "pulses_per_kwh": str(calibration),
    }


class ImporterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        RECORDER_IMPORTS.clear()
        FakeStore.VALUES.clear()
        self.hass = object()
        self.importer = palpulse.PalPulseImporter(self.hass, MAC)
        await self.importer.async_load()

    async def feed_hour(self, start, starting_pulses, skip=None):
        with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
            for minute in range(60):
                if minute == skip:
                    continue
                await self.importer.async_receive(
                    record_event(start + minute * 60, 10, starting_pulses + (minute + 1) * 10)
                )

    async def test_event_listener_schedules_records_on_event_loop(self):
        """ESPHome events must use an event-loop callback, not an executor callback."""
        listeners = []

        def listen(event_type, handler):
            self.assertEqual(event_type, "esphome.palpulse_record")
            self.assertTrue(getattr(handler, "__ha_callback", False))
            listeners.append(handler)
            return lambda: None

        async def forward_platforms(entry, platforms):
            self.assertEqual(platforms, ["button"])

        hass = types.SimpleNamespace(
            bus=types.SimpleNamespace(async_listen=listen),
            data={},
            config_entries=types.SimpleNamespace(
                async_forward_entry_setups=forward_platforms
            ),
            async_create_task=asyncio.create_task,
        )
        entry = types.SimpleNamespace(
            data={"meter_mac": MAC},
            entry_id="thread-safe-test",
            async_on_unload=lambda _: None,
        )
        self.assertTrue(await palpulse.async_setup_entry(hass, entry))
        self.assertEqual(len(listeners), 1)

        event = types.SimpleNamespace(data=record_event(BASE, 10, 40010))
        with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
            listeners[0](event)
            await asyncio.sleep(0)
        self.assertIn(
            BASE, hass.data["palpulse"][entry.entry_id]._records
        )

    async def test_hourly_import_and_duplicate(self):
        await self.feed_hour(BASE, 40000)
        self.assertEqual(len(RECORDER_IMPORTS), 1)
        meta, rows = RECORDER_IMPORTS[0]
        self.assertEqual(meta["statistic_id"], "palpulse:powerpal_d8a5d6d91964_energy")
        self.assertEqual(meta["source"], "palpulse")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["start"].timestamp(), BASE - 3600)
        self.assertEqual(rows[1]["start"].timestamp(), BASE)
        self.assertEqual(rows[0]["sum"], 0)
        self.assertAlmostEqual(rows[1]["sum"], 600 / 3200)
        with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
            await self.importer.async_receive(record_event(BASE + 59 * 60, 10, 40600))
        self.assertEqual(len(RECORDER_IMPORTS), 1)

    async def test_incomplete_hour_never_misattributes(self):
        await self.feed_hour(BASE, 40000)
        await self.feed_hour(BASE + 3600, 40600, skip=13)
        await self.feed_hour(BASE + 7200, 41200)
        self.assertEqual(len(RECORDER_IMPORTS), 1)

    async def test_second_hour_and_restart(self):
        await self.feed_hour(BASE, 40000)
        restored = palpulse.PalPulseImporter(self.hass, MAC)
        await restored.async_load()
        self.assertEqual(restored._last_hour, BASE)
        self.importer = restored
        await self.feed_hour(BASE + 3600, 40600)
        self.assertEqual(len(RECORDER_IMPORTS), 2)
        self.assertEqual(len(RECORDER_IMPORTS[1][1]), 1)
        self.assertEqual(RECORDER_IMPORTS[1][1][0]["start"].timestamp(), BASE + 3600)
        self.assertAlmostEqual(RECORDER_IMPORTS[1][1][0]["sum"], 1200 / 3200)

    async def test_calibration_change_halts(self):
        await self.feed_hour(BASE, 40000)
        with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
            await self.importer.async_receive(
                record_event(BASE + 3600, 10, 40610, 1000)
            )
        self.assertTrue(self.importer._halted)
        self.assertEqual(len(RECORDER_IMPORTS), 1)


if __name__ == "__main__":
    unittest.main()
