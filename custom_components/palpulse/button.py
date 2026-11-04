"""Explicitly opted-in experimental historical-migration controls."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .migration import LegacyMigration


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Expose three DISABLED-BY-DEFAULT controls for one Powerpal meter."""
    importer = hass.data[DOMAIN][entry.entry_id]
    manager = LegacyMigration(hass, entry, importer.statistic_id)
    await manager.async_load()
    hass.data.setdefault(f"{DOMAIN}_migration", {})[entry.entry_id] = manager
    mac = entry.data["meter_mac"]
    async_add_entities([
        MigrationButton(mac, "preview", "Preview historical migration", manager),
        MigrationButton(mac, "migrate", "Import old Powerpal history", manager),
        MigrationButton(mac, "undo", "Undo historical migration", manager),
    ])


class MigrationButton(ButtonEntity):
    """A user-initiated mutation with a durable recovery journal."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False  # Explicit experimental opt-in
    _attr_has_entity_name = False
    _attr_icon = "mdi:history"

    def __init__(self, mac: str, action: str, name: str,
                 manager: LegacyMigration) -> None:
        self._action = action
        self._manager = manager
        self._attr_unique_id = f"palpulse_{mac}_{action}_historical_migration"
        self._attr_name = name
        if action == "undo":
            self._attr_icon = "mdi:backup-restore"
        elif action == "migrate":
            self._attr_icon = "mdi:database-arrow-right"

    @property
    def extra_state_attributes(self) -> dict:
        """Show last action, source identity and recovery backup references."""
        journal = self._manager.journal
        return {
            "experimental": True,
            "source_statistic": self._manager.source_id,
            "target_statistic": self._manager.target_id,
            "migration_status": journal.get("status", "never_run"),
            "pre_migration_backup": journal.get("backup_before_migration"),
            "pre_undo_backup": journal.get("backup_before_undo"),
            "last_result": self._manager.last_message[:1024],
        }

    async def async_press(self) -> None:
        """Run only when a human presses the selected action."""
        if self._action == "preview":
            await self._manager.async_preview()
        elif self._action == "migrate":
            await self._manager.async_migrate()
        else:
            await self._manager.async_undo()
        self.async_write_ha_state()
