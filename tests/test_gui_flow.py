"""Exercise the PalPulse GUI's opt-in actions without starting real HA.

This isolates config_flow.py behind narrow Home Assistant flow doubles.
Actual HA 2026.11 frontend/Recorder integration tests are still required.
"""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "custom_components" / "palpulse"


def load_options_flow():
    vol = types.ModuleType("voluptuous")
    vol.Required = lambda key, **kwargs: key
    vol.Optional = lambda key, **kwargs: key
    vol.Schema = lambda schema: schema
    vol.In = lambda items: items
    vol.All = lambda *items: items[-1]
    vol.Coerce = lambda item: item
    vol.Range = lambda **kwargs: kwargs

    class FakeFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        def async_show_menu(self, **kwargs):
            return {"type": "menu", **kwargs}

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

        def async_abort(self, **kwargs):
            return {"type": "abort", **kwargs}

        def async_show_progress(self, **kwargs):
            return {"type": "progress", **kwargs}

        def async_show_progress_done(self, **kwargs):
            return {"type": "progress_done", **kwargs}

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigFlow = FakeFlow
    config_entries.OptionsFlowWithReload = FakeFlow
    homeassistant.config_entries = config_entries

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    selector = types.ModuleType("homeassistant.helpers.selector")

    class FakeSelector:
        def __init__(self, *args, **kwargs):
            self.args = args

    class FakeSelectorConfig:
        def __init__(self, **kwargs):
            self.options = kwargs

    for selector_name in (
        "SelectSelector", "StatisticSelector", "EntitySelector",
        "TimeSelector", "NumberSelector",
    ):
        setattr(selector, selector_name, FakeSelector)
    for config_name in (
        "SelectSelectorConfig", "EntitySelectorConfig", "NumberSelectorConfig",
    ):
        setattr(selector, config_name, FakeSelectorConfig)
    selector.NumberSelectorMode = types.SimpleNamespace(BOX="box")
    helpers.selector = selector
    mocks = {
        "voluptuous": vol,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.selector": selector,
    }

    package = types.ModuleType("isolated_gui_palpulse")
    package.__path__ = [str(PACKAGE_PATH)]
    with patch.dict(sys.modules, mocks):
        sys.modules[package.__name__] = package
        # Each source module imports only the pure history/pricing modules.
        module = importlib.import_module("isolated_gui_palpulse.config_flow")
    return module


class FakeManager:
    def __init__(self):
        self.source_id = "sensor.original_energy"
        self.target_id = "palpulse:energy"
        self.journal = {}
        self._preview = None
        self.busy = False
        self.last_message = "No preview yet"
        self.calls = []
        self.progress = {"stage": "idle"}

    async def async_preview(self):
        self.calls.append("preview")
        self._preview = types.SimpleNamespace(count=27, gaps=2)
        self.last_message = "27 historical hours checked. No writes were made."
        self.progress["stage"] = "ready"

    async def async_migrate(self):
        self.calls.append("migrate")

    async def async_resume(self):
        self.calls.append("resume")

    async def async_undo(self):
        self.calls.append("undo")


class GuiFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = load_options_flow()
        self.flow = self.module.PalPulseOptionsFlow()
        self.manager = FakeManager()
        self.cost_manager = FakeManager()
        self.cost_manager.source_id = "sensor.old_daily_cost"
        self.cost_manager.target_id = "palpulse:cost"
        self.hass = types.SimpleNamespace(
            data={
                "palpulse_migration": {"test": self.manager},
                "palpulse_cost_migration": {"test": self.cost_manager},
            },
            async_create_task=asyncio.create_task,
        )
        self.flow.hass = self.hass
        self.flow.config_entry = types.SimpleNamespace(
            entry_id="test",
            data={"meter_mac": "d8a5d6d91964"},
            options={"pricing_mode": "custom_time_of_use"},
        )

    async def test_gui_menu_is_available_and_distinguishes_usage_from_costs(self):
        menu = await self.flow.async_step_init()
        self.assertEqual(menu["type"], "menu")
        self.assertIn("energy_history", menu["menu_options"])
        self.assertIn("cost_history", menu["menu_options"])
        self.assertIn("free_hours", menu["menu_options"])
        self.assertIn("overview", menu["menu_options"])

    async def test_preview_never_imports_without_two_confirmations(self):
        preview = await self.flow.async_step_energy_preview()
        self.assertEqual(preview["type"], "form")
        self.assertEqual(self.manager.calls, ["preview"])
        confirm = await self.flow.async_step_energy_preview(
            {"continue_to_import": True}
        )
        self.assertEqual(confirm["type"], "form")
        self.assertEqual(confirm["step_id"], "energy_import")
        denied = await self.flow.async_step_energy_import(
            {"confirm_action": False}
        )
        self.assertEqual(denied["errors"]["base"], "confirmation_required")
        self.assertEqual(self.manager.calls, ["preview"])
        started = await self.flow.async_step_energy_import(
            {"confirm_action": True}
        )
        self.assertEqual(started["type"], "progress")
        await asyncio.sleep(0)
        self.assertEqual(self.manager.calls, ["preview", "migrate"])
        done = await self.flow.async_step_history_progress()
        self.assertEqual(done["type"], "progress_done")

    async def test_cost_preview_is_separate_from_energy_and_cannot_undo_unapplied(self):
        preview = await self.flow.async_step_cost_preview()
        self.assertEqual(preview["description_placeholders"]["source"],
                         "sensor.old_daily_cost")
        denied = await self.flow.async_step_cost_undo({"confirm_action": True})
        self.assertEqual(denied["reason"], "nothing_to_undo")
        self.assertEqual(self.cost_manager.calls, ["preview"])
        self.assertEqual(self.manager.calls, [])

    async def test_missing_cost_source_opens_picker_without_preview_notification(self):
        self.cost_manager.source_id = ""
        menu = await self.flow.async_step_cost_history()
        self.assertEqual(menu["type"], "menu")
        self.assertEqual(menu["menu_options"][0], "cost_source_setup")
        self.assertEqual(menu["description_placeholders"]["source"], "Not configured")
        first_run = await self.flow.async_step_cost_preview()
        self.assertEqual(first_run["type"], "form")
        self.assertEqual(first_run["step_id"], "historical_costs")
        self.assertEqual(self.cost_manager.calls, [])
        choose_source = await self.flow.async_step_cost_source_setup()
        self.assertEqual(choose_source["step_id"], "historical_costs")
        self.assertEqual(self.manager.calls, [])

    async def test_cost_source_keeps_pricing_and_locks_active_journal(self):
        self.cost_manager.journal = {"status": "applied"}
        self.flow.config_entry.options.update({
            "legacy_cost_statistic_id": "sensor.old_daily_cost",
            "legacy_cost_source_mode": "daily_reset",
        })
        self.assertEqual(
            self.flow._save_cost_source("sensor.other", "daily_reset"),
            "migration_locked",
        )
        self.assertIsNone(
            self.flow._save_cost_source("sensor.old_daily_cost", "daily_reset")
        )
        self.assertEqual(
            self.flow.config_entry.options["pricing_mode"],
            "custom_time_of_use",
        )

    async def test_device_identity_and_translations(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "isolated_gui_device",
            PACKAGE_PATH / "device.py",
            submodule_search_locations=[],
        )
        # Device grouping should be stable even with multiple entities.
        source = (PACKAGE_PATH / "device.py").read_text()
        self.assertIn('f"powerpal_{mac}"', source)
        self.assertIn("identifiers", source)
        buttons = (PACKAGE_PATH / "button.py").read_text()
        sensors = (PACKAGE_PATH / "sensor.py").read_text()
        self.assertIn("self._attr_device_info = meter_device_info(mac)", buttons)
        self.assertIn("meter_device_info(meter)", sensors)
        strings = json.loads(
            (PACKAGE_PATH / "strings.json").read_text()
        )["options"]
        for step in (
            "overview", "free_hours", "energy_preview", "cost_preview",
            "energy_import", "cost_import", "energy_undo", "cost_undo",
            "history_progress", "cost_source_daily", "cost_source_statistic",
        ):
            self.assertIn(step, strings["step"])


if __name__ == "__main__":
    unittest.main()
