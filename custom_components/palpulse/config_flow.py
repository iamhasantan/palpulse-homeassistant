"""UI setup for the PalPulse timestamped statistics importer."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import CONF_METER_MAC, DOMAIN
from .history import normalize_mac


class PalPulseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Select the precise Powerpal meter whose records are allowed."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                mac = normalize_mac(user_input[CONF_METER_MAC])
            except (ValueError, KeyError):
                errors["base"] = "invalid_mac"
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"PalPulse {mac.upper()}",
                    data={CONF_METER_MAC: mac},
                )

        schema = vol.Schema({vol.Required(CONF_METER_MAC): str})
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
