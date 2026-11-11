"""One-time, non-destructive cleanup of older PalPulse device pages.

Only hide uncustomised PalPulse-owned auxiliary entities. Their unique IDs,
automations, statistics, migration journals and recorded history remain intact.
A durable marker respects users who choose to show an entity again afterwards.
"""

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN


def auxiliary_unique_ids(mac: str) -> frozenset[str]:
    """Known auxiliary entity IDs created by previous PalPulse releases."""
    actions = ("preview", "migrate", "resume", "undo")
    return frozenset(
        [
            *(f"palpulse_{mac}_{action}_historical_migration" for action in actions),
            *(f"palpulse_{mac}_{action}_historical_cost_migration" for action in actions),
            f"palpulse_{mac}_migration_status",
            f"palpulse_{mac}_migration_progress",
            f"palpulse_{mac}_migration_cost_status",
            f"palpulse_{mac}_migration_cost_progress",
            f"palpulse_{mac}_daily_supply_charge",
        ]
    )


async def async_tidy_existing_entities(hass, entry, mac: str) -> None:
    """Hide old uncustomised auxiliary entries just once per config entry."""
    store = Store(hass, 1, f"{DOMAIN}_ui_tidy_{entry.entry_id}")
    if (await store.async_load() or {}).get("applied"):
        return

    registry = er.async_get(hass)
    unique_ids = auxiliary_unique_ids(mac)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.platform == DOMAIN
            and entity.unique_id in unique_ids
            and entity.hidden_by is None
            and entity.name is None  # Respect explicitly renamed entities.
        ):
            registry.async_update_entity(
                entity.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION,
            )

    # Do not repeat on future restarts. User unhide/customisation must stick.
    await store.async_save({"applied": True})
