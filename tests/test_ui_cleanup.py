"""The 0.2b2 UI migration must not erase or repeatedly hide user entities."""

from __future__ import annotations

import importlib
import sys
import types
import unittest

import test_importer


registry_module = types.ModuleType("homeassistant.helpers.entity_registry")
registry_module.RegistryEntryHider = types.SimpleNamespace(INTEGRATION="integration")


class FakeRegistry:
    def __init__(self, entries):
        self.entries = entries
        self.changed = []

    def async_update_entity(self, entity_id, *, hidden_by):
        self.changed.append((entity_id, hidden_by))
        next_entry = next(e for e in self.entries if e.entity_id == entity_id)
        next_entry.hidden_by = hidden_by


registry_module.async_get = lambda hass: hass.registry
registry_module.async_entries_for_config_entry = (
    lambda registry, entry_id: [
        item for item in registry.entries if item.config_entry_id == entry_id
    ]
)
sys.modules[registry_module.__name__] = registry_module
ui = importlib.import_module("custom_components.palpulse.ui_cleanup")


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        test_importer.FakeStore.VALUES.clear()
        self.mac = test_importer.MAC
        self.entry = types.SimpleNamespace(entry_id="example")
        self.ids = ui.auxiliary_unique_ids(self.mac)

    def entity(self, name, unique_id, domain="sensor", **overrides):
        fields = {
            "entity_id": name,
            "unique_id": unique_id,
            "platform": "palpulse",
            "config_entry_id": "example",
            "hidden_by": None,
            "name": None,
        }
        fields.update(overrides)
        return types.SimpleNamespace(**fields)

    async def test_old_buttons_progress_and_supply_are_hidden_without_deletion(self):
        targets = [
            self.entity("button.import_history", f"palpulse_{self.mac}_migrate_historical_migration", "button"),
            self.entity("button.cost_import", f"palpulse_{self.mac}_migrate_historical_cost_migration", "button"),
            self.entity("sensor.old_progress", f"palpulse_{self.mac}_migration_progress"),
            self.entity("sensor.old_cost_progress", f"palpulse_{self.mac}_migration_cost_progress"),
            self.entity("sensor.supply", f"palpulse_{self.mac}_daily_supply_charge"),
        ]
        customised = self.entity(
            "sensor.custom",
            f"palpulse_{self.mac}_migration_status",
            name="My migration monitoring",
        )
        user_hidden = self.entity(
            "sensor.user_hidden",
            f"palpulse_{self.mac}_migration_cost_status",
            hidden_by="user",
        )
        other = self.entity("sensor.live", f"palpulse_{self.mac}_live_history_status")
        other_entry = self.entity(
            "sensor.other_config",
            f"palpulse_{self.mac}_migration_status",
            config_entry_id="different",
        )
        hass = types.SimpleNamespace(registry=FakeRegistry(
            targets + [customised, user_hidden, other, other_entry]
        ))
        await ui.async_tidy_existing_entities(hass, self.entry, self.mac)
        self.assertEqual(len(hass.registry.changed), len(targets))
        self.assertTrue(all(t.hidden_by == "integration" for t in targets))
        self.assertIsNone(customised.hidden_by)
        self.assertEqual(user_hidden.hidden_by, "user")
        self.assertIsNone(other.hidden_by)
        self.assertIsNone(other_entry.hidden_by)
        # A user re-exposes a control after upgrade: never hide it on restart.
        targets[0].hidden_by = None
        await ui.async_tidy_existing_entities(hass, self.entry, self.mac)
        self.assertIsNone(targets[0].hidden_by)
        self.assertEqual(len(hass.registry.changed), len(targets))

    def test_only_known_auxiliary_unique_ids_are_targeted(self):
        self.assertEqual(len(self.ids), 13)
        self.assertIn(f"palpulse_{self.mac}_daily_supply_charge", self.ids)
        self.assertNotIn(f"palpulse_{self.mac}_cost_import_status", self.ids)
        self.assertNotIn(f"palpulse_{self.mac}_live_history_status", self.ids)


if __name__ == "__main__":
    unittest.main()
