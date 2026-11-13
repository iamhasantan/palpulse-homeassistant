"""UI setup for the PalPulse timestamped statistics importer."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (CONF_METER_MAC, CONF_ACK_SERVICE, CONF_DISABLE_ACK, CONF_LEGACY_ENERGY_SOURCE, LEGACY_STATISTIC_ID, DOMAIN)
from .history import normalize_mac
from .pricing import (
    CONF_PRICING_MODE, CONF_FLAT_CENTS, CONF_PEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
    WINDOW_FIELDS, MODE_HOME_ASSISTANT, MODE_FLAT, MODE_TOU,
    validate_pricing, CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS,
    CONF_WEEKDAY_FREE, CONF_WEEKEND_FREE,
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
        """Keep the day-to-day settings short; recovery stays available."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "overview", "wizard", "electricity_settings",
                "history_tools", "advanced",
            ],
        )

    async def async_step_electricity_settings(self, user_input=None):
        """All tariff controls in one place."""
        menu = ["pricing"]
        mode = self.config_entry.options.get(
            CONF_PRICING_MODE, MODE_HOME_ASSISTANT
        )
        if mode == MODE_TOU:
            menu.append("free_hours")
        if mode != MODE_HOME_ASSISTANT:
            menu.append("energy_dashboard_cost")
        return self.async_show_menu(
            step_id="electricity_settings", menu_options=menu,
        )

    async def async_step_energy_dashboard_cost(self, user_input=None):
        """Diagnose missing costs; only change Energy prefs with confirmation."""
        from .dashboard_cost import async_check_cost_link, async_link_verified_cost

        mac = self.config_entry.data[CONF_METER_MAC]
        energy_id = f"{DOMAIN}:powerpal_{mac}_energy"
        cost_id = f"{DOMAIN}:powerpal_{mac}_cost"
        writer = self.hass.data.get(f"{DOMAIN}_cost", {}).get(
            self.config_entry.entry_id
        )
        try:
            state = await async_check_cost_link(self.hass, energy_id, cost_id)
        except Exception as err:
            return self.async_abort(
                reason="cost_link_unavailable",
                description_placeholders={"error": str(err)[:300]},
            )
        from .const import CONF_AUTO_LINK_ENERGY_COST

        errors = {}
        if user_input is not None and user_input.get("confirm_link") is True:
            state = await async_link_verified_cost(
                self.hass, energy_id, cost_id, writer,
            )
            if state.status == "linked":
                # The link is repaired now. Persist opt-in separately so a
                # user who unticks it will not have their choice overridden.
                updated = {
                    **self.config_entry.options,
                    CONF_AUTO_LINK_ENERGY_COST: bool(
                        user_input.get("keep_link_active", False)
                    ),
                }
                return self.async_create_entry(data=updated)
            errors["base"] = "cost_link_not_ready"
        return self.async_show_form(
            step_id="energy_dashboard_cost",
            data_schema=vol.Schema({
                vol.Optional("confirm_link", default=False): bool,
                vol.Optional(
                    "keep_link_active",
                    default=self.config_entry.options.get(
                        CONF_AUTO_LINK_ENERGY_COST, False
                    ),
                ): bool,
            }),
            errors=errors,
            description_placeholders={
                "status": state.status.replace("_", " ").title(),
                "guidance": state.guidance,
                "energy_statistic": energy_id,
                "cost_statistic": cost_id,
            },
        )

    async def async_step_history_tools(self, user_input=None):
        """Optional imports and diagnostics, never automatic on upgrade."""
        return self.async_show_menu(
            step_id="history_tools",
            menu_options=[
                "energy_history", "cost_history", "energy_integrity",
            ],
        )

    async def async_step_advanced(self, user_input=None):
        """Occasional troubleshooting and ESP32 recovery options."""
        return self.async_show_menu(
            step_id="advanced",
            menu_options=[
                "connection_check", "history_transport",
                "replace_esp32", "dashboard",
            ],
        )

    async def async_step_wizard(self, user_input=None):
        """Begin a step-by-step setup without touching Recorder or imports."""
        if user_input is None:
            return self.async_show_form(
                step_id="wizard",
                data_schema=vol.Schema({
                    vol.Required("setup_goal"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=[
                            {"value": "red_power5", "label": "Red Energy Power5 (11 am-4 pm, 15 kWh cap)"},
                            {"value": "flat", "label": "Australian flat electricity rate"},
                            {"value": "tou", "label": "Custom time-of-use / free power"},
                            {"value": "ha", "label": "Home Assistant native pricing"},
                            {"value": "old_costs", "label": "Connect previous historical costs only"},
                        ])
                    )
                }),
            )
        goal = user_input["setup_goal"]
        self._wizard_active = True
        self._wizard_pending = dict(self.config_entry.options)
        if goal == "red_power5":
            return await self.async_step_wizard_red_power5()
        if goal == "flat":
            return await self.async_step_flat()
        if goal == "tou":
            return await self.async_step_time_of_use()
        if goal == "ha":
            self._wizard_pending[CONF_PRICING_MODE] = MODE_HOME_ASSISTANT
            return await self.async_step_wizard_cost_choice()
        if goal == "old_costs":
            return await self.async_step_wizard_cost_choice()
        return self.async_abort(reason="wizard_unavailable")

    async def async_step_wizard_red_power5(self, user_input=None):
        """Preset published Power5 windows; require actual paid rates."""
        prior = self.config_entry.options
        rate_keys = (
            CONF_PEAK_CENTS,
            CONF_SHOULDER_CENTS,
            CONF_OFFPEAK_CENTS,
            CONF_SUPPLY_CENTS,
            CONF_FREE_EXCESS_CENTS,
        )
        errors = {}
        if user_input is not None:
            options = {
                **prior,
                CONF_PRICING_MODE: MODE_TOU,
                CONF_WEEKDAY_FREE: (
                    f"{str(user_input['free_start'])[:5]}-"
                    f"{str(user_input['free_end'])[:5]}"
                ),
                CONF_WEEKEND_FREE: (
                    f"{str(user_input['free_start'])[:5]}-"
                    f"{str(user_input['free_end'])[:5]}"
                ),
                # The published Red Energy Victorian TOU bands put evening
                # peak at 4-9pm daily. A saved peak rate without this window
                # silently becomes off-peak, so the explicit Power5 preset
                # must set the full schedule, not only the free hours.
                "weekday_peak": "16:00-21:00",
                "weekend_peak": "16:00-21:00",
                "weekday_shoulder": "",
                "weekend_shoulder": "",
                CONF_FREE_CAP_KWH: user_input[CONF_FREE_CAP_KWH],
                **{key: user_input[key] for key in rate_keys},
            }
            try:
                validate_pricing(options)
            except (ValueError, TypeError) as err:
                reason = str(err)
                if "positive excess rate" in reason:
                    # Highlight the actual invalid field. Never invent a paid
                    # rate from the user's other TOU prices.
                    errors[CONF_FREE_EXCESS_CENTS] = "free_excess_required"
                elif "Tariff windows must stay within one day" in reason:
                    errors["free_end"] = "invalid_free_window"
                elif "Overlapping" in reason:
                    # Existing peak/shoulder windows may be carried forward
                    # from an earlier tariff even though this preset hides
                    # them. Tell the user where to inspect them.
                    errors["base"] = "existing_schedule_overlap"
                else:
                    errors["base"] = "invalid_pricing"
            else:
                self._wizard_pending = options
                return await self.async_step_wizard_cost_choice()
        fields = {}
        for key in rate_keys:
            current = (
                user_input[key] if user_input is not None and key in user_input
                else prior.get(key)
            )
            marker = (
                vol.Required(key, default=current)
                if current is not None else vol.Required(key)
            )
            fields[marker] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=10000, step="any",
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="c/kWh" if key != CONF_SUPPLY_CENTS else "c/day",
                )
            )
        values = user_input or {}
        fields[vol.Required("free_start", default=values.get("free_start", "11:00"))] = selector.TimeSelector()
        fields[vol.Required("free_end", default=values.get("free_end", "16:00"))] = selector.TimeSelector()
        fields[vol.Required(
            CONF_FREE_CAP_KWH,
            default=values.get(CONF_FREE_CAP_KWH, prior.get(CONF_FREE_CAP_KWH, 15)),
        )] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=10000, step="any",
                    mode=selector.NumberSelectorMode.BOX, unit_of_measurement="kWh",
                )
            )
        )
        return self.async_show_form(
            step_id="wizard_red_power5", data_schema=vol.Schema(fields),
            errors=errors,
        )

    async def async_step_wizard_cost_choice(self, user_input=None):
        """Optionally connect a recorded historical monetary source."""
        if user_input is None:
            return self.async_show_form(
                step_id="wizard_cost_choice",
                data_schema=vol.Schema({
                    vol.Required("configure_old_costs", default=False): bool,
                }),
            )
        if not user_input.get("configure_old_costs", False):
            return await self.async_step_wizard_review()
        return await self.async_step_wizard_cost_type()

    async def async_step_wizard_cost_type(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="wizard_cost_type",
                data_schema=vol.Schema({
                    vol.Required(
                        CONF_LEGACY_COST_SOURCE_MODE,
                        default=self.config_entry.options.get(
                            CONF_LEGACY_COST_SOURCE_MODE, SOURCE_CUMULATIVE
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=[
                            {"value": kind, "label": name}
                            for kind, name in _SOURCE_CHOICES.items()
                        ])
                    ),
                }),
            )
        mode = user_input[CONF_LEGACY_COST_SOURCE_MODE]
        if mode not in _SOURCE_CHOICES:
            return self.async_abort(reason="wizard_unavailable")
        self._wizard_cost_mode = mode
        return await self.async_step_wizard_cost_picker()

    async def async_step_wizard_cost_picker(self, user_input=None):
        mode = getattr(self, "_wizard_cost_mode", None)
        if mode not in _SOURCE_CHOICES:
            return self.async_abort(reason="wizard_unavailable")
        errors = {}
        if user_input is not None:
            source = str(user_input.get(CONF_LEGACY_COST_STATISTIC_ID) or "").strip()
            if not source:
                errors["base"] = "source_required"
            elif error := self._save_cost_source(source, mode):
                errors["base"] = error
            else:
                self._wizard_pending[CONF_LEGACY_COST_STATISTIC_ID] = source
                self._wizard_pending[CONF_LEGACY_COST_SOURCE_MODE] = mode
                return await self.async_step_wizard_review()
        pick = (
            selector.StatisticSelector()
            if mode == SOURCE_CUMULATIVE else
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
        )
        return self.async_show_form(
            step_id="wizard_cost_picker",
            data_schema=vol.Schema({
                vol.Required(CONF_LEGACY_COST_STATISTIC_ID): pick,
            }),
            errors=errors,
        )

    async def async_step_wizard_review(self, user_input=None):
        """Single deliberate save. Preview never imports historical rows."""
        pending = getattr(self, "_wizard_pending", None)
        if pending is None:
            return self.async_abort(reason="wizard_unavailable")
        if user_input is not None:
            if user_input.get("confirm_save") is True:
                return self.async_create_entry(data=pending)
            errors = {"base": "confirmation_required"}
        else:
            errors = {}
        mode = pending.get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT)
        is_tou = mode == MODE_TOU
        free = pending.get(CONF_WEEKDAY_FREE, "None") if is_tou else "None"
        def showing(key: str, applicable: bool = True) -> str:
            return str(pending.get(key, "Not configured")) if applicable else "Not applicable"
        return self.async_show_form(
            step_id="wizard_review",
            data_schema=vol.Schema({
                vol.Required("confirm_save", default=False): bool,
            }),
            errors=errors,
            description_placeholders={
                "pricing": _PRICING_CHOICES.get(mode, mode),
                "free_hours": str(free),
                "free_cap": showing(CONF_FREE_CAP_KWH, is_tou),
                "peak": showing(CONF_PEAK_CENTS, is_tou),
                "shoulder": showing(CONF_SHOULDER_CENTS, is_tou),
                "offpeak": showing(CONF_OFFPEAK_CENTS, is_tou),
                "flat": showing(CONF_FLAT_CENTS, mode == MODE_FLAT),
                "supply": showing(CONF_SUPPLY_CENTS, mode != MODE_HOME_ASSISTANT),
                "excess": showing(CONF_FREE_EXCESS_CENTS, is_tou),
                "cost_source": str(pending.get(
                    CONF_LEGACY_COST_STATISTIC_ID, "Not configured"
                ) or "Not configured"),
            },
        )

    async def async_step_dashboard(self, user_input=None):
        """Guide optional Lovelace import without editing HA dashboards."""
        if user_input is not None:
            return self.async_abort(reason="dashboard_guide_complete")
        meter = self.config_entry.data[CONF_METER_MAC]
        return self.async_show_form(
            step_id="dashboard",
            data_schema=vol.Schema({}),
            description_placeholders={
                "dashboard_url": (
                    "https://github.com/iamhasantan/palpulse-homeassistant/"
                    "blob/main/dashboards/palpulse-control-centre.yaml"
                ),
                "meter": meter,
                "energy_statistic": f"palpulse:powerpal_{meter}_energy",
                "cost_statistic": f"palpulse:powerpal_{meter}_cost",
            },
            last_step=True,
        )

    async def async_step_energy_integrity(self, user_input=None):
        """Read-only diagnosis of Recorder sum discontinuities."""
        if user_input is not None:
            return self.async_abort(reason="energy_integrity_reviewed")
        importer = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if importer is None:
            return self.async_abort(reason="history_unavailable")
        try:
            report = await importer.async_integrity_report()
        except Exception as err:
            return self.async_abort(
                reason="energy_integrity_unavailable",
                description_placeholders={"error": str(err)[:400]},
            )
        issues = report["issues"]
        if len(issues) == 1:
            issue = issues[0]
            when = datetime.fromtimestamp(
                issue.hour_start, timezone.utc
            ).astimezone(ZoneInfo(self.hass.config.time_zone))
            status = "One large negative Recorder sum jump detected"
            interval = when.strftime("%d %b %Y %I:%M %p %Z")
            expected = f"{issue.meter_delta:.6f}"
            recorded = f"{issue.recorded_delta:.6f}"
            adjustment = f"{issue.correction:.6f}"
            guidance = (
                "A backup and review are required before any correction. "
                "Home Assistant > Developer Tools > Statistics > "
                "PalPulse timestamped total energy > Adjust sum > Outliers "
                "can adjust the displayed hourly CHANGE at this interval. "
                "Do not use this recommendation if the reported interval "
                "or adjacent raw meter states differ from your observations."
            )
        elif not issues:
            status = "No very large negative sum jumps found in the recent window"
            interval = "None"
            expected = recorded = adjustment = "Not applicable"
            guidance = (
                "This only scans recent hourly statistics. A clean result "
                "does not guarantee all older history is correct."
            )
        else:
            status = f"{len(issues)} large negative sum jumps found"
            interval = "Multiple intervals"
            expected = recorded = adjustment = "Requires manual review"
            guidance = (
                "Multiple anomalies cannot be repaired using one adjustment. "
                "Inspect each event and confirm a verified Recorder backup."
            )
        return self.async_show_form(
            step_id="energy_integrity",
            data_schema=vol.Schema({}),
            description_placeholders={
                "status": status,
                "interval": interval,
                "expected_change": expected,
                "recorded_change": recorded,
                "required_adjustment": adjustment,
                "hours_checked": str(report["hour_count"]),
                "recorder_offset": f'{report["offset"]:.6f}',
                "guidance": guidance,
            },
            last_step=True,
        )

    async def async_step_overview(self, user_input=None):
        """A read-only summary without forcing the user into entity attributes."""
        if user_input is not None:
            return self.async_abort(reason="overview_complete")
        entry_id = self.config_entry.entry_id
        importer = self.hass.data.get(DOMAIN, {}).get(entry_id)
        energy = self._history_manager("energy")
        costs = self._history_manager("cost")
        price_mode = self.config_entry.options.get(
            CONF_PRICING_MODE, MODE_HOME_ASSISTANT
        )
        cost_source = self.config_entry.options.get(
            CONF_LEGACY_COST_STATISTIC_ID, ""
        )
        last_stamp = (
            importer.health["last_completed_utc_hour"] if importer else None
        )
        last_hour = (
            datetime.fromtimestamp(
                last_stamp, timezone.utc
            ).astimezone(ZoneInfo(self.hass.config.time_zone)).strftime(
                "%d %b %Y, %I:%M %p %Z"
            )
            if last_stamp is not None else "No complete hour yet"
        )
        cost_recorder = self.hass.data.get(f"{DOMAIN}_cost", {}).get(entry_id)
        cost_state = (
            cost_recorder.status if cost_recorder else None
        )
        return self.async_show_form(
            step_id="overview",
            data_schema=vol.Schema({}),
            description_placeholders={
                "energy_status": importer.health["status"] if importer else "Unavailable",
                "last_energy_hour": last_hour,
                "energy_gaps": str(
                    importer.health["skipped_hours"] if importer else "Unavailable"
                ),
                "pricing": _PRICING_CHOICES.get(price_mode, str(price_mode)),
                "cost_source": str(cost_source or "Not configured"),
                "cost_status": (
                    "Error: " + str(cost_state["last_error"])
                    if cost_state and cost_state["last_error"]
                    else "Active" if cost_state and cost_state["last_costed_hour"]
                    else "Waiting" if cost_state
                    else "Not enabled"
                ),
                "usage_history": (
                    energy.progress["stage"] if energy else "Unavailable"
                ),
                "cost_history": (
                    costs.progress["stage"] if costs else "Unavailable"
                ),
            },
            last_step=True,
        )

    async def async_step_free_hours(self, user_input=None):
        """Simple time-picker setup for a single daily free-power window.

        Advanced users keep their weekday/weekend/peak settings in the
        existing time-of-use pricing form.
        """
        prior = self.config_entry.options
        if prior.get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT) != MODE_TOU:
            return self.async_abort(reason="enable_tou_first")

        def window_defaults():
            # Do not hide conflicting or multi-window advanced configurations.
            weekday = str(prior.get("weekday_free", "")).strip()
            weekend = str(prior.get("weekend_free", "")).strip()
            if weekday and weekday == weekend and weekday.count("-") == 1 and "," not in weekday:
                return tuple(weekday.split("-", 1))
            return (None, None)  # No retailer-specific default for a generic tariff

        start_default, end_default = window_defaults()
        errors = {}
        if user_input is not None:
            start = str(user_input["free_start"])[:5]
            end = str(user_input["free_end"])[:5]
            options = {
                **prior,
                "weekday_free": f"{start}-{end}",
                "weekend_free": f"{start}-{end}",
                CONF_FREE_CAP_KWH: user_input[CONF_FREE_CAP_KWH],
                CONF_FREE_EXCESS_CENTS: user_input[CONF_FREE_EXCESS_CENTS],
            }
            try:
                validate_pricing(options)
            except ValueError:
                errors["base"] = "invalid_pricing"
            else:
                return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="free_hours",
            data_schema=vol.Schema({
                (vol.Required("free_start", default=start_default) if start_default else vol.Required("free_start")): selector.TimeSelector(),
                (vol.Required("free_end", default=end_default) if end_default else vol.Required("free_end")): selector.TimeSelector(),
                vol.Required(
                    CONF_FREE_CAP_KWH,
                    default=prior.get(CONF_FREE_CAP_KWH, 0),
                ): selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=10000, step="any",
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="kWh",
                )),
                vol.Required(
                    CONF_FREE_EXCESS_CENTS,
                    default=prior.get(CONF_FREE_EXCESS_CENTS, 0),
                ): selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=10000, step="any",
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="c/kWh",
                )),
            }),
            errors=errors,
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
                        options=[
                            {"value": value, "label": label}
                            for value, label in _SOURCE_CHOICES.items()
                        ],
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

    async def async_step_connection_check(self, user_input=None):
        """Read-only onboarding health without creating Recorder rows."""
        if user_input is not None:
            return self.async_abort(reason="overview_complete")
        importer = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        state = importer.health if importer else {}
        ack = str(state.get("ack_service") or "")
        available = (
            self.hass.services.has_service(*ack.split(".", 1))
            if ack and "." in ack else False
        )
        return self.async_show_form(
            step_id="connection_check",
            data_schema=vol.Schema({}),
            description_placeholders={
                "status": str(state.get("status", "Not started")),
                "last_event": (
                    datetime.fromtimestamp(
                        state["last_event_received"], timezone.utc
                    ).isoformat()
                    if state.get("last_event_received") is not None
                    else "No meter events received"
                ),
                "completed_hour": (
                    datetime.fromtimestamp(
                        state["last_completed_utc_hour"], timezone.utc
                    ).isoformat()
                    if state.get("last_completed_utc_hour") is not None
                    else "No completed hour"
                ),
                "ack": ack or "Disabled (best-effort events)",
                "ack_ready": "Available" if available else (
                    "Not found; check ESPHome action and firmware" if ack
                    else "Not configured"
                ),
                "error": str(state.get("last_import_error") or
                             state.get("ack_error") or "None"),
            },
            last_step=True,
        )

    async def async_step_replace_esp32(self, user_input=None):
        """Explicit, time-limited rebase for a replacement ESP32 checkpoint."""
        if user_input is not None and user_input.get("confirm_replacement") is True:
            importer = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
            if importer is None:
                return self.async_abort(reason="importer_unavailable")
            await importer.async_arm_device_replacement()
            return self.async_abort(reason="replacement_armed")
        return self.async_show_form(
            step_id="replace_esp32",
            data_schema=vol.Schema({
                vol.Required("confirm_replacement", default=False): bool,
            }),
        )

    async def async_step_history_transport(self, user_input=None):
        """Acknowledged history auto-discovery, enabled unless explicitly disabled."""
        current = str(self.config_entry.options.get(CONF_ACK_SERVICE, ""))
        disabled = self.config_entry.options.get(CONF_DISABLE_ACK, False) is True
        errors = {}
        if user_input is not None:
            service = str(user_input.get(CONF_ACK_SERVICE) or "").strip()
            opt_out = user_input.get(CONF_DISABLE_ACK, disabled)
            if type(opt_out) is not bool:
                errors["base"] = "invalid_ack_service"
            elif service and not re.fullmatch(r"esphome\.[a-z][a-z0-9_]+", service):
                errors["base"] = "invalid_ack_service"
            else:
                options = {**self.config_entry.options, CONF_DISABLE_ACK: opt_out}
                if service:
                    options[CONF_ACK_SERVICE] = service
                else:
                    options.pop(CONF_ACK_SERVICE, None)
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="history_transport",
            data_schema=vol.Schema({
                vol.Optional(CONF_DISABLE_ACK, default=disabled): bool,
                vol.Optional(CONF_ACK_SERVICE, default=current): str,
            }),
            errors=errors,
        )

    async def async_step_energy_source(self, user_input=None):
        """Choose an energy migration source without touching old statistics."""
        manager = self._history_manager("energy")
        current = self.config_entry.options.get(CONF_LEGACY_ENERGY_SOURCE)
        errors = {}
        if user_input is not None:
            source = str(user_input.get(CONF_LEGACY_ENERGY_SOURCE) or "").strip()
            target = f"{DOMAIN}:powerpal_{self.config_entry.data[CONF_METER_MAC]}_energy"
            if not source or not _SOURCE_ID_RE.fullmatch(source) or source == target:
                errors["base"] = "invalid_energy_source"
            elif (manager and manager.journal.get("status") not in
                  {None, "undone"} and source != manager.source_id):
                errors["base"] = "energy_migration_locked"
            else:
                return self.async_create_entry(
                    data={**self.config_entry.options,
                          CONF_LEGACY_ENERGY_SOURCE: source}
                )
        return self.async_show_form(
            step_id="energy_source",
            data_schema=vol.Schema({
                (vol.Required(CONF_LEGACY_ENERGY_SOURCE, default=current)
                 if current else vol.Required(CONF_LEGACY_ENERGY_SOURCE)):
                    selector.StatisticSelector(),
            }),
            errors=errors,
        )

    async def async_step_energy_history(self, user_input=None):
        """Guided workflow, separate from tariff configuration."""
        return self.async_show_menu(
            step_id="energy_history",
            menu_options=[
                "energy_source", "energy_preview", "energy_import", "energy_resume", "energy_undo",
            ],
        )

    async def async_step_cost_history(self, user_input=None):
        """Present the cost source first, before any import controls."""
        manager = self._history_manager("cost")
        return self.async_show_menu(
            step_id="cost_history",
            menu_options=[
                "cost_source_setup", "cost_preview", "cost_import",
                "cost_resume", "cost_undo",
            ],
            description_placeholders={
                "source": (
                    manager.source_id or "Not configured"
                    if manager is not None else "Unavailable"
                ),
                "status": (
                    manager.progress["stage"] if manager is not None
                    else "Unavailable"
                ),
            },
        )

    async def async_step_cost_source_setup(self, user_input=None):
        """Open the picker from within Historical electricity costs."""
        return await self.async_step_historical_costs(user_input)

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
            # Native HA wizard displays preview/error itself. Legacy entity
            # buttons still publish notifications for backwards compatibility.
            await manager.async_preview(notify=False)
        if not getattr(manager, "_preview", None):
            if kind == "cost":
                return self.async_show_menu(
                    step_id="cost_preview_blocked",
                    menu_options=[
                        "cost_check_again", "cost_source_setup",
                        "overview", "cost_history",
                    ],
                    description_placeholders={
                        "message": manager.last_message[:1400],
                    },
                )
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
        """Guide missing prerequisites rather than aborting a safe preview."""
        manager = self._history_manager("cost")
        if manager is None:
            return self.async_abort(reason="history_unavailable")
        if not manager.source_id:
            return await self.async_step_historical_costs()
        if user_input is not None:
            # Do not invalidate an already-armed preview on form submission.
            return await self._show_history_preview("cost", user_input)
        return await self.async_step_cost_readiness()

    async def async_step_cost_readiness(self, user_input=None):
        """Screen for the distinct legacy source and live target prerequisites."""
        manager = self._history_manager("cost")
        if manager is None:
            return self.async_abort(reason="history_unavailable")
        info = await manager.async_readiness()
        state = info["status"]
        if state == "ready":
            return await self._show_history_preview("cost", None)
        if state == "source_missing":
            return await self.async_step_historical_costs()
        if state == "previous_import":
            actions = ["cost_resume", "cost_undo", "cost_history"]
        elif state == "pricing_disabled":
            actions = [
                "pricing", "cost_check_again",
                "cost_source_setup", "overview", "cost_history",
            ]
        else:
            actions = [
                "cost_check_again", "cost_source_setup",
                "pricing", "overview", "cost_history",
            ]
        return self.async_show_menu(
            step_id="cost_readiness",
            menu_options=actions,
            description_placeholders={
                "source": info["source"],
                "target": info["target"],
                "status": {
                    "source_invalid": "Invalid historical source",
                    "source_not_recorded": "Historical source not recorded",
                    "pricing_disabled": "Custom pricing not enabled",
                    "cost_writer_error": "Live cost writer error",
                    "writer_unavailable": "Cost writer unavailable",
                    "waiting_for_cost": "Waiting for verified live cost",
                    "target_invalid": "Invalid PalPulse cost statistic",
                    "recorder_unavailable": "Recorder unavailable",
                    "previous_import": "Previous migration detected",
                }.get(state, state),
                "writer_status": info["writer_status"],
                "guidance": info["guidance"][:1400],
            },
        )

    async def async_step_cost_check_again(self, user_input=None):
        """Re-read Recorder. Never generate an artificial cost baseline."""
        return await self.async_step_cost_readiness()

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
            return (
                await self.async_step_cost_preview()
                if kind == "cost"
                else await self._show_history_preview(kind, None)
            )
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
                if getattr(self, "_wizard_active", False):
                    self._wizard_pending = options
                    return await self.async_step_wizard_cost_choice()
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
                if getattr(self, "_wizard_active", False):
                    self._wizard_pending = options
                    return await self.async_step_wizard_cost_choice()
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
