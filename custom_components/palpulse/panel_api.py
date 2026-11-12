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
from homeassistant.core import callback

from .const import DOMAIN, CONF_ACK_SERVICE
from .checkpoint import parse_checkpoint_event, replay_eligible, derive_replay_service

_LOGGER = logging.getLogger(__name__)
PANEL_WS_TYPE = "palpulse/panel_info"
REPLAY_WS_TYPE = "palpulse/replay_checkpoint"
CHECKPOINT_EVENT = "esphome.palpulse_checkpoint"
REPLAY_COOLDOWN_SECONDS = 300

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


def register_checkpoint_listener(hass, entry) -> None:
    """Read ESPHome's reported NVS/ACK checkpoints, never alter them."""
    key = f"{DOMAIN}_checkpoint"
    store = hass.data.setdefault(key, {})

    @callback
    def on_event(event) -> None:
        snapshot = parse_checkpoint_event(
            event.data, entry.data.get("meter_mac", ""),
        )
        if snapshot is not None:
            store[entry.entry_id] = snapshot

    unsub = hass.bus.async_listen(CHECKPOINT_EVENT, on_event)
    entry.async_on_unload(unsub)


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
        checkpoint = hass.data.get(f"{DOMAIN}_checkpoint", {}).get(entry_id)
        enabled, reason = replay_eligible(checkpoint, int(time.time()))
        action = derive_replay_service(str(entry.options.get(CONF_ACK_SERVICE) or ""))
        if not action or not hass.services.has_service(*action.split(".", 1)):
            enabled = False
            reason = "Matching ESPHome replay action not installed. Check firmware and ACK action setup."
        item["checkpoint"] = checkpoint
        item["replay_service"] = action
        item["replay_available"] = enabled
        item["replay_guidance"] = reason
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


@websocket_api.require_admin
@websocket_api.websocket_command({
    probatio.Required("type"): REPLAY_WS_TYPE,
    probatio.Required("entry_id"): str,
    probatio.Required("confirm"): bool,
})
@websocket_api.async_response
async def ws_replay_checkpoint(hass, connection, msg) -> None:
    """Request ordinary acknowledged replay, never move the NVS/ACK cursor."""
    entry_id = msg["entry_id"]
    entries = [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id == entry_id
    ]
    if len(entries) != 1 or not msg["confirm"]:
        connection.send_result(msg["id"], {
            "accepted": False, "message": "Select a configured meter and confirm replay.",
        })
        return
    entry = entries[0]
    importer = hass.data.get(DOMAIN, {}).get(entry_id)
    manager = hass.data.get(f"{DOMAIN}_migration", {}).get(entry_id)
    cost_manager = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry_id)
    if (
        importer is None
        or importer.health["status"] == "Halted"
        or (manager is not None and manager.busy)
        or (cost_manager is not None and cost_manager.busy)
    ):
        connection.send_result(msg["id"], {
            "accepted": False,
            "message": "Importer unavailable or historical migration active. Replay was not requested.",
        })
        return

    checkpoint = hass.data.get(f"{DOMAIN}_checkpoint", {}).get(entry_id)
    eligible, reason = replay_eligible(checkpoint, int(time.time()))
    action = derive_replay_service(str(entry.options.get(CONF_ACK_SERVICE) or ""))
    if not eligible or not action or not hass.services.has_service(*action.split(".", 1)):
        connection.send_result(msg["id"], {
            "accepted": False,
            "message": reason if not eligible else "Matching ESPHome replay action is unavailable.",
        })
        return
    last = hass.data.setdefault(f"{DOMAIN}_replay_request", {}).get(entry_id, 0)
    if time.monotonic() - last < REPLAY_COOLDOWN_SECONDS:
        connection.send_result(msg["id"], {
            "accepted": False, "message": "A replay was already requested recently. Check the meter status.",
        })
        return
    domain, service = action.split(".", 1)
    try:
        await hass.services.async_call(domain, service, {}, blocking=False)
    except Exception:
        _LOGGER.exception("PalPulse replay action call failed for entry %s", entry_id)
        connection.send_result(msg["id"], {
            "accepted": False, "message": "ESPHome replay action failed. Check ESPHome connectivity and logs.",
        })
        return
    hass.data[f"{DOMAIN}_replay_request"][entry_id] = time.monotonic()
    connection.send_result(msg["id"], {
        "accepted": True,
        "message": (
            "ESPHome replay requested from its saved ACK checkpoint. This is "
            "not confirmation that missing hours were recovered. Refresh the "
            "checkpoint and Recorder history to check actual progress."
        ),
    })


def async_register_panel_api(hass) -> None:
    """The WebSocket commands belong to HA's lifetime, not one meter entry."""
    key = f"{DOMAIN}_panel_api_registered"
    if hass.data.get(key):
        return
    websocket_api.async_register_command(hass, ws_panel_info)
    websocket_api.async_register_command(hass, ws_replay_checkpoint)
    hass.data[key] = True
