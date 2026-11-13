"""Admin-only PalPulse sidebar diagnostics and guarded controls.

Meter and cost snapshots stay read-only. Explicit historical gap repairs are
backup-first, journalled and verified through supported Recorder APIs.
Migrations remain in their existing guarded Home Assistant flows.
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
from homeassistant.helpers.recorder import get_instance

from .const import DOMAIN, CONF_ACK_SERVICE
from .checkpoint import parse_checkpoint_event, replay_eligible, derive_replay_service
from .history import normalize_mac
from .panel_settings import revision, visible_settings, normalize_settings

_LOGGER = logging.getLogger(__name__)
PANEL_WS_TYPE = "palpulse/panel_info"
REPLAY_WS_TYPE = "palpulse/replay_checkpoint"
SAVE_WS_TYPE = "palpulse/save_settings"
LINK_COST_WS_TYPE = "palpulse/link_verified_cost"
REPAIR_GAP_WS_TYPE = "palpulse/repair_gap"
COST_PREVIEW_WS_TYPE = "palpulse/cost_reconcile_preview"
COST_APPLY_WS_TYPE = "palpulse/cost_reconcile_apply"
COST_RESUME_WS_TYPE = "palpulse/cost_reconcile_resume"
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
        "settings": visible_settings(options),
        "settings_revision": revision(options),
        "tariff_versions": options.get("tariff_versions", []),
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
    delivered = hass.data.setdefault(f"{DOMAIN}_checkpoint_delivery", {})
    status = delivered.setdefault(entry.entry_id, {
        "received_count": 0, "rejected_count": 0,
        "last_event_at": None, "last_valid_at": None,
        "last_rejection": None,
    })
    meter = normalize_mac(entry.data.get("meter_mac", ""))

    @callback
    def on_event(event) -> None:
        # Only count events addressed to this meter; multiple PalPulse
        # instances share the same Home Assistant event bus.
        try:
            if normalize_mac(str(event.data.get("meter", ""))) != meter:
                return
        except (ValueError, TypeError, AttributeError):
            return
        now = int(time.time())
        status["received_count"] += 1
        status["last_event_at"] = now
        snapshot = parse_checkpoint_event(event.data, meter, now=now)
        if snapshot is None:
            status["rejected_count"] += 1
            status["last_rejection"] = (
                "Meter checkpoint telemetry failed validity checks. "
                "Review timestamp, ACK, pulse counters, NVS state and ESPHome logs."
            )
            return
        status["last_valid_at"] = now
        status["last_rejection"] = None
        store[entry.entry_id] = snapshot
        if snapshot is not None:
            # The firmware reports ACK progress once per minute. Request a
            # normal replay only when its saved meter counter is ahead of its
            # acknowledged cursor, the outgoing queue is empty and every
            # durability/buffer guard passes. This never changes an NVS
            # checkpoint or writes a Recorder statistic.
            if snapshot["ack_timestamp"] >= snapshot["checkpoint_timestamp"]:
                return
            eligible, _ = replay_eligible(snapshot, int(time.time()))
            if not eligible:
                return
            entry_id = entry.entry_id
            importer = hass.data.get(DOMAIN, {}).get(entry_id)
            if importer is None or importer.health["status"] == "Halted":
                return
            manager = hass.data.get(f"{DOMAIN}_migration", {}).get(entry_id)
            cost_manager = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry_id)
            if (
                (manager is not None and manager.busy)
                or (cost_manager is not None and cost_manager.busy)
            ):
                return
            action = derive_replay_service(
                str(entry.options.get(CONF_ACK_SERVICE) or "")
            )
            if not action or not hass.services.has_service(*action.split(".", 1)):
                return
            attempts = hass.data.setdefault(f"{DOMAIN}_replay_request", {})
            now = time.monotonic()
            if now - attempts.get(entry_id, 0) < REPLAY_COOLDOWN_SECONDS:
                return
            attempts[entry_id] = now

            async def request_replay() -> None:
                domain, service = action.split(".", 1)
                try:
                    await hass.services.async_call(
                        domain, service, {}, blocking=False,
                    )
                except Exception:
                    _LOGGER.exception(
                        "Automatic buffered history replay failed for %s",
                        entry_id,
                    )

            hass.async_create_task(request_replay())

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

    series = await get_instance(hass).async_add_executor_job(load)
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
        item["checkpoint_delivery"] = hass.data.get(f"{DOMAIN}_checkpoint_delivery", {}).get(entry_id)
        item["history_delivery"] = hass.data.get(f"{DOMAIN}_history_delivery", {}).get(entry_id)
        try:
            from .cost_reconcile_supervisor import status as cost_repair_status
            item["cost_repair"] = await cost_repair_status(hass, entry)
        except Exception as err:
            _LOGGER.warning("Cost repair status unavailable: %s", err)
            item["cost_repair"] = {"status": "unavailable", "last_error": str(err)[:150]}
        try:
            from .gap_repair_runtime import preview_gaps
            item["energy_gap_previews"] = await preview_gaps(hass, importer)
            item["energy_gap_scan_error"] = None
        except Exception as err:
            _LOGGER.warning("PalPulse historical gap preview unavailable: %s", err)
            item["energy_gap_previews"] = []
            item["energy_gap_scan_error"] = type(err).__name__
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



@websocket_api.require_admin
@websocket_api.websocket_command({
    probatio.Required("type"): SAVE_WS_TYPE,
    probatio.Required("entry_id"): str,
    probatio.Required("expected_revision"): str,
    probatio.Required("changes"): dict,
})
@websocket_api.async_response
async def ws_save_settings(hass, connection, msg) -> None:
    """Persist validated administrator settings without touching Recorder rows."""
    matches = [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id == msg["entry_id"]
    ]
    if len(matches) != 1 or msg["entry_id"] not in hass.data.get(DOMAIN, {}):
        connection.send_result(msg["id"], {
            "saved": False, "message": "This meter is no longer available.",
        })
        return
    entry = matches[0]
    if revision(entry.options) != msg["expected_revision"]:
        connection.send_result(msg["id"], {
            "saved": False,
            "message": "Settings changed elsewhere. Refresh before saving so no edits are lost.",
        })
        return
    manager = hass.data.get(f"{DOMAIN}_migration", {}).get(entry.entry_id)
    cost_manager = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry.entry_id)
    if (manager is not None and manager.busy) or (
        cost_manager is not None and cost_manager.busy
    ):
        connection.send_result(msg["id"], {
            "saved": False,
            "message": "Historical migration is running. Finish it before editing settings.",
        })
        return
    if hass.data.get(f"{DOMAIN}_cost_reconcile_active", {}).get(entry.entry_id):
        connection.send_result(msg["id"], {
            "saved": False, "message": "A cost reconciliation is in progress. Finish it before editing tariffs.",
        })
        return
    previous = dict(entry.options)
    try:
        updated = normalize_settings(
            previous, msg["changes"],
            meter=entry.data.get("meter_mac", ""),
            energy_journal=manager.journal if manager else None,
            cost_journal=cost_manager.journal if cost_manager else None,
            timezone_name=hass.config.time_zone,
        )
    except (ValueError, TypeError) as err:
        connection.send_result(msg["id"], {
            "saved": False, "message": str(err)[:500],
        })
        return
    if updated == previous:
        connection.send_result(msg["id"], {
            "saved": True, "message": "No changes were needed.",
            "settings_revision": revision(previous),
        })
        return

    # Native OptionsFlowWithReload handles its own path. Explicit sidebar
    # saves must reload too, recording a new tariff-profile boundary.
    hass.config_entries.async_update_entry(entry, options=updated)
    try:
        reloaded = await hass.config_entries.async_reload(entry.entry_id)
    except Exception:
        _LOGGER.exception("PalPulse settings reload failed for %s", entry.entry_id)
        reloaded = False
    if not reloaded:
        hass.config_entries.async_update_entry(entry, options=previous)
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("PalPulse settings rollback reload failed")
        connection.send_result(msg["id"], {
            "saved": False,
            "message": "Integration reload failed. Previous settings were restored; inspect PalPulse logs.",
        })
        return
    connection.send_result(msg["id"], {
        "saved": True,
        "message": "Settings saved. The meter reloaded, and future tariff hours use the new profile.",
        "settings_revision": revision(updated),
    })


@websocket_api.require_admin
@websocket_api.websocket_command({
    probatio.Required("type"): LINK_COST_WS_TYPE,
    probatio.Required("entry_id"): str,
    probatio.Required("confirm"): bool,
})
@websocket_api.async_response
async def ws_link_verified_cost(hass, connection, msg) -> None:
    """Link only an unconfigured PalPulse grid source after Recorder verification."""
    if not msg["confirm"]:
        connection.send_result(msg["id"], {
            "linked": False, "message": "Confirm the Energy cost link before changing it.",
        })
        return
    entries = [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id == msg["entry_id"]
    ]
    if len(entries) != 1:
        connection.send_result(msg["id"], {
            "linked": False, "message": "The selected meter was not found.",
        })
        return
    entry = entries[0]
    meter = entry.data.get("meter_mac", "")
    from .dashboard_cost import async_link_verified_cost
    writer = hass.data.get(f"{DOMAIN}_cost", {}).get(entry.entry_id)
    try:
        result = await async_link_verified_cost(
            hass, f"{DOMAIN}:powerpal_{meter}_energy",
            f"{DOMAIN}:powerpal_{meter}_cost", writer,
        )
    except Exception:
        _LOGGER.exception("PalPulse Energy cost link failed")
        connection.send_result(msg["id"], {
            "linked": False, "message": "Energy settings could not be verified. No link was confirmed.",
        })
        return
    connection.send_result(msg["id"], {
        "linked": result.status == "linked",
        "status": result.status, "message": result.guidance,
    })


@websocket_api.require_admin
@websocket_api.websocket_command({
    probatio.Required("type"): REPAIR_GAP_WS_TYPE,
    probatio.Required("entry_id"): str,
    probatio.Required("gap_start"): int,
    probatio.Required("confirm"): bool,
})
@websocket_api.async_response
async def ws_repair_historical_gap(hass, connection, msg) -> None:
    """Repair only one exact skipped UTC hour after a verified HA backup."""
    requested = msg["gap_start"]
    if not msg["confirm"] or requested <= 0 or requested % 3600:
        connection.send_result(msg["id"], {
            "repaired": False,
            "message": "Explicit confirmation and a UTC-aligned hour are required.",
        })
        return
    if not any(
        e.entry_id == msg["entry_id"]
        for e in hass.config_entries.async_entries(DOMAIN)
    ):
        connection.send_result(msg["id"], {
            "repaired": False, "message": "The selected meter is unavailable.",
        })
        return
    try:
        from .gap_repair_runtime import repair_gap
        result = await repair_gap(hass, msg["entry_id"], requested)
    except Exception as err:
        _LOGGER.exception("PalPulse historical energy gap repair refused/failed")
        connection.send_result(msg["id"], {
            "repaired": False,
            "message": f"{type(err).__name__}: {err}"[:500],
        })
        return
    connection.send_result(msg["id"], result)



@websocket_api.require_admin
@websocket_api.websocket_command({
    probatio.Required("type"): COST_PREVIEW_WS_TYPE,
    probatio.Required("entry_id"): str,
    probatio.Required("start_date"): str,
    probatio.Required("end_date"): str,
})
@websocket_api.async_response
async def ws_cost_reconcile_preview(hass, connection, msg) -> None:
    """Preview dated cost corrections for a bounded local billing period."""
    matches = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id == msg["entry_id"]
    ]
    if len(matches) != 1:
        connection.send_result(msg["id"], {
            "ok": False, "message": "Selected PalPulse meter not found.",
        })
        return
    try:
        from .cost_reconcile_runtime import preview_cost_reconciliation
        plan = await preview_cost_reconciliation(
            hass, matches[0], msg["start_date"], msg["end_date"],
        )
        # Keep the original signed row plan on the server. A full contract
        # can contain thousands of hours; the UI only needs summaries.
        connection.send_result(msg["id"], {
            "ok": True, "plan": {k: v for k, v in plan.items() if k != "rows"},
        })
    except Exception as err:
        _LOGGER.warning("PalPulse cost reconciliation preview refused: %s", err)
        connection.send_result(msg["id"], {
            "ok": False, "message": str(err)[:500],
        })


@websocket_api.require_admin
@websocket_api.websocket_command({
    probatio.Required("type"): COST_APPLY_WS_TYPE,
    probatio.Required("entry_id"): str,
    probatio.Required("start_date"): str,
    probatio.Required("end_date"): str,
    probatio.Required("digest"): str,
    probatio.Required("expected_revision"): str,
    probatio.Required("confirm"): bool,
})
@websocket_api.async_response
async def ws_cost_reconcile_apply(hass, connection, msg) -> None:
    """Backup-first, explicitly confirmed, snapshot-matched cost correction."""
    matches = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id == msg["entry_id"]
    ]
    if (
        len(matches) != 1 or not msg["confirm"]
        or revision(matches[0].options) != msg["expected_revision"]
    ):
        connection.send_result(msg["id"], {
            "applied": False,
            "message": "Meter, tariff revision or confirmation changed. Preview again.",
        })
        return
    try:
        from .cost_reconcile_supervisor import launch
        result = await launch(
            hass, matches[0], msg["start_date"], msg["end_date"],
            msg["digest"],
        )
        connection.send_result(msg["id"], result)
    except Exception as err:
        _LOGGER.exception("PalPulse cost reconciliation refused/failed")
        connection.send_result(msg["id"], {
            "applied": False, "message": f"{type(err).__name__}: {err}"[:500],
        })



@websocket_api.require_admin
@websocket_api.websocket_command({
    probatio.Required("type"): COST_RESUME_WS_TYPE,
    probatio.Required("entry_id"): str,
    probatio.Required("confirm"): bool,
})
@websocket_api.async_response
async def ws_cost_reconcile_resume(hass, connection, msg) -> None:
    """Resume an existing journal by its saved plan, never user-edited dates."""
    matches = [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id == msg["entry_id"]
    ]
    if len(matches) != 1 or not msg["confirm"]:
        connection.send_result(msg["id"], {
            "started": False, "message": "Select a meter and confirm resumption",
        })
        return
    try:
        from .cost_reconcile_supervisor import launch
        result = await launch(
            hass, matches[0], None, None, None, resume=True,
        )
        connection.send_result(msg["id"], result)
    except Exception as err:
        _LOGGER.exception("PalPulse cost repair resume refused")
        connection.send_result(msg["id"], {
            "started": False, "message": f"{type(err).__name__}: {err}"[:500],
        })


def async_register_panel_api(hass) -> None:
    """The WebSocket commands belong to HA's lifetime, not one meter entry."""
    key = f"{DOMAIN}_panel_api_registered"
    if hass.data.get(key):
        return
    websocket_api.async_register_command(hass, ws_panel_info)
    websocket_api.async_register_command(hass, ws_replay_checkpoint)
    websocket_api.async_register_command(hass, ws_save_settings)
    websocket_api.async_register_command(hass, ws_link_verified_cost)
    websocket_api.async_register_command(hass, ws_repair_historical_gap)
    websocket_api.async_register_command(hass, ws_cost_reconcile_preview)
    websocket_api.async_register_command(hass, ws_cost_reconcile_apply)
    websocket_api.async_register_command(hass, ws_cost_reconcile_resume)
    hass.data[key] = True
