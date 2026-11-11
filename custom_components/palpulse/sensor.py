"""Visible, live progress entities for safe legacy statistics migration."""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.core import callback

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .device import meter_device_info
from .pricing import (
    CONF_PRICING_MODE, CONF_FREE_CAP_KWH, MODE_HOME_ASSISTANT, daily_supply_dollars,
    price_for_datetime,
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Expose status and progress on the integration's Entities page."""
    manager = hass.data[f"{DOMAIN}_migration"][entry.entry_id]
    meter = entry.data["meter_mac"]
    importer = hass.data[DOMAIN][entry.entry_id]
    entities = [
        MigrationStatusSensor(meter, manager),
        MigrationProgressSensor(meter, manager),
        LiveHistoryStatusSensor(meter, importer),
    ]
    # Native HA pricing remains untouched until custom pricing is enabled.
    if getattr(entry, "options", {}).get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT) != MODE_HOME_ASSISTANT:
        entities.extend([
            DailySupplyChargeSensor(meter, entry, hass.config.currency),
            CostImportStatusSensor(
                meter, hass.data[f"{DOMAIN}_cost"][entry.entry_id]
            ),
        ])
        # With a capped free allowance, a fixed schedule cannot accurately
        # report the marginal price until every minute of live consumption is
        # accounted for. Omit that misleading price sensor for capped offers.
        if not float(entry.options.get(CONF_FREE_CAP_KWH, 0)):
            entities.append(ElectricityPriceSensor(meter, entry, hass.config.currency))
    cost_migration = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry.entry_id)
    if cost_migration:
        entities.extend([
            CostMigrationStatusSensor(meter, cost_migration),
            CostMigrationProgressSensor(meter, cost_migration),
        ])
    async_add_entities(entities)


