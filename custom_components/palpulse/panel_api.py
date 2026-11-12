"""Admin-only read-only data for the PalPulse sidebar control centre.

The panel exposes the existing migration journals and importer diagnostics.
It does not introduce new history writes, tariff edits or migration shortcuts.
All modifications still go through Home Assistant's native guarded flows.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any

import probatio

from homeassistant.components import websocket_api
from homeassistant.components.recorder.statistics import statistics_during_period

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PANEL_WS_TYPE = "palpulse/panel_info"

_PRICING_FIELDS = (
    "pricing_mode", "flat_cents_per_kwh", "peak_cents_per_kwh",
    "shoulder_cents_per_kwh", "offpeak_cents_per_kwh",
    "daily_supply_cents", "weekday_free", "weekend_free",
    "weekday_peak", "weekend_peak", "weekday_shoulder",
    "weekend_shoulder", "daily_free_cap_kwh",
    "free_excess_cents_per_kwh",
)


def public_meter_summary(entry, importer, writer, energy_migration, cost_migration) -> dict:
    """Only explicit, non-secret options and read-only component snapshots."""
    mac = entry.data.get("meter_mac", "")
    options = entry.options
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "meter": mac,
        "energy": importer.health if importer is not None else None,
        "cost": writer.status if writer is not None else None,
        "pricing": {key: options[key] for key in _PRICING_FIELDS if key in options},
        "migration": energy_migration.progress if energy_migration is not None else None,
        "cost_migration": cost_migration.progress if cost_migration is not None else None,
        "statistics": {
            "energy": f"{DOMAIN}:powerpal_{mac}_energy",
            "cost": f"{DOMAIN}:powerpal_{mac}_cost",
        },
    }


async def _recent_statistics(hass, statistic_ids: tuple[str, str]) -> dict:
    """Read only a small recent UTC interval, preserving genuine missing hours."""
    end = int(time.time()) // 3600 * 3600 + 3600
    start = end - 72 * 3600

    def load():
        return statistics_during_period(
            hass,
            datetime.fromtimestamp(start, timezone.utc),
            datetime.fromtimestamp(end, timezone.utc),
            set(statistic_ids),
            "hour",
            {},
            {"sum", "state", "change"},
        )

    series = await hass.async_add_executor_job(load)
    result = {}
    for statistic_id in statistic_ids:
        rows = series.get(statistic_id, [])
        result[statistic_id] = [
            {
                "start": int(row["start"]),
                "sum": row.get("sum"),
                "change": row.get("change"),
                "state": row.get("state"),
            }
            for row in rows
        ]
    return result


@websocket_api.require_admin
@websocket_api.websocket_command({probatio.Required("type"): PANEL_WS_TYPE})
@websocket_api.async_response
async def ws_panel_info(hass, connection, msg) -> None:
    """Return a single admin-only dashboard snapshot for all PalPulse meters."""
    meters: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        entry_id = entry.entry_id
        importer = hass.data.get(DOMAIN, {}).get(entry_id)
        if importer is None:
            continue
        writer = hass.data.get(f"{DOMAIN}_cost", {}).get(entry_id)
        migration = hass.data.get(f"{DOMAIN}_migration", {}).get(entry_id)
        cost_migration = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry_id)
        item = public_meter_summary(entry, importer, writer, migration, cost_migration)
        try:
            item["recent"] = await _recent_statistics(
                hass,
                (item["statistics"]["energy"], item["statistics"]["cost"]),
            )
            item["statistics_error"] = None
        except Exception as err:
            # A Recorder outage should not make diagnostics and backups vanish.
            _LOGGER.warning("PalPulse panel recent statistics unavailable: %s", err)
            item["recent"] = {}
            item["statistics_error"] = type(err).__name__
        meters.append(item)
    connection.send_result(msg["id"], {"meters": meters, "generated_at": int(time.time())})


def async_register_panel_api(hass) -> None:
    """The WebSocket command belongs to HA's lifetime, not one meter entry."""
    key = f"{DOMAIN}_panel_api_registered"
    if hass.data.get(key):
        return
    websocket_api.async_register_command(hass, ws_panel_info)
    hass.data[key] = True
