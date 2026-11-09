"""UI setup for the PalPulse timestamped statistics importer."""

from __future__ import annotations

import re


import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_METER_MAC, DOMAIN
from .history import normalize_mac
from .pricing import (
    CONF_PRICING_MODE, CONF_FLAT_CENTS, CONF_PEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
    WINDOW_FIELDS, MODE_HOME_ASSISTANT, MODE_FLAT, MODE_TOU,
    validate_pricing, CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS,
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
CONF_LEGACY_COST_STATISTIC_ID = "legacy_cost_statistic_id"
CONF_LEGACY_COST_SOURCE_MODE = "legacy_cost_source_mode"
SOURCE_CUMULATIVE = "cumulative"
SOURCE_DAILY_RESET = "daily_reset"
_SOURCE_CHOICES = {
    SOURCE_CUMULATIVE: "Existing cumulative hourly cost statistic",
    SOURCE_DAILY_RESET: "Daily-reset cost sensor (hourly closing states)",
}
_SOURCE_ID_RE = re.compile(r"^(?:sensor\.[a-z0-9_]+|[a-z0-9_]+:[a-z0-9_]+)$")
_RATE_SCHEMA = vol.All(vol.Coerce(float), vol.Range(min=0, max=10000))


class PalPulseOptionsFlow(config_entries.OptionsFlowWithReload):
    """Independent tariff and historical-cost settings that preserve each other."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["pricing", "historical_costs", "energy_history", "cost_history"],
        )

    async def async_step_pricing(self, user_input=None):
        if user_input is not None:
            self._mode = user_input[CONF_PRICING_MODE]
            if self._mode == MODE_HOME_ASSISTANT:
                return self.async_create_entry(data={
                    **self.config_entry.options, CONF_PRICING_MODE: self._mode,
                })
            if self._mode == MODE_FLAT:
                return await self.async_step_flat()
            return await self.async_step_time_of_use()

        return self.async_show_form(
            step_id="pricing",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_PRICING_MODE,
                    default=self.config_entry.options.get(
                        CONF_PRICING_MODE, MODE_HOME_ASSISTANT
                    ),
                ): vol.In(_PRICING_CHOICES),
            }),
        )

    async def async_step_historical_costs(self, user_input=None):
        """Choose source type first, then pick a named entity/statistic."""
        if user_input is not None:
            self._legacy_source_mode = user_input[CONF_LEGACY_COST_SOURCE_MODE]
            if self._legacy_source_mode == SOURCE_CUMULATIVE:
                return await self.async_step_cost_source_statistic()
            return await self.async_step_cost_source_daily()
        return self.async_show_form(
            step_id="historical_costs",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_LEGACY_COST_SOURCE_MODE,
                    default=self.config_entry.options.get(
                        CONF_LEGACY_COST_SOURCE_MODE, SOURCE_CUMULATIVE
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(_SOURCE_CHOICES),
                        translation_key=CONF_LEGACY_COST_SOURCE_MODE,
                    )
                ),
            }),
        )

    def _source_schema(self, field_selector):
        """The picker may be left empty to clear a migration source."""
        key = CONF_LEGACY_COST_STATISTIC_ID
        prior = str(self.config_entry.options.get(key, "")).strip()
        mode = getattr(self, "_legacy_source_mode", SOURCE_CUMULATIVE)
        if prior and self.config_entry.options.get(
            CONF_LEGACY_COST_SOURCE_MODE, SOURCE_CUMULATIVE
        ) == mode:
            marker = vol.Optional(key, default=prior)
        else:
            marker = vol.Optional(key)
        return vol.Schema({marker: field_selector})

    def _save_cost_source(self, source: str, kind: str) -> str | None:
        """Validate source and journal without changing unrelated tariffs."""
        meter = self.config_entry.data[CONF_METER_MAC]
        target = f"{DOMAIN}:powerpal_{meter}_cost"
        if source and (not _SOURCE_ID_RE.fullmatch(source) or source == target):
            return "invalid_source"
        manager = self.hass.data.get(
            f"{DOMAIN}_cost_migration", {}
        ).get(self.config_entry.entry_id)
        previous_source = str(self.config_entry.options.get(
            CONF_LEGACY_COST_STATISTIC_ID, ""
        )).strip()
        previous_kind = self.config_entry.options.get(
            CONF_LEGACY_COST_SOURCE_MODE, SOURCE_CUMULATIVE
        )
        if (
            manager is not None
            and manager.journal.get("status") not in {None, "undone"}
            and (source != previous_source or kind != previous_kind)
        ):
            return "migration_locked"
        return None

    async def _save_source_form(self, step_id, field_selector, user_input):
        kind = getattr(self, "_legacy_source_mode", SOURCE_CUMULATIVE)
        errors = {}
        if user_input is not None:
            source = str(user_input.get(CONF_LEGACY_COST_STATISTIC_ID) or "").strip()
            if error := self._save_cost_source(source, kind):
                errors["base"] = error
            else:
                return self.async_create_entry(data={
                    **self.config_entry.options,
                    CONF_LEGACY_COST_STATISTIC_ID: source,
                    CONF_LEGACY_COST_SOURCE_MODE: kind,
                })
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._source_schema(field_selector),
            errors=errors,
        )

    async def async_step_cost_source_statistic(self, user_input=None):
        """Browse Recorder's actual cumulative statistic identifiers."""
        return await self._save_source_form(
            "cost_source_statistic",
            selector.StatisticSelector(),
            user_input,
        )

    async def async_step_cost_source_daily(self, user_input=None):
        """Browse sensor entities for an existing daily-reset cost sensor."""
        return await self._save_source_form(
            "cost_source_daily",
            selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            user_input,
        )

    def _history_manager(self, kind: str):
        key = f"{DOMAIN}_{'migration' if kind == 'energy' else 'cost_migration'}"
        return self.hass.data.get(key, {}).get(self.config_entry.entry_id)

    async def async_step_energy_history(self, user_input=None):
        """Guided workflow, separate from tariff configuration."""
        return self.async_show_menu(
            step_id="energy_history",
            menu_options=[
                "energy_preview", "energy_import", "energy_resume", "energy_undo",
            ],
        )

    async def async_step_cost_history(self, user_input=None):
        """Cost migration has its own preview, status, import and undo."""
        return self.async_show_menu(
            step_id="cost_history",
            menu_options=[
                "cost_preview", "cost_import", "cost_resume", "cost_undo",
            ],
        )

    def _preview_summary(self, manager) -> dict[str, str]:
        """Short readable fields instead of raw Recorder JSON or entity IDs."""
        plan = getattr(manager, "_preview", None)
        return {
            "source": manager.source_id or "Not configured",
            "target": manager.target_id,
            "rows": f"{plan.count:,}" if plan else "0",
            "gaps": str(plan.gaps) if plan else "0",
            "message": manager.last_message[:1600],
        }

    async def _show_history_preview(self, kind: str, user_input):
        manager = self._history_manager(kind)
        if manager is None:
            return self.async_abort(reason="history_unavailable")
        if user_input is None:
            await manager.async_preview()
        if not getattr(manager, "_preview", None):
            return self.async_abort(
                reason="preview_unavailable",
                description_placeholders={
                    "message": manager.last_message[:1400],
                },
            )
        if user_input is not None:
            if user_input.get("continue_to_import") is True:
                return await self._confirm_history_action(kind, "import", None)
            return self.async_abort(reason="preview_complete")
        return self.async_show_form(
            step_id=f"{kind}_preview",
            data_schema=vol.Schema({
                vol.Required("continue_to_import", default=False): bool,
            }),
            description_placeholders=self._preview_summary(manager),
        )

    async def async_step_energy_preview(self, user_input=None):
        return await self._show_history_preview("energy", user_input)

    async def async_step_cost_preview(self, user_input=None):
        return await self._show_history_preview("cost", user_input)

    async def _confirm_history_action(self, kind: str, action: str, user_input):
        """Only explicit confirmation can start a mutating operation."""
        manager = self._history_manager(kind)
        if manager is None:
            return self.async_abort(reason="history_unavailable")
        if manager.busy:
            return self.async_abort(reason="history_busy")
        status = manager.journal.get("status")
        if action == "import" and status not in (None, "undone"):
            # An interrupted migration must use the clearly marked Resume step.
            return self.async_abort(reason="history_already_started")
        if action == "import" and not getattr(manager, "_preview", None):
            return await self._show_history_preview(kind, None)
        if action == "resume" and status not in ("prepared", "applying", "failed"):
            return self.async_abort(reason="nothing_to_resume")
        if action == "undo" and status not in (
            "prepared", "applying", "applied", "failed", "undoing"
        ):
            return self.async_abort(reason="nothing_to_undo")
        if user_input is not None:
            if user_input.get("confirm_action") is not True:
                return self.async_show_form(
                    step_id=f"{kind}_{action}",
                    data_schema=vol.Schema({
                        vol.Required("confirm_action", default=False): bool,
                    }),
                    errors={"base": "confirmation_required"},
                    description_placeholders=self._preview_summary(manager),
                )
            self._operation_manager = manager
            self._operation_kind = kind
            self._operation_action = action
            coroutine = {
                "import": manager.async_migrate,
                "resume": manager.async_resume,
                "undo": manager.async_undo,
            }[action]()
            self._operation_task = self.hass.async_create_task(coroutine)
            return await self.async_step_history_progress()
        return self.async_show_form(
            step_id=f"{kind}_{action}",
            data_schema=vol.Schema({
                vol.Required("confirm_action", default=False): bool,
            }),
            description_placeholders=self._preview_summary(manager),
        )

    async def async_step_energy_import(self, user_input=None):
        return await self._confirm_history_action("energy", "import", user_input)

    async def async_step_cost_import(self, user_input=None):
        return await self._confirm_history_action("cost", "import", user_input)

    async def async_step_energy_resume(self, user_input=None):
        return await self._confirm_history_action("energy", "resume", user_input)

    async def async_step_cost_resume(self, user_input=None):
        return await self._confirm_history_action("cost", "resume", user_input)

    async def async_step_energy_undo(self, user_input=None):
        return await self._confirm_history_action("energy", "undo", user_input)

    async def async_step_cost_undo(self, user_input=None):
        return await self._confirm_history_action("cost", "undo", user_input)

    async def async_step_history_progress(self, user_input=None):
        """Native HA progress screen for the backup, import or undo."""
        task = getattr(self, "_operation_task", None)
        if task is None:
            return self.async_abort(reason="history_unavailable")
        if not task.done():
            return self.async_show_progress(
                step_id="history_progress",
                progress_action="processing_history",
                progress_task=task,
                description_placeholders={
                    "operation": self._operation_action,
                },
            )
        try:
            task.result()
        except Exception as err:
            self._operation_error = str(err)
            return self.async_show_progress_done(next_step_id="history_failed")
        return self.async_show_progress_done(next_step_id="history_finished")

    async def async_step_history_finished(self, user_input=None):
        return self.async_abort(
            reason="history_finished",
            description_placeholders={
                "message": self._operation_manager.last_message[:1200],
            },
        )

    async def async_step_history_failed(self, user_input=None):
        return self.async_abort(
            reason="history_failed",
            description_placeholders={
                "message": getattr(self, "_operation_error", "Unknown error")[:1200],
            },
        )

    async def async_step_flat(self, user_input=None):
        errors = {}
        if user_input is not None:
            options = {**self.config_entry.options, CONF_PRICING_MODE: MODE_FLAT, **user_input}
            try:
                validate_pricing(options)
            except ValueError:
                errors["base"] = "invalid_pricing"
            else:
                return self.async_create_entry(data=options)
        prior = self.config_entry.options
        schema = vol.Schema({
            (vol.Required(CONF_FLAT_CENTS, default=prior[CONF_FLAT_CENTS])
             if CONF_FLAT_CENTS in prior else vol.Required(CONF_FLAT_CENTS)): _RATE_SCHEMA,
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
            options = {**self.config_entry.options, CONF_PRICING_MODE: MODE_TOU, **user_input}
            try:
                validate_pricing(options)
            except ValueError:
                errors["base"] = "invalid_pricing"
            else:
                return self.async_create_entry(data=options)
        prior = self.config_entry.options
        schema_fields = {
            (vol.Required(field, default=prior[field])
             if field in prior else vol.Required(field)): _RATE_SCHEMA
            for field in (
                CONF_PEAK_CENTS, CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS,
            )
        }
        schema_fields[vol.Required(
            CONF_SUPPLY_CENTS, default=prior.get(CONF_SUPPLY_CENTS, 0)
        )] = _RATE_SCHEMA
        schema_fields[vol.Required(
            CONF_FREE_CAP_KWH, default=prior.get(CONF_FREE_CAP_KWH, 0)
        )] = vol.All(vol.Coerce(float), vol.Range(min=0, max=10000))
        schema_fields[vol.Required(
            CONF_FREE_EXCESS_CENTS, default=prior.get(CONF_FREE_EXCESS_CENTS, 0)
        )] = _RATE_SCHEMA
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
