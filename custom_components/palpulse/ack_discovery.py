"""Resolve PalPulse acknowledged-history actions from a Powerpal meter MAC.

ESPHome registers the exact FNV-1a hash of the uppercase colon-delimited MAC.
Discovery is read-only, exact-suffix matched, and refuses ambiguous devices.
It runs when needed, not only at HA startup, because ESPHome can register late.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from .history import normalize_mac


def meter_ack_suffix(mac: str) -> str:
    canonical = normalize_mac(mac).upper()
    formatted = ":".join(canonical[i:i + 2] for i in range(0, 12, 2))
    result = 2166136261
    for byte in formatted.encode("ascii"):
        result = ((result ^ byte) * 16777619) & 0xFFFFFFFF
    return f"{result:08x}"


def find_meter_ack_action(services: Mapping[str, object] | None, mac: str
                          ) -> tuple[str | None, str]:
    """Return a uniquely matching ESPHome action, never another meter's."""
    suffix = meter_ack_suffix(mac)
    pattern = re.compile(rf"^[a-z][a-z0-9_]*_palpulse_ack_{suffix}$")
    matches = sorted({
        name for name in (services or {})
        if isinstance(name, str) and pattern.fullmatch(name)
    })
    if not matches:
        return None, "Waiting for matching ESPHome ACK action"
    if len(matches) != 1:
        return None, f"Ambiguous ESPHome ACK actions ({len(matches)}); check duplicate meter bridges"
    return f"esphome.{matches[0]}", "Automatically discovered"


def resolve_meter_ack(hass, mac: str, *,
                      configured: str | None = None,
                      disabled: bool = False) -> tuple[str | None, str]:
    """Prefer a valid explicit legacy override; otherwise auto-discover.

    Never auto-ack if the user deliberately disabled acknowledged delivery.
    """
    if disabled:
        return None, "Disabled by user"
    services = getattr(hass, "services", None)
    if services is None:
        return None, "Waiting for ESPHome action registration"
    if configured:
        domain, separator, action = configured.partition(".")
        if separator and services.has_service(domain, action):
            return configured, "Configured action available"
    # Home Assistant services can register after the integration has loaded.
    available = getattr(services, "async_services", None)
    if callable(available):
        registry = available()
        esphome = registry.get("esphome", {}) if isinstance(registry, Mapping) else {}
        found, status = find_meter_ack_action(esphome, mac)
        if found:
            return found, status
        return None, status
    return None, "Waiting for ESPHome action registration"
