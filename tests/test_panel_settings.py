"""Regression tests for dashboard settings validation, concurrency, and migration locks."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest

package = types.ModuleType("palpulse_sidebar_test")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/palpulse")]
sys.modules[package.__name__] = package
settings = importlib.import_module("palpulse_sidebar_test.panel_settings")


def plan():
    return {
        "pricing_mode": "custom_time_of_use",
        "peak_cents_per_kwh": 34.936,
        "shoulder_cents_per_kwh": 29.5,
        "offpeak_cents_per_kwh": 20.24,
        "daily_supply_cents": 118.217,
        "weekday_free": "11:00-16:00",
        "weekend_free": "11:00-16:00",
        "daily_free_cap_kwh": 15,
        "free_excess_cents_per_kwh": 17.22,
        "unrelated_private_setting": "keep untouched",
    }


class PanelSettingsTests(unittest.TestCase):
    def test_valid_capped_tariff_saves_without_damaging_other_options(self):
        original = plan()
        changed = settings.normalize_settings(
            original, {"free_excess_cents_per_kwh": "19.75", "weekday_peak": "06:00-09:00"},
            meter="d8a5d6d91964",
        )
        self.assertEqual(changed["free_excess_cents_per_kwh"], 19.75)
        self.assertEqual(changed["unrelated_private_setting"], "keep untouched")
        self.assertEqual(original["free_excess_cents_per_kwh"], 17.22)

    def test_invalid_cap_cannot_be_saved_or_assumed(self):
        with self.assertRaisesRegex(ValueError, "positive excess rate"):
            settings.normalize_settings(plan(), {
                "free_excess_cents_per_kwh": "0",
            }, meter="d8a5d6d91964")

    def test_overlap_and_invalid_numeric_fails(self):
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            settings.normalize_settings(plan(), {
                "weekday_peak": "12:00-14:00",
                "weekday_shoulder": "13:00-15:00",
            }, meter="d8a5d6d91964")
        for invalid in ("nan", "inf", "-1", "abc", "10001"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                settings.normalize_settings(plan(), {
                    "offpeak_cents_per_kwh": invalid,
                }, meter="d8a5d6d91964")

    def test_refuses_read_only_fields_and_wrong_types(self):
        for change in ({"meter_mac": "other"}, {"auto_link_energy_cost": "true"},
                       {"history_ack_service": "shell.execute"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                settings.normalize_settings(plan(), change, meter="d8a5d6d91964")

    def test_migration_source_protection(self):
        original = {**plan(),
                    "legacy_energy_statistic_id": "sensor.original_energy",
                    "legacy_cost_statistic_id": "sensor.original_cost"}
        with self.assertRaisesRegex(ValueError, "Energy migration has a journal"):
            settings.normalize_settings(
                original, {"legacy_energy_statistic_id": "sensor.other"},
                meter="d8a5d6d91964",
                energy_journal={"status": "applied"},
            )
        with self.assertRaisesRegex(ValueError, "Cost migration has a journal"):
            settings.normalize_settings(
                original, {"legacy_cost_statistic_id": "sensor.other_cost"},
                meter="d8a5d6d91964",
                cost_journal={"status": "applying"},
            )
        with self.assertRaisesRegex(ValueError, "cannot be its target"):
            settings.normalize_settings(
                original, {"legacy_energy_statistic_id":
                           "palpulse:powerpal_d8a5d6d91964_energy"},
                meter="d8a5d6d91964",
            )

    def test_transport_and_visibility(self):
        saved = settings.normalize_settings(plan(), {
            "history_ack_service": "esphome.powerpal_ack",
            "auto_link_energy_cost": True,
            "legacy_cost_source_mode": "daily_reset",
        }, meter="d8a5d6d91964")
        self.assertEqual(saved["history_ack_service"], "esphome.powerpal_ack")
        self.assertEqual(saved["legacy_cost_source_mode"], "daily_reset")
        self.assertNotIn("unrelated_private_setting", settings.visible_settings(saved))

    def test_revision_detects_simultaneous_edit(self):
        original = plan()
        rev = settings.revision(original)
        self.assertEqual(rev, settings.revision(dict(original)))
        self.assertNotEqual(rev, settings.revision({**original, "daily_supply_cents": 120}))


if __name__ == "__main__":
    unittest.main()
