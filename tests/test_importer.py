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

    async def async_add_executor_job(self, callback, *args):
        return await asyncio.to_thread(callback, *args)

ha_helpers_recorder.get_instance = lambda hass: FakeRecorder()



class StatisticMeanType(IntEnum):
    NONE = 0


ha_models.StatisticMeanType = StatisticMeanType
RECORDER_IMPORTS = []
RECORDER_ROWS = {}


def statistics_during_period(hass, start, end, statistic_ids, period,
                             units, types_to_load):
    rows = [
        dict(row) for stamp, row in sorted(RECORDER_ROWS.items())
        if start.timestamp() <= stamp < end.timestamp()
    ]
    return {next(iter(statistic_ids)): rows} if rows else {}


def async_add_external_statistics(hass, metadata, statistics):
    values = list(statistics)
    RECORDER_IMPORTS.append((metadata, values))
    for row in values:
        RECORDER_ROWS[int(row["start"].timestamp())] = {
            "start": int(row["start"].timestamp()),
            "state": row["state"],
            "sum": row["sum"],
        }


ha_statistics.async_add_external_statistics = async_add_external_statistics
ha_statistics.statistics_during_period = statistics_during_period
ha_core.Event = type("Event", (), {})
ha_core.HomeAssistant = type("HomeAssistant", (), {})


def callback(func):
    """Model Home Assistant's event-loop-safe callback annotation."""
    func.__ha_callback = True
    return func


