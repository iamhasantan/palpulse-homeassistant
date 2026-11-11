"""Verify and optionally repair PalPulse's Home Assistant Energy cost link.

Never replace a user's existing cost/price selection, change other grid
connections or modify Recorder statistics. Reconnection needs a confirmed
Configure action and a real committed PalPulse cost row.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from homeassistant.components.energy import async_get_manager


@dataclass(frozen=True)
class CostLink:
    status: str
    guidance: str


def assess_cost_link(preferences: dict | None, energy_id: str, cost_id: str) -> CostLink:
    """Classify the exact matching grid source without changing preferences."""
    if not preferences:
        return CostLink("not_configured", "Home Assistant's Energy dashboard has no configured sources yet.")

    matching: list[dict] = []
    for source in preferences.get("energy_sources", ()):
        if source.get("type") != "grid":
            continue
        if source.get("stat_energy_from") == energy_id:
            matching.append(source)
        for flow in source.get("flow_from", ()):
            if flow.get("stat_energy_from") == energy_id:
                matching.append(flow)

    if not matching:
        return CostLink("no_source", "Add the PalPulse energy statistic as grid consumption in Energy settings.")
    if len(matching) != 1:
        return CostLink("ambiguous", "More than one grid source uses these readings. Review Energy settings manually.")

    selected = matching[0]
    if selected.get("stat_cost") == cost_id:
        return CostLink("linked", "PalPulse's cost statistic is already selected.")
    if selected.get("stat_cost"):
        return CostLink("other_cost", "Another cost statistic is selected. PalPulse will not replace it.")
    if selected.get("entity_energy_price") is not None or selected.get("number_energy_price") is not None:
        return CostLink("other_price", "An existing electricity price setting is selected. Review it in Energy settings.")
    return CostLink("unlinked", "Grid consumption is configured without a cost statistic. Link PalPulse costs to display them.")


async def async_check_cost_link(hass, energy_id: str, cost_id: str) -> CostLink:
    """Read-only check of the actual HA Energy dashboard settings."""
    manager = await async_get_manager(hass)
    return assess_cost_link(manager.data, energy_id, cost_id)


async def async_link_verified_cost(hass, energy_id: str, cost_id: str, cost_writer) -> CostLink:
    """Explicitly link only an unconfigured, unambiguous PalPulse grid source."""
    if cost_writer is None or cost_writer.statistic_id != cost_id:
        return CostLink("cost_unavailable", "PalPulse custom electricity pricing is not active.")
    hour = cost_writer.status.get("last_costed_hour")
    if hour is None:
        return CostLink("cost_unavailable", "No verified PalPulse cost hour exists yet. Check cost import status.")

    # A saved ledger cursor is not proof that Recorder still has the cost row.
    actual = await cost_writer._read(hour, hour + 3600)
    if len(actual) != 1 or int(actual[0]["start"]) != hour:
        return CostLink("cost_unavailable", "Recorder has no matching committed cost hour. Review the cost ledger first.")

    manager = await async_get_manager(hass)
    check = assess_cost_link(manager.data, energy_id, cost_id)
    if check.status != "unlinked":
        return check

    # Only change the exact matching source. Do not rewrite a default preference
    # object or another meter's grid connection. The manager performs its normal
    # persisted update and notifies the Energy dashboard.
    updated = deepcopy(manager.data["energy_sources"])
    matching = []
    for source in updated:
        if source.get("type") != "grid":
            continue
        if source.get("stat_energy_from") == energy_id:
            matching.append(source)
        matching.extend(
            flow for flow in source.get("flow_from", ())
            if flow.get("stat_energy_from") == energy_id
        )
    if len(matching) != 1:
        return CostLink("ambiguous", "Grid preferences changed while checking. No update was made.")
    target = matching[0]
    if any(target.get(field) is not None for field in (
        "stat_cost", "entity_energy_price", "number_energy_price",
    )):
        return CostLink("other_cost", "An electricity cost or price was set meanwhile. No update was made.")
    target["stat_cost"] = cost_id
    await manager.async_update({"energy_sources": updated})
    verified = assess_cost_link(manager.data, energy_id, cost_id)
    if verified.status != "linked":
        return CostLink("update_failed", "Energy settings did not confirm the cost link.")
    return CostLink("linked", "PalPulse costs are now linked to the Energy dashboard.")
