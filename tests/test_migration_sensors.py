"""HA-like entity tests: status/progress must update without polling."""

from __future__ import annotations

import importlib
import sys
import types
import unittest

import test_importer  # registers the minimal Home Assistant module tree
import test_migration_runtime  # adds migration and entity dependencies


class SensorEntity:
    def async_write_ha_state(self):
        self.writes = getattr(self, "writes", 0) + 1


sensor_module = types.ModuleType("homeassistant.components.sensor")
sensor_module.SensorEntity = SensorEntity
entity_module = types.ModuleType("homeassistant.helpers.entity")
entity_module.EntityCategory = types.SimpleNamespace(
    DIAGNOSTIC="diagnostic", CONFIG="config"
)
sys.modules[sensor_module.__name__] = sensor_module
sys.modules[entity_module.__name__] = entity_module

entities = importlib.import_module("custom_components.palpulse.sensor")


class ManagerDouble:
    def __init__(self):
        self.callbacks = set()
        self.progress = {
            "stage": "interrupted",
            "journal_status": "applying",
            "completed_rows": 240,
            "total_rows": 1000,
            "percent": 24.0,
            "resume_available": True,
            "last_message": "Recovered saved progress; verify Recorder",
            "last_error": None,
            "pre_migration_backup": "verified-backup",
            "pre_undo_backup": None,
            "last_updated": "2026-11-04T00:00:00+00:00",
        }

    def subscribe(self, callback):
        self.callbacks.add(callback)
        return lambda: self.callbacks.discard(callback)

    def update(self, **fields):
        self.progress.update(fields)
        for callback in tuple(self.callbacks):
            callback()


class SensorTests(unittest.IsolatedAsyncioTestCase):
    def test_auxiliary_sensors_are_hidden_but_keep_functional_entity_classes(self):
        self.assertFalse(entities.MigrationSensor._attr_entity_registry_visible_default)
        self.assertFalse(entities.DailySupplyChargeSensor._attr_entity_registry_visible_default)
        self.assertTrue(entities.LiveHistoryStatusSensor._attr_entity_registry_enabled_default)

    def test_incomplete_tariff_does_not_raise_from_auxiliary_supply_sensor(self):
        entry = types.SimpleNamespace(options={"pricing_mode": "flat"})
        supply = entities.DailySupplyChargeSensor(test_importer.MAC, entry, "AUD")
        self.assertIsNone(supply.native_value)

    async def test_entities_show_real_progress_and_recovery_status(self):
        manager = ManagerDouble()
        importer = test_importer.palpulse.PalPulseImporter(
            object(), test_importer.MAC
        )
        hass = types.SimpleNamespace(data={
            "palpulse_migration": {"example": manager},
            "palpulse": {"example": importer},
        })
        entry = types.SimpleNamespace(
            entry_id="example", data={"meter_mac": "d8a5d6d91964"}
        )
        added = []
        await entities.async_setup_entry(hass, entry, added.extend)
        self.assertEqual(len(added), 3)
        status, progress, live = added
        self.assertEqual(live.native_value, "No Events")
        self.assertEqual(live.extra_state_attributes["skipped_hours"], 0)
        self.assertEqual(live.extra_state_attributes["statistic_id"],
                         "palpulse:powerpal_d8a5d6d91964_energy")
        self.assertEqual(status.native_value, "Interrupted")
        self.assertEqual(progress.native_value, 24.0)
        self.assertEqual(progress.extra_state_attributes["completed_rows"], 240)
        self.assertEqual(progress.extra_state_attributes["backup_id"], "verified-backup")
        self.assertTrue(status.extra_state_attributes["resume_available"])

        await status.async_added_to_hass()
        await progress.async_added_to_hass()
        await live.async_added_to_hass()
        importer._gap_count = 2
        importer._status_message = "Recovered after 2 missing hours"
        importer._notify_health()
        self.assertEqual(live.extra_state_attributes["skipped_hours"], 2)
        self.assertEqual(live.writes, 1)
        manager.update(
            stage="importing", completed_rows=480, percent=48.0
        )
        self.assertEqual(status.native_value, "Importing")
        self.assertEqual(progress.native_value, 48.0)
        self.assertEqual(status.writes, 1)
        self.assertEqual(progress.writes, 1)
        await status.async_will_remove_from_hass()
        await progress.async_will_remove_from_hass()
        await live.async_will_remove_from_hass()
        importer._notify_health()
        self.assertEqual(live.writes, 1)
        manager.update(stage="completed")
        self.assertEqual(status.writes, 1)
        self.assertEqual(progress.writes, 1)


if __name__ == "__main__":
    unittest.main()