ha_core.callback = callback
ha_const.Platform = types.SimpleNamespace(BUTTON="button", SENSOR="sensor")
ha_const.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"


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
        RECORDER_ROWS.clear()
        FakeStore.VALUES.clear()
        self.hass = types.SimpleNamespace(
            async_add_executor_job=lambda callback: asyncio.to_thread(callback),
        )
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
        """Buffer early events while Recorder bootstraps, then deliver FIFO."""
        listeners, started_listeners, tasks = [], [], []

        def listen(event_type, handler):
            self.assertEqual(event_type, "esphome.palpulse_record")
            self.assertTrue(getattr(handler, "__ha_callback", False))
            listeners.append(handler)
            return lambda: None

        def listen_once(event_type, handler):
            self.assertEqual(event_type, "homeassistant_started")
            started_listeners.append(handler)
            return lambda: None

        async def forward_platforms(entry, platforms):
            self.assertEqual(platforms, ["button", "sensor"])

        def background(_hass, coroutine, name, eager_start=True):
            self.assertTrue(name.startswith("PalPulse"))
            self.assertFalse(eager_start)
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        hass = types.SimpleNamespace(
            bus=types.SimpleNamespace(
                async_listen=listen, async_listen_once=listen_once,
            ),
            data={}, is_running=False,
            config_entries=types.SimpleNamespace(
                async_forward_entry_setups=forward_platforms
            ),
        )
        entry = types.SimpleNamespace(
            data={"meter_mac": MAC},
            entry_id="thread-safe-test",
            async_on_unload=lambda _: None,
            async_create_background_task=background,
        )
        try:
            self.assertTrue(await palpulse.async_setup_entry(hass, entry))
            self.assertEqual(len(listeners), 1)
            self.assertEqual(len(started_listeners), 1)
            event = types.SimpleNamespace(data=record_event(BASE, 10, 40010))
            with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
                listeners[0](event)
                await asyncio.sleep(0)
                self.assertNotIn(
                    BASE, hass.data["palpulse"][entry.entry_id]._records,
                )
                started_listeners[0](None)
                for _ in range(20):
                    await asyncio.sleep(0.01)
                    if BASE in hass.data["palpulse"][entry.entry_id]._records:
                        break
            self.assertIn(
                BASE, hass.data["palpulse"][entry.entry_id]._records
            )
            self.assertEqual(
                hass.data["palpulse_history_delivery"][entry.entry_id]["received_count"], 1,
            )
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_pending_recovery_does_not_block_home_assistant_boot(self):
        """A Recorder queue stall must not cancel config-entry bootstrap."""
        tasks = []
        never_complete = asyncio.Event()
        recovery_started = asyncio.Event()
        async def stalled_start(_self):
            recovery_started.set()
            await never_complete.wait()

        def background(_hass, coro, _name, eager_start=True):
            self.assertFalse(eager_start)
            task = asyncio.create_task(coro)
            tasks.append(task)
            return task

        async def forward_platforms(_entry, _platforms):
            return None

        hass = types.SimpleNamespace(
            data={}, is_running=True,
            bus=types.SimpleNamespace(
                async_listen=lambda _event, _cb: lambda: None,
            ),
            config_entries=types.SimpleNamespace(
                async_forward_entry_setups=forward_platforms,
            ),
        )
        entry = types.SimpleNamespace(
            entry_id="test-stalled-recorder",
            data={"meter_mac": MAC},
            async_on_unload=lambda _: None,
            async_create_background_task=background,
        )
        try:
            with patch.object(palpulse.PalPulseImporter, "async_start",
                              stalled_start):
                self.assertTrue(await asyncio.wait_for(
                    palpulse.async_setup_entry(hass, entry), timeout=0.5
                ))
                await asyncio.wait_for(recovery_started.wait(), timeout=0.5)
                self.assertTrue(any(not t.done() for t in tasks))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_persisted_measurements_retry_without_a_new_event(self):
        """A restart must not strand complete buffered energy hours."""
        start = BASE
        self.importer._calibration = CAL
        self.importer._records = {
            start + minute * 60: palpulse.Record(
                start + minute * 60, 10, 40000 + (minute + 1) * 10, CAL,
            )
            for minute in range(60)
        }
        timer = types.ModuleType("homeassistant.helpers.event")
        periodic = []
        timer.async_track_time_interval = lambda _hass, callback, _period: (
            periodic.append(callback) or (lambda: None)
        )
        with patch.dict(sys.modules, {"homeassistant.helpers.event": timer}):
            with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
                await self.importer.async_start()
                self.assertEqual(self.importer.health["last_completed_utc_hour"], BASE)
                self.assertEqual(len(RECORDER_IMPORTS), 1)
                self.assertEqual(len(periodic), 1)
                await periodic[0](None)
                self.assertEqual(len(RECORDER_IMPORTS), 1)
                await self.importer.async_flush()

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
        await self.feed_hour(BASE + 3600, 40600, skip=59)
        await self.feed_hour(BASE + 7200, 41200)
        self.assertEqual(len(RECORDER_IMPORTS), 2)
        recovered = RECORDER_IMPORTS[1][1][0]
        self.assertEqual(recovered["start"].timestamp(), BASE + 7200)
        # Initial 600 pulses + resumed valid hour's 600, not the intervening
        # missing hour's 600. No catch-up spike.
        self.assertAlmostEqual(recovered["sum"], 1200 / 3200)
        self.assertEqual(self.importer.health["skipped_hours"], 1)
        self.assertEqual(self.importer.health["last_gap_utc_hour"], BASE + 3600)

    async def test_sparse_interior_minute_recovers_exact_energy(self):
        costed_hours = []

        class FakeCostWriter:
            async def async_import_verified_hour(self, hour, records, calibration):
                costed_hours.append(hour.hour_start)

        self.importer.cost_recorder = FakeCostWriter()
        await self.feed_hour(BASE, 40000)
        await self.feed_hour(BASE + 3600, 40600, skip=13)
        await self.feed_hour(BASE + 7200, 41200)
        self.assertEqual(len(RECORDER_IMPORTS), 3)
        recovered = RECORDER_IMPORTS[1][1][0]
        self.assertEqual(recovered["start"].timestamp(), BASE + 3600)
        self.assertAlmostEqual(recovered["sum"], 1200 / CAL)
        self.assertAlmostEqual(RECORDER_IMPORTS[2][1][0]["sum"], 1800 / CAL)
        self.assertEqual(self.importer.health["skipped_hours"], 0)
        self.assertEqual(self.importer.health["sparse_recovered_hours"], 1)
        # Counter-verified sparse hours now reach the cost writer, which
        # independently refuses only mixed/ambiguous tariff intervals.
        self.assertEqual(costed_hours, [BASE, BASE + 3600, BASE + 7200])

    async def test_missing_first_minute_recovers_with_prior_hour_end(self):
        await self.feed_hour(BASE, 40000)
        await self.feed_hour(BASE + 3600, 40600, skip=0)
        await self.feed_hour(BASE + 7200, 41200)
        self.assertEqual(len(RECORDER_IMPORTS), 3)
        self.assertEqual(self.importer.health["skipped_hours"], 0)
        self.assertEqual(self.importer.health["sparse_recovered_hours"], 1)
        self.assertAlmostEqual(RECORDER_ROWS[BASE + 7200]["sum"], 1800 / CAL)

    async def test_replacement_esp32_rebases_once_without_rewriting_old_sum(self):
        await self.feed_hour(BASE, 40000)
        original = dict(RECORDER_ROWS[BASE])
        await self.importer.async_arm_device_replacement()
        await self.feed_hour(BASE + 3600, 1000)
        self.assertEqual(self.importer.health["device_rebase_count"], 1)
        self.assertEqual(RECORDER_ROWS[BASE], original)
        self.assertAlmostEqual(RECORDER_IMPORTS[-1][1][0]["sum"], 1200 / CAL)
        self.assertIsNone(self.importer.health["device_rebase_deadline"])
        # A second unexpected reset must not reuse the old approval.
        await self.feed_hour(BASE + 7200, 100)
        self.assertTrue(self.importer._halted)

    async def test_auto_ack_detects_delayed_esphome_registration(self):
        received = []

        class Services:
            def __init__(self):
                self.actions = {}
            def has_service(self, domain, service):
                return domain == "esphome" and service in self.actions
            def async_services(self):
                return {"esphome": dict(self.actions)}
            async def async_call(self, domain, service, data, blocking):
                assert service == "powerpal_proxy_palpulse_ack_8fed36da"
                assert FakeStore.VALUES[f"palpulse_{MAC}"]["ack_timestamp"] >= data["timestamp"]
                received.append(data["timestamp"])

        services = Services()
        self.hass.services = services
        self.importer = palpulse.PalPulseImporter(self.hass, MAC)
        await self.importer.async_load()
        with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
            await self.importer.async_receive(record_event(BASE, 10, 40010))
            self.assertIsNone(self.importer.health["ack_service"])
            self.assertEqual(received, [])
            services.actions["powerpal_proxy_palpulse_ack_8fed36da"] = {}
            # The ESP32 retries its oldest unacknowledged event.
            await self.importer.async_receive(record_event(BASE, 10, 40010))
            await self.importer.async_receive(record_event(BASE + 60, 10, 40020))
        self.assertEqual(received, [BASE, BASE + 60])
        self.assertEqual(
            self.importer.health["ack_service"],
            "esphome.powerpal_proxy_palpulse_ack_8fed36da",
        )

    async def test_ack_opt_out_disables_even_explicit_registered_service(self):
        received = []

        class Services:
            def has_service(self, domain, service):
                return True
            def async_services(self):
                return {"esphome": {"powerpal_proxy_palpulse_ack_8fed36da": {}}}
            async def async_call(self, domain, service, data, blocking):
                received.append(data)

        self.hass.services = Services()
        self.importer = palpulse.PalPulseImporter(
            self.hass, MAC, "esphome.powerpal_proxy_palpulse_ack_8fed36da",
            ack_disabled=True,
        )
        await self.importer.async_load()
        with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
            await self.importer.async_receive(record_event(BASE, 10, 40010))
        self.assertEqual(received, [])
        self.assertEqual(self.importer.health["ack_resolution"], "Disabled by user")

    async def test_acknowledges_only_durable_saved_minute(self):
        acknowledgments = []

        class Services:
            def has_service(self, domain, service):
                return domain == "esphome" and service == "meter_palpulse_ack_1234"

            async def async_call(self, domain, service, data, blocking):
                snapshot = FakeStore.VALUES.get(f"palpulse_{MAC}")
                self.assertIsNotNone(snapshot)
                self.assertGreaterEqual(snapshot["ack_timestamp"], data["timestamp"])
                acknowledgments.append(data["timestamp"])

        # Nested class must see this testcase's assertions.
        testcase = self
        Services.assertIsNotNone = lambda _, v: testcase.assertIsNotNone(v)
        Services.assertGreaterEqual = lambda _, a, b: testcase.assertGreaterEqual(a, b)
        self.hass.services = Services()
        self.importer = palpulse.PalPulseImporter(
            self.hass, MAC, "esphome.meter_palpulse_ack_1234"
        )
        await self.importer.async_load()
        with patch.object(palpulse.time, "time", return_value=BASE + 4 * 3600):
            await self.importer.async_receive(record_event(BASE, 10, 40010))
            await self.importer.async_receive(record_event(BASE + 60, 10, 40020))
            await self.importer.async_receive(record_event(BASE + 60, 10, 40020))
        self.assertEqual(acknowledgments, [BASE, BASE + 60, BASE + 60])
        self.assertEqual(self.importer.health["last_acknowledged_utc_minute"], BASE + 60)

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
            if minute != 0:
                stamp = BASE + 3600 + minute * 60
                legacy["records"][str(stamp)] = [10, 40600 + 10 * (minute + 1)]
        for hour, base in [(BASE + 7200, 41200), (BASE + 10800, 41800)]:
            for minute in range(60):
                stamp = hour + minute * 60
                legacy["records"][str(stamp)] = [10, base + 10 * (minute + 1)]
        FakeStore.VALUES[f"palpulse_{MAC}"] = legacy
        RECORDER_ROWS[BASE] = {
            "start": BASE, "state": 40600 / CAL, "sum": 600 / CAL,
        }
        self.importer = palpulse.PalPulseImporter(self.hass, MAC)
        await self.importer.async_load()
        self.assertEqual(self.importer._accounted_pulses, 600)
        with patch.object(palpulse.time, "time", return_value=BASE + 6 * 3600):
            await self.importer.async_receive(
                record_event(BASE + 4 * 3600, 10, 42410)
            )
        self.assertEqual(self.importer._last_hour, BASE + 3 * 3600)
        # The previous durable meter endpoint now proves the formerly
        # skipped hour even though its first minute is missing.
        self.assertEqual(self.importer.health["skipped_hours"], 0)
        self.assertEqual(self.importer.health["sparse_recovered_hours"], 1)
        self.assertEqual(len(RECORDER_IMPORTS), 3)
        self.assertAlmostEqual(RECORDER_IMPORTS[0][1][0]["sum"], 1200 / CAL)
        self.assertAlmostEqual(RECORDER_IMPORTS[1][1][0]["sum"], 1800 / CAL)
        self.assertAlmostEqual(RECORDER_IMPORTS[2][1][0]["sum"], 2400 / CAL)
        # Duplicate replay of an already imported record cannot double count.
        with patch.object(palpulse.time, "time", return_value=BASE + 6 * 3600):
            await self.importer.async_receive(record_event(
                BASE + 3 * 3600 + 59 * 60, 10, 42400
            ))
        self.assertEqual(len(RECORDER_IMPORTS), 3)

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
        class FailedRecorder(FakeRecorder):
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
        with patch.object(palpulse.time, "time", return_value=BASE + 2 * 3600 + 10):
            self.assertEqual(self.importer.health["status"], "Active")
        self.assertEqual(self.importer._accounted_pulses, 600)
        # The previous write was committed even though acknowledgement failed.
        # Idempotent retry verifies that row without writing it a second time.
        self.assertEqual(len(RECORDER_IMPORTS), 1)
        self.assertEqual(RECORDER_ROWS[BASE]["sum"], 600 / CAL)

    async def test_migrated_recorder_sum_reanchors_future_hours_without_a_negative_spike(self):
        """Regression for the user's 9 November 2026 ~11,179 kWh reset."""
        await self.feed_hour(BASE, 40000)
        first = RECORDER_ROWS[BASE]
        old_total = first["sum"]
        migrated_offset = 11179.097525
        # After historical migration, Recorder's cumulative sum may shift
        # while the actual raw Powerpal meter state stays exactly unchanged.
        first["sum"] += migrated_offset
        await self.feed_hour(BASE + 3600, 40600)
        second = RECORDER_ROWS[BASE + 3600]
        self.assertAlmostEqual(second["sum"] - first["sum"], 600 / CAL)
        self.assertAlmostEqual(
            second["sum"], migrated_offset + old_total + 600 / CAL
        )
        self.assertAlmostEqual(self.importer._sum_offset, migrated_offset)
        self.assertEqual(self.importer._last_reconciled_at, BASE)
        self.assertFalse(self.importer._halted)
        # The reconciled offset survives a restart, and is reverified against
        # the actual immediately preceding Recorder hour.
        restored = palpulse.PalPulseImporter(self.hass, MAC)
        await restored.async_load()
        self.assertAlmostEqual(restored._sum_offset, migrated_offset)
        self.importer = restored
        await self.feed_hour(BASE + 7200, 41200)
        third = RECORDER_ROWS[BASE + 7200]
        self.assertAlmostEqual(third["sum"] - second["sum"], 600 / CAL)

    async def test_missing_prior_recorder_row_blocks_new_energy_write(self):
        await self.feed_hour(BASE, 40000)
        RECORDER_ROWS.pop(BASE)
        await self.feed_hour(BASE + 3600, 40600)
        self.assertEqual(self.importer._last_hour, BASE)
        self.assertIn("last imported energy hour", self.importer.health["message"])
        self.assertNotIn(BASE + 3600, RECORDER_ROWS)

    async def test_mismatched_recorder_state_fails_closed(self):
        await self.feed_hour(BASE, 40000)
        RECORDER_ROWS[BASE]["state"] += 15
        await self.feed_hour(BASE + 3600, 40600)
        self.assertEqual(self.importer._last_hour, BASE)
        self.assertIn("meter state does not match", self.importer.health["message"])
        self.assertNotIn(BASE + 3600, RECORDER_ROWS)

    async def test_foreign_conflicting_hour_is_never_overwritten(self):
        await self.feed_hour(BASE, 40000)
        previous = RECORDER_ROWS[BASE]
        RECORDER_ROWS[BASE + 3600] = {
            "start": BASE + 3600,
            "state": (41200 / CAL),
            "sum": previous["sum"] - 5.0,
        }
        await self.feed_hour(BASE + 3600, 40600)
        self.assertEqual(self.importer._last_hour, BASE)
        self.assertIn("Refusing overwrite", self.importer.health["message"])
        self.assertEqual(RECORDER_ROWS[BASE + 3600]["sum"],
                         previous["sum"] - 5.0)

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
