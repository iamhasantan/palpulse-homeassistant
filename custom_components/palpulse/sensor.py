"""Visible, live progress entities for safe legacy statistics migration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Expose status and progress on the integration's Entities page."""
    manager = hass.data[f"{DOMAIN}_migration"][entry.entry_id]
    meter = entry.data["meter_mac"]
    async_add_entities([
        MigrationStatusSensor(meter, manager),
        MigrationProgressSensor(meter, manager),
    ])


class MigrationSensor(SensorEntity):
    """Entity subscribed to the same manager used by migration buttons."""

    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = True

    def __init__(self, meter: str, manager, suffix: str, name: str) -> None:
        self._manager = manager
        self._attr_unique_id = f"palpulse_{meter}_migration_{suffix}"
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
