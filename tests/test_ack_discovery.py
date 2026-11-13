"""Per-meter discovery must never attach the wrong ESPHome ACK action."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest

package = types.ModuleType("palpulse_ack_test")
package.__path__ = [str(Path(__file__).resolve().parents[1] /
                          "custom_components" / "palpulse")]
sys.modules[package.__name__] = package
discovery = importlib.import_module("palpulse_ack_test.ack_discovery")
MAC = "d8a5d6d91964"
ACK = "powerpal_proxy_palpulse_ack_8fed36da"


class Services:
    def __init__(self, esphome=None):
        self.available = dict(esphome or {})
    def async_services(self):
        return {"esphome": dict(self.available)}
    def has_service(self, domain, name):
        return domain == "esphome" and name in self.available


class AutoAckTests(unittest.TestCase):
    def test_exact_esp32_fnv_hash(self):
        self.assertEqual(discovery.meter_ack_suffix(MAC), "8fed36da")
        self.assertEqual(
            discovery.meter_ack_suffix("D8:A5:D6:D9:19:64"), "8fed36da")

    def test_finds_matching_meter_but_not_other_meter(self):
        matches = {
            "different_proxy_palpulse_ack_11111111": {},
            ACK: {},
            "powerpal_proxy_palpulse_replay_8fed36da": {},
        }
        action, reason = discovery.find_meter_ack_action(matches, MAC)
        self.assertEqual(action, "esphome." + ACK)
        self.assertIn("discovered", reason.lower())

    def test_waits_then_detects_after_esp_registers(self):
        services = Services()
        hass = types.SimpleNamespace(services=services)
        action, reason = discovery.resolve_meter_ack(hass, MAC)
        self.assertIsNone(action)
        services.available[ACK] = {}
        action, reason = discovery.resolve_meter_ack(hass, MAC)
        self.assertEqual(action, "esphome." + ACK)

    def test_duplicate_matching_meters_fail_closed(self):
        services = Services({ACK: {}, "other_proxy_palpulse_ack_8fed36da": {}})
        action, reason = discovery.resolve_meter_ack(
            types.SimpleNamespace(services=services), MAC)
        self.assertIsNone(action)
        self.assertIn("Ambiguous", reason)

    def test_opt_out_overrides_existing_manual_action(self):
        services = Services({ACK: {}})
        action, reason = discovery.resolve_meter_ack(
            types.SimpleNamespace(services=services), MAC,
            configured="esphome." + ACK, disabled=True)
        self.assertIsNone(action)
        self.assertEqual(reason, "Disabled by user")

    def test_legacy_manual_action_remains_compatible(self):
        services = Services({"custom_palpulse_ack_1234": {}})
        action, reason = discovery.resolve_meter_ack(
            types.SimpleNamespace(services=services), MAC,
            configured="esphome.custom_palpulse_ack_1234")
        self.assertEqual(action, "esphome.custom_palpulse_ack_1234")

    def test_stale_manual_action_falls_back_to_autodiscovery(self):
        services = Services({ACK: {}})
        action, _ = discovery.resolve_meter_ack(
            types.SimpleNamespace(services=services), MAC,
            configured="esphome.deleted_palpulse_ack_1234")
        self.assertEqual(action, "esphome." + ACK)

    def test_no_services_await_registration(self):
        self.assertIsNone(discovery.resolve_meter_ack(
            types.SimpleNamespace(), MAC)[0])

    def test_invalid_meter_identity_refused(self):
        with self.assertRaises(ValueError):
            discovery.meter_ack_suffix("bad:mac")


if __name__ == "__main__":
    unittest.main()
