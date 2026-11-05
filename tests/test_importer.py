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
ha_helpers_recorder = _module("homeassistant.helpers.recorder")

class FakeRecorder:
    async def async_block_till_done(self):
        return None

ha_helpers_recorder.get_instance = lambda hass: FakeRecorder()



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
ha_const.Platform = types.SimpleNamespace(BUTTON="button", SENSOR="sensor")


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
            self.assertEqual(platforms, ["button", "sensor"])

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

    async def test_incomplete_hour_skips_without_misattributing_energy(self):
        await self.feed_hour(BASE, 40000)
        await self.feed_hour(BASE + 3600, 40600, skip=13)
        await self.feed_hour(BASE + 7200, 41200)
        self.assertEqual(len(RECORDER_IMPORTS), 2)
        recovered = RECORDER_IMPORTS[1][1][0]
        self.assertEqual(recovered["start"].timestamp(), BASE + 7200)
        # Initial 600 pulses + resumed valid hour's 600, not the intervening
        # missing hour's 600. No catch-up spike.
        self.assertAlmostEqual(recovered["sum"], 1200 / 3200)
        self.assertEqual(self.importer.health["skipped_hours"], 1)
        self.assertEqual(self.importer.health["last_gap_utc_hour"], BASE + 3600)

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

    async def feed_custom_hour(self, start, base, *, now, skip=None, pulses=10):
        with patch.object(palpulse.time, "time", return_value=now):
            for minute in range(60):
                if minute == skip:
                    continue
                await self.importer.async_receive(
                    record_event(start + minute * 60, pulses,
                                 base + (minute + 1) * pulses)
                )

    async def test_waits_for_late_records_before_skipping(self):
        await self.feed_custom_hour(BASE, 40000, now=BASE + 2 * 3600)
        await self.feed_custom_hour(
            BASE + 3600, 40600, now=BASE + 2 * 3600, skip=13
        )
        # Following hour has already completed but grace period is not over.
        await self.feed_custom_hour(BASE + 7200, 41200, now=BASE + 3 * 3600 + 500)
        self.assertEqual(len(RECORDER_IMPORTS), 1)
        self.assertEqual(self.importer.health["skipped_hours"], 0)

        # Delayed missing minute arrives BEFORE the recovery grace expires.
        with patch.object(palpulse.time, "time", return_value=BASE + 3 * 3600 + 600):
            await self.importer.async_receive(
                record_event(BASE + 3600 + 13 * 60, 10, 40740)
            )
        self.assertEqual(len(RECORDER_IMPORTS), 3)
        self.assertEqual(self.importer.health["skipped_hours"], 0)
        self.assertAlmostEqual(RECORDER_IMPORTS[-1][1][0]["sum"], 1800 / CAL)

    async def test_multiple_missing_hours_and_restart_do_not_spike(self):
        await self.feed_custom_hour(BASE, 40000, now=BASE + 2 * 3600)
        # Two completely missing UTC hours, even though Powerpal consumed
        # energy during them and its real cumulative total advanced.
        await self.feed_custom_hour(
            BASE + 3 * 3600, 41800, now=BASE + 5 * 3600
        )
        self.assertEqual(self.importer.health["skipped_hours"], 2)
        self.assertAlmostEqual(RECORDER_IMPORTS[-1][1][0]["sum"], 1200 / CAL)

        restored = palpulse.PalPulseImporter(self.hass, MAC)
        await restored.async_load()
        self.importer = restored
        self.assertEqual(restored.health["skipped_hours"], 2)
        self.assertEqual(restored._accounted_pulses, 1200)
        await self.feed_custom_hour(
            BASE + 4 * 3600, 42400, now=BASE + 6 * 3600
        )
        self.assertAlmostEqual(RECORDER_IMPORTS[-1][1][0]["sum"], 1800 / CAL)
        self.assertEqual(RECORDER_IMPORTS[-1][1][0]["start"].timestamp(),
                         BASE + 4 * 3600)
        self.assertEqual(len(RECORDER_IMPORTS), 3)
        self.assertEqual(restored.health["skipped_hours"], 2)

    async def test_existing_stalled_ledger_recovers_after_update(self):
        # Simulate the persisted cursor from 0.1.0b7 with no new fields,
        # an incomplete hour and several later complete hours buffered.
        legacy = {
            "calibration": CAL,
            "baseline_pulses": 40000,
            "last_hour": BASE,
            "last_timestamp": BASE + 59 * 60,
            "last_total": 40600,
            "halted": False,
            "records": {},
        }
        for minute in range(60):
            stamp = BASE + minute * 60
            legacy["records"][str(stamp)] = [10, 40000 + 10 * (minute + 1)]
        for minute in range(60):
            if minute != 5:
                stamp = BASE + 3600 + minute * 60
                legacy["records"][str(stamp)] = [10, 40600 + 10 * (minute + 1)]
        for hour, base in [(BASE + 7200, 41200), (BASE + 10800, 41800)]:
            for minute in range(60):
                stamp = hour + minute * 60
                legacy["records"][str(stamp)] = [10, base + 10 * (minute + 1)]
        FakeStore.VALUES[f"palpulse_{MAC}"] = legacy
        self.importer = palpulse.PalPulseImporter(self.hass, MAC)
        await self.importer.async_load()
        self.assertEqual(self.importer._accounted_pulses, 600)
        with patch.object(palpulse.time, "time", return_value=BASE + 6 * 3600):
            await self.importer.async_receive(
                record_event(BASE + 4 * 3600, 10, 42410)
            )
        self.assertEqual(self.importer._last_hour, BASE + 3 * 3600)
        self.assertEqual(self.importer.health["skipped_hours"], 1)
        self.assertEqual(len(RECORDER_IMPORTS), 2)
        self.assertAlmostEqual(RECORDER_IMPORTS[0][1][0]["sum"], 1200 / CAL)
        self.assertAlmostEqual(RECORDER_IMPORTS[1][1][0]["sum"], 1800 / CAL)
        # Duplicate replay of an already imported record cannot double count.
        with patch.object(palpulse.time, "time", return_value=BASE + 6 * 3600):
            await self.importer.async_receive(record_event(
                BASE + 3 * 3600 + 59 * 60, 10, 42400
            ))
        self.assertEqual(len(RECORDER_IMPORTS), 2)

    async def test_counter_reset_across_gap_still_halts(self):
        await self.feed_custom_hour(BASE, 40000, now=BASE + 2 * 3600)
        # Meter total fell from 40600 to ~1500. Don't treat this as a benign
        # missing data gap and don't import a misleading energy value.
        await self.feed_custom_hour(
            BASE + 2 * 3600, 1000, now=BASE + 5 * 3600
        )
        self.assertTrue(self.importer._halted)
        self.assertEqual(len(RECORDER_IMPORTS), 1)
        self.assertIn("discontinuous", self.importer.health["message"])

    async def test_no_events_health_timeout(self):
        await self.feed_custom_hour(BASE, 40000, now=BASE + 2 * 3600)
        with patch.object(palpulse.time, "time", return_value=BASE + 2 * 3600 + 60):
            self.assertEqual(self.importer.health["status"], "Active")
        with patch.object(palpulse.time, "time", return_value=BASE + 2 * 3600 + 1260):
            self.assertEqual(self.importer.health["status"], "No Events")

    async def test_old_cursor_loads_without_rezeroing_accepted_sum(self):
        await self.feed_custom_hour(BASE, 40000, now=BASE + 2 * 3600)
        persisted = FakeStore.VALUES[f"palpulse_{MAC}"].copy()
        persisted.pop("accounted_pulses")
        FakeStore.VALUES[f"palpulse_{MAC}"] = persisted
        restored = palpulse.PalPulseImporter(self.hass, MAC)
        await restored.async_load()
        self.assertEqual(restored._accounted_pulses, 600)
        self.assertFalse(restored._halted)

    async def test_waiting_gap_logs_once_instead_of_hundreds(self):
        await self.feed_custom_hour(BASE, 40000, now=BASE + 2 * 3600)
        await self.feed_custom_hour(
            BASE + 3600, 40600, now=BASE + 2 * 3600, skip=9
        )
        with self.assertLogs(palpulse._LOGGER, level="WARNING") as observed:
            await self.feed_custom_hour(
                BASE + 2 * 3600, 41200, now=BASE + 3 * 3600 + 10
            )
            with patch.object(palpulse.time, "time",
                              return_value=BASE + 3 * 3600 + 30):
                for minute in range(10):
                    await self.importer.async_receive(record_event(
                        BASE + 3 * 3600 + minute * 60,
                        10, 41800 + (minute + 1) * 10
                    ))
        blocked = [line for line in observed.output if "incomplete UTC hour" in line]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(self.importer.health["blocked_utc_hour"], BASE + 3600)

    async def test_recorder_failure_does_not_advance_import_cursor(self):
        class FailedRecorder:
            async def async_block_till_done(self):
                raise RuntimeError("Recorder busy")

        with patch.object(palpulse, "get_instance", return_value=FailedRecorder()):
            await self.feed_custom_hour(BASE, 40000, now=BASE + 2 * 3600)
        self.assertIsNone(self.importer._last_hour)
        self.assertEqual(self.importer.health["status"], "Recorder Error")
        self.assertIn("Recorder busy", self.importer.health["last_import_error"])
        # The next event replays exactly the same hour without resetting sums.
        with patch.object(palpulse.time, "time", return_value=BASE + 2 * 3600 + 10):
            await self.importer.async_receive(
                record_event(BASE + 3600, 10, 40610)
            )
        self.assertEqual(self.importer._last_hour, BASE)
        self.assertEqual(self.importer.health["status"], "Active")
        self.assertEqual(self.importer._accounted_pulses, 600)
        self.assertEqual(len(RECORDER_IMPORTS), 2)
        self.assertEqual(RECORDER_IMPORTS[0][1], RECORDER_IMPORTS[1][1])

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
