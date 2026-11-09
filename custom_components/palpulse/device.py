"""Stable Home Assistant device identity for all PalPulse meter entities.

Only presentation and grouping change. Entity unique IDs, Recorder statistic
IDs, and Home Assistant config-entry identity remain exactly as before.
"""

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def meter_device_info(mac: str) -> DeviceInfo:
    """Group meter controls, status, and pricing under one discoverable device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"powerpal_{mac}")},
        name=f"PalPulse Powerpal {mac[-6:].upper()}",
        manufacturer="PalPulse",
        model="Powerpal electricity monitor",
        configuration_url="https://github.com/iamhasantan/palpulse-homeassistant",
    )
