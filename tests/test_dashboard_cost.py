"""Protect existing HA Energy settings when offering a missing-cost repair."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from copy import deepcopy

import test_importer  # register Home Assistant stubs

energy_module = types.ModuleType("homeassistant.components.energy")


async def async_get_manager(hass):
    return hass.energy_manager


energy_module.async_get_manager = async_get_manager
sys.modules[energy_module.__name__] = energy_module
cost_link = importlib.import_module("custom_components.palpulse.dashboard_cost")

ENERGY = "palpulse:powerpal_aabbccddeeff_energy"
COST = "palpulse:powerpal_aabbccddeeff_cost"
GRID = {
    "type": "grid",
    "stat_energy_from": ENERGY,
    "stat_energy_to": None,
    "stat_cost": None,
    "entity_energy_price": None,
    "number_energy_price": None,
    "cost_adjustment_day": 0.0,
}


class FakeEnergyManager:
    def __init__(self, sources):
        self.data = {
            "energy_sources": sources,
            "device_consumption": [{"stat_consumption": "sensor.other"}],
            "device_consumption_water": [],
        }
        self.calls = 0

    async def async_update(self, updates):
        self.calls += 1
        self.data = {**self.data, **updates}


class VerifiedCostWriter:
    statistic_id = COST
    status = {"last_costed_hour": 1800000000}

    async def _read(self, start, end):
        return [{"start": start, "state": 10.0, "sum": 10.0}]


class DashboardCostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = types.SimpleNamespace(
            energy_manager=FakeEnergyManager([deepcopy(GRID)]),
        )
        self.writer = VerifiedCostWriter()

    def test_unlinked_detected_without_writing(self):
        result = cost_link.assess_cost_link(self.hass.energy_manager.data, ENERGY, COST)
        self.assertEqual(result.status, "unlinked")
        self.assertEqual(self.hass.energy_manager.calls, 0)

    async def test_confirmed_link_only_changes_matching_grid_source(self):
        another = {**GRID, "stat_energy_from": "sensor.other_energy"}
        self.hass.energy_manager.data["energy_sources"].append(another)
        original = deepcopy(self.hass.energy_manager.data)
        result = await cost_link.async_link_verified_cost(
            self.hass, ENERGY, COST, self.writer,
        )
        self.assertEqual(result.status, "linked")
        self.assertEqual(self.hass.energy_manager.calls, 1)
        self.assertEqual(
            self.hass.energy_manager.data["energy_sources"][0]["stat_cost"],
            COST,
        )
        self.assertEqual(
            self.hass.energy_manager.data["energy_sources"][1], another,
        )
        self.assertEqual(
            self.hass.energy_manager.data["device_consumption"],
            original["device_consumption"],
        )
        # Reopening setup should be harmless and not rewrite the prefs.
        again = await cost_link.async_link_verified_cost(
            self.hass, ENERGY, COST, self.writer,
        )
        self.assertEqual(again.status, "linked")
        self.assertEqual(self.hass.energy_manager.calls, 1)

    async def test_existing_tariff_and_other_cost_never_overwritten(self):
        for key, existing in (
            ("entity_energy_price", "sensor.grid_price"),
            ("number_energy_price", 0.0),
            ("stat_cost", "sensor.old_cost"),
        ):
            with self.subTest(key=key):
                self.hass.energy_manager.data["energy_sources"] = [
                    {**GRID, key: existing},
                ]
                result = await cost_link.async_link_verified_cost(
                    self.hass, ENERGY, COST, self.writer,
                )
                self.assertIn(result.status, {"other_price", "other_cost"})
                self.assertEqual(self.hass.energy_manager.calls, 0)

    async def test_missing_real_cost_row_or_cost_writer_rejected(self):
        self.writer.status = {"last_costed_hour": None}
        missing = await cost_link.async_link_verified_cost(
            self.hass, ENERGY, COST, self.writer,
        )
        self.assertEqual(missing.status, "cost_unavailable")
        self.assertEqual(self.hass.energy_manager.calls, 0)
        self.writer.status = {"last_costed_hour": 1800000000}

        async def empty(_start, _end):
            return []

        self.writer._read = empty
        missing = await cost_link.async_link_verified_cost(
            self.hass, ENERGY, COST, self.writer,
        )
        self.assertEqual(missing.status, "cost_unavailable")
        self.assertEqual(self.hass.energy_manager.calls, 0)

    async def test_duplicate_grid_source_and_legacy_flow_rejected_or_linked(self):
        duplicate = deepcopy(GRID)
        self.hass.energy_manager.data["energy_sources"].append(duplicate)
        status = await cost_link.async_link_verified_cost(
            self.hass, ENERGY, COST, self.writer,
        )
        self.assertEqual(status.status, "ambiguous")
        self.assertEqual(self.hass.energy_manager.calls, 0)

        self.hass.energy_manager.data["energy_sources"] = [{
            "type": "grid",
            "flow_from": [{**GRID, "stat_energy_from": ENERGY}],
        }]
        status = await cost_link.async_link_verified_cost(
            self.hass, ENERGY, COST, self.writer,
        )
        self.assertEqual(status.status, "linked")
        self.assertEqual(
            self.hass.energy_manager.data["energy_sources"][0]["flow_from"][0]["stat_cost"],
            COST,
        )

    async def test_confirmed_auto_relink_recovers_without_overwriting_user_choice(self):
        cost_recorder = importlib.import_module(
            "custom_components.palpulse.cost_recorder"
        )
        entry = types.SimpleNamespace(options={
            "pricing_mode": "custom_flat",
            "flat_cents_per_kwh": 25,
            "daily_supply_cents": 0,
            "auto_link_energy_cost": True,
        })
        # Same physical meter/statistic; no fake configuration of another source.
        mac = "aabbccddeeff"
        writer = cost_recorder.PalPulseCostRecorder(self.hass, entry, mac)
        writer._last_hour = 1800000000

        async def read_verified_hour(start, end):
            return [{"start": start, "state": 10.0, "sum": 10.0}]

        writer._read = read_verified_hour
        await writer.async_refresh_cost_link()
        self.assertEqual(writer.status["energy_dashboard_cost_link"], "linked")
        self.assertEqual(self.hass.energy_manager.calls, 1)

        # Removing a previously opted-in mapping should only recover if it
        # is still empty and the opt-in remains enabled.
        self.hass.energy_manager.data["energy_sources"][0]["stat_cost"] = None
        writer.entry.options["auto_link_energy_cost"] = False
        await writer.async_refresh_cost_link()
        self.assertEqual(writer.status["energy_dashboard_cost_link"], "unlinked")
        self.assertEqual(self.hass.energy_manager.calls, 1)

        # An alternate user-selected tariff must never be overwritten.
        writer.entry.options["auto_link_energy_cost"] = True
        self.hass.energy_manager.data["energy_sources"][0]["entity_energy_price"] = "sensor.my_price"
        await writer.async_refresh_cost_link()
        self.assertEqual(writer.status["energy_dashboard_cost_link"], "other_price")
        self.assertEqual(self.hass.energy_manager.calls, 1)

    def test_other_user_sources_and_empty_configuration_untouched(self):
        unknown = cost_link.assess_cost_link(
            {"energy_sources": [{"type": "grid", "stat_energy_from": "sensor.other"}]},
            ENERGY, COST,
        )
        self.assertEqual(unknown.status, "no_source")
        self.assertEqual(
            cost_link.assess_cost_link(None, ENERGY, COST).status,
            "not_configured",
        )


if __name__ == "__main__":
    unittest.main()
