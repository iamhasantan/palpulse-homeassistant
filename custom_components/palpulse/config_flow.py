"""UI setup for the PalPulse timestamped statistics importer."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_METER_MAC, DOMAIN
from .history import normalize_mac
from .pricing import (
    CONF_PRICING_MODE, CONF_FLAT_CENTS, CONF_PEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
    WINDOW_FIELDS, MODE_HOME_ASSISTANT, MODE_FLAT, MODE_TOU,
    validate_pricing,
)


class PalPulseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Select the precise Powerpal meter whose records are allowed."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Expose optional pricing independently of meter configuration."""
        return PalPulseOptionsFlow()

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
                    title="PalPulse for Home Assistant",
                    data={CONF_METER_MAC: mac},
                )

        schema = vol.Schema({vol.Required(CONF_METER_MAC): str})
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )


_PRICING_CHOICES = {
    MODE_HOME_ASSISTANT: "Home Assistant manages costs (default)",
    MODE_FLAT: "Custom flat electricity tariff",
    MODE_TOU: "Custom time-of-use electricity tariff",
}
_RATE_SCHEMA = vol.All(vol.Coerce(float), vol.Range(min=0, max=10000))


class PalPulseOptionsFlow(config_entries.OptionsFlowWithReload):
    """User-configured price entity; native HA pricing remains the default."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            self._mode = user_input[CONF_PRICING_MODE]
            if self._mode == MODE_HOME_ASSISTANT:
                return self.async_create_entry(data={CONF_PRICING_MODE: self._mode})
            if self._mode == MODE_FLAT:
                return await self.async_step_flat()
            return await self.async_step_time_of_use()

        schema = vol.Schema({
            vol.Required(
                CONF_PRICING_MODE,
                default=self.config_entry.options.get(
                    CONF_PRICING_MODE, MODE_HOME_ASSISTANT
                ),
            ): vol.In(_PRICING_CHOICES)
        })
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_flat(self, user_input=None):
        errors = {}
        if user_input is not None:
            options = {CONF_PRICING_MODE: MODE_FLAT, **user_input}
            try:
                validate_pricing(options)
            except ValueError:
                errors["base"] = "invalid_pricing"
            else:
                return self.async_create_entry(data=options)
        prior = self.config_entry.options
        schema = vol.Schema({
            vol.Required(
                CONF_FLAT_CENTS, default=prior.get(CONF_FLAT_CENTS, 0)
            ): _RATE_SCHEMA,
            vol.Required(
                CONF_SUPPLY_CENTS, default=prior.get(CONF_SUPPLY_CENTS, 0)
            ): _RATE_SCHEMA,
        })
        return self.async_show_form(
            step_id="flat", data_schema=self.add_suggested_values_to_schema(
                schema, user_input or prior
            ), errors=errors
        )

    async def async_step_time_of_use(self, user_input=None):
        errors = {}
        if user_input is not None:
            options = {CONF_PRICING_MODE: MODE_TOU, **user_input}
            try:
                validate_pricing(options)
            except ValueError:
                errors["base"] = "invalid_pricing"
            else:
                return self.async_create_entry(data=options)
        prior = self.config_entry.options
        schema_fields = {
            vol.Required(field, default=prior.get(field, 0)): _RATE_SCHEMA
            for field in (
                CONF_PEAK_CENTS, CONF_SHOULDER_CENTS,
                CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
            )
        }
        schema_fields.update({
            vol.Optional(field, default=prior.get(field, "")): str
            for field in WINDOW_FIELDS
        })
        schema = vol.Schema(schema_fields)
        return self.async_show_form(
            step_id="time_of_use", data_schema=self.add_suggested_values_to_schema(
                schema, user_input or prior
            ), errors=errors
        )
