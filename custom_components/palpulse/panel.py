"""Register the PalPulse sidebar panel without taking over user dashboards."""

from __future__ import annotations

from pathlib import Path
import logging

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN
from .panel_api import async_register_panel_api

_LOGGER = logging.getLogger(__name__)
PANEL_PATH = "palpulse"
PANEL_MODULE = "/api/palpulse_frontend/palpulse-panel.js?v=0.3b3"
PANEL_KEY = f"{DOMAIN}_panel_registered"
STATIC_KEY = f"{DOMAIN}_panel_static_registered"


async def async_setup_panel(hass) -> None:
    """Install a single admin-only panel for any number of Powerpal meters."""
    async_register_panel_api(hass)
    if hass.data.get(PANEL_KEY):
        return
    if frontend.async_panel_exists(hass, PANEL_PATH):
        # Never remove or override a user-defined panel with the same URL.
        _LOGGER.warning(
            "PalPulse sidebar path /%s already exists; leaving it untouched",
            PANEL_PATH,
        )
        return
    if not hass.data.get(STATIC_KEY):
        assets = Path(__file__).parent / "frontend"
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/api/palpulse_frontend", str(assets), False)]
        )
        hass.data[STATIC_KEY] = True

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="palpulse-control-centre",
        frontend_url_path=PANEL_PATH,
        sidebar_title="PalPulse",
        sidebar_icon="mdi:flash",
        module_url=PANEL_MODULE,
        require_admin=True,
        config_panel_domain=DOMAIN,
        config={},
    )
    hass.data[PANEL_KEY] = True


def async_remove_panel_if_unused(hass) -> None:
    """Keep a single panel until the last PalPulse config entry unloads."""
    if hass.data.get(DOMAIN):
        return
    if hass.data.pop(PANEL_KEY, False):
        frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