class MigrationSensor(SensorEntity):
    """Entity subscribed to the same manager used by migration buttons."""

    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = True
    _attr_entity_registry_visible_default = False  # Open Configure for migration progress.

    def __init__(self, meter: str, manager, suffix: str, name: str) -> None:
        self._manager = manager
        self._attr_unique_id = f"palpulse_{meter}_migration_{suffix}"
        self._attr_device_info = meter_device_info(meter)
        self._attr_name = name
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        self._unsubscribe = self._manager.subscribe(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    @property
    def extra_state_attributes(self) -> dict:
        state = self._manager.progress
        return {
            "journal_status": state["journal_status"],
            "completed_rows": state["completed_rows"],
            "total_rows": state["total_rows"],
            "progress_percent": state["percent"],
            "resume_available": state["resume_available"],
            "last_message": state["last_message"][:1024],
            "last_error": state["last_error"],
            "backup_id": state["pre_migration_backup"],
            "last_updated": state["last_updated"],
        }


class MigrationStatusSensor(MigrationSensor):
    """Current phase, including interruption detection on HA restart."""

    _attr_icon = "mdi:database-clock-outline"

    def __init__(self, meter, manager) -> None:
        super().__init__(meter, manager, "status", "PalPulse migration status")

    @property
    def native_value(self) -> str:
        return self._manager.progress["stage"].replace("_", " ").title()


class MigrationProgressSensor(MigrationSensor):
    """Percentage of Recorder rows confirmed against the journal."""

    _attr_icon = "mdi:progress-check"
    _attr_native_unit_of_measurement = "%"
    _attr_suggested_display_precision = 1

    def __init__(self, meter, manager) -> None:
        super().__init__(meter, manager, "progress", "PalPulse migration progress")

    @property
    def native_value(self) -> float | None:
        return self._manager.progress["percent"]


class LiveHistoryStatusSensor(SensorEntity):
    """Independent health and recovery state of LIVE hourly energy imports."""

    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Poll to detect HA event-delivery outages even if no new event arrives.
    _attr_should_poll = True
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, meter: str, importer) -> None:
        self._importer = importer
        self._attr_unique_id = f"palpulse_{meter}_live_history_status"
        self._attr_device_info = meter_device_info(meter)
        self._attr_name = "PalPulse live history status"
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        self._unsubscribe = self._importer.subscribe(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    @property
    def native_value(self) -> str:
        return self._importer.health["status"]

    @property
    def extra_state_attributes(self) -> dict:
        status = self._importer.health
        return {
            "last_event_received": self._as_utc_iso(status["last_event_received"]),
            "last_completed_utc_hour": self._as_utc_iso(status["last_completed_utc_hour"]),
            "blocked_utc_hour": self._as_utc_iso(status["blocked_utc_hour"]),
            "skipped_hours": status["skipped_hours"],
            "sparse_recovered_hours": status["sparse_recovered_hours"],
            "last_sparse_utc_hour": self._as_utc_iso(status["last_sparse_utc_hour"]),
            "last_gap_utc_hour": self._as_utc_iso(status["last_gap_utc_hour"]),
            "pending_minute_records": status["pending_minute_records"],
            "message": status["message"],
            "last_import_error": status["last_import_error"],
            "ack_service": status["ack_service"],
            "last_acknowledged_utc_minute": self._as_utc_iso(status["last_acknowledged_utc_minute"]),
            "ack_error": status["ack_error"],
            "statistic_id": status["statistic_id"],
        }

    @staticmethod
    def _as_utc_iso(timestamp: int | None) -> str | None:
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


class ElectricityPriceSensor(SensorEntity):
    """Current local-time tariff, ready for HA Energy's current-price option."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_icon = "mdi:currency-usd"
    _attr_suggested_display_precision = 5

    def __init__(self, meter, entry, currency) -> None:
        self._entry = entry
        self._attr_unique_id = f"palpulse_{meter}_electricity_unit_price"
        self._attr_device_info = meter_device_info(meter)
        self._attr_name = "PalPulse electricity unit price"
        self._attr_native_unit_of_measurement = f"{currency}/kWh"
        self._unsubscribe_price = None

    async def async_added_to_hass(self) -> None:
        # A timezone-aware local reading is calculated at every minute boundary.
        # This also handles DST changes without treating UTC as the local tariff.
        from homeassistant.helpers.event import async_track_time_change
        self._unsubscribe_price = async_track_time_change(
            self.hass, self._handle_tick, second=0
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe_price:
            self._unsubscribe_price()
            self._unsubscribe_price = None

    @callback
    def _handle_tick(self, now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        from homeassistant.util import dt as dt_util
        return price_for_datetime(
            self._entry.options, dt_util.as_local(dt_util.utcnow())
        )

    @property
    def extra_state_attributes(self) -> dict:
        return {"pricing_mode": self._entry.options.get(CONF_PRICING_MODE)}


class DailySupplyChargeSensor(SensorEntity):
    """Optional daily supply-charge detail, hidden from the normal device page."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_entity_registry_visible_default = False
    _attr_icon = "mdi:cash-clock"
    _attr_suggested_display_precision = 4

    def __init__(self, meter, entry, currency) -> None:
        self._entry = entry
        self._attr_unique_id = f"palpulse_{meter}_daily_supply_charge"
        self._attr_device_info = meter_device_info(meter)
        self._attr_name = "PalPulse daily supply charge"
        self._attr_native_unit_of_measurement = f"{currency}/day"

    @property
    def native_value(self) -> float | None:
        # This is an auxiliary sensor. An unfinished tariff should not raise
        # an exception from an entity property every time HA updates it.
        try:
            return daily_supply_dollars(self._entry.options)
        except (ValueError, TypeError, KeyError):
            return None


class CostImportStatusSensor(SensorEntity):
    """Explain why a cost hour is not generated without hiding usage data."""

    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = True
    _attr_icon = "mdi:cash-check"

    def __init__(self, mac, recorder):
        self._recorder = recorder
        self._attr_unique_id = f"palpulse_{mac}_cost_import_status"
        self._attr_device_info = meter_device_info(mac)
        self._attr_name = "PalPulse cost import status"

    @property
    def native_value(self):
        state = self._recorder.status
        if state["last_error"]:
            return "Needs attention"
        if state.get("pending_cost_hours"):
            return "Recovering"
        if state.get("energy_dashboard_cost_link") == "unlinked":
            return "Dashboard cost not linked"
        if state["last_costed_hour"] is None:
            return "Waiting"
        # Use the current verified energy cursor to spot an actual cost lag,
        # not just the age of a single older cost row.
        lag = state.get("energy_to_cost_lag_hours")
        if lag is not None and lag >= 2:
            return "Cost history delayed"
        if state.get("unpriced_hours"):
            return "Active with missing costs"
        import time
        if int(time.time()) - int(state["last_costed_hour"]) > 4 * 3600:
            return "Cost history delayed"
        return "Active"

    @property
    def extra_state_attributes(self):
        return self._recorder.status


class CostMigrationStatusSensor(MigrationSensor):
    _attr_icon = "mdi:cash-clock"

    def __init__(self, meter, manager):
        super().__init__(
            meter, manager, "cost_status", "PalPulse historical cost migration status"
        )

    @property
    def native_value(self):
        if not self._manager.source_id:
            return "Not configured"
        return self._manager.progress["stage"].replace("_", " ").title()

    @property
    def extra_state_attributes(self):
        return {
            **super().extra_state_attributes,
            "cost_source": self._manager.source_id or None,
            "cost_source_mode": self._manager.source_mode,
            "target_cost_statistic": self._manager.target_id,
        }


class CostMigrationProgressSensor(MigrationSensor):
    _attr_icon = "mdi:progress-check"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, meter, manager):
        super().__init__(
            meter, manager, "cost_progress", "PalPulse historical cost migration progress"
        )

    @property
    def native_value(self):
        return self._manager.progress["percent"]
