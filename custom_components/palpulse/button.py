"""Explicitly opted-in experimental historical-migration controls."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .device import meter_device_info
from .migration import LegacyMigration


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Show usage controls and an independent, guarded cost-import group."""
    importer = hass.data[DOMAIN][entry.entry_id]
    manager = hass.data[f"{DOMAIN}_migration"][entry.entry_id]
    mac = entry.data["meter_mac"]
    buttons = [
        MigrationButton(mac, "preview", "Preview historical migration", manager),
        MigrationButton(mac, "migrate", "Import old Powerpal history", manager),
        MigrationButton(mac, "resume", "Resume interrupted historical migration", manager),
        MigrationButton(mac, "undo", "Undo historical migration", manager),
    ]
    cost_manager = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry.entry_id)
    if cost_manager:
        buttons.extend([
            CostMigrationButton(mac, "preview", "Preview old daily electricity costs", cost_manager),
            CostMigrationButton(mac, "migrate", "Import old daily electricity costs", cost_manager),
            CostMigrationButton(mac, "resume", "Resume interrupted daily cost import", cost_manager),
            CostMigrationButton(mac, "undo", "Undo historical daily cost import", cost_manager),
        ])
    async_add_entities(buttons)


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
        self._attr_device_info = meter_device_info(mac)
        if action == "undo":
            self._attr_icon = "mdi:backup-restore"
        elif action == "migrate":
            self._attr_icon = "mdi:database-arrow-right"
        elif action == "resume":
            self._attr_icon = "mdi:play-circle-outline"

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
            "progress": self._manager.progress,
        }

    async def async_press(self) -> None:
        """Run only when a human presses the selected action."""
        if self._action == "preview":
            await self._manager.async_preview()
        elif self._action == "migrate":
            await self._manager.async_migrate()
        elif self._action == "resume":
            await self._manager.async_resume()
        else:
            await self._manager.async_undo()
        self.async_write_ha_state()


class CostMigrationButton(MigrationButton):
    """Separate cost migration buttons. Never collide with energy controls."""

    def __init__(self, mac: str, action: str, name: str, manager) -> None:
        super().__init__(mac, action, name, manager)
        self._attr_unique_id = f"palpulse_{mac}_{action}_historical_cost_migration"
        # Cost controls remain separately preview/backup gated; show them even
        # before a cost source has been configured, so users can find the feature.
        self._attr_entity_registry_enabled_default = True
        self._attr_icon = "mdi:cash-sync"
