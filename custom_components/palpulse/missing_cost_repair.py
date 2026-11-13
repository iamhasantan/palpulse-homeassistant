"""Backup-first, crash-resumable repair for missing PalPulse cost rows.

Only missing charges are independently calculated from verified Recorder energy
and dated tariffs. Recorded charges remain unchanged; cumulative sums shift.
Never manipulate Recorder until after a verified full backup and frozen journal.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
import time

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .cost_reconcile_runtime import _read
from .cost_reconcile_plan import energy_fingerprint
from .missing_cost_plan import build_missing_cost_plan, HOUR
from .panel_settings import revision
from .tariff_history import CONF_TARIFF_VERSIONS

_LOGGER = logging.getLogger(__name__)
LOOKBACK = 10 * 86400
BATCH = 12


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return math.isfinite(float(a)) and math.isfinite(float(b)) and math.isclose(
            float(a), float(b), rel_tol=0, abs_tol=1e-5
        )
    except (ValueError, TypeError, OverflowError):
        return False


def _store(hass, mac):
    return Store(hass, 1, f"{DOMAIN}_missing_cost_repair_{mac}")


def _legacy_store(hass, mac):
    return Store(hass, 1, f"{DOMAIN}_cost_reconcile_{mac}")


async def _inputs(hass, importer, writer, entry):
    if writer._last_hour is None:
        raise ValueError("No durable cost checkpoint exists yet")
    end = int(writer._last_hour)
    data = await _read(
        hass, (importer.statistic_id, writer.statistic_id),
        end - LOOKBACK, end + HOUR,
    )
    energy = data.get(importer.statistic_id, [])
    cost = data.get(writer.statistic_id, [])
    plan = build_missing_cost_plan(
        energy, cost,
        entry.options.get(CONF_TARIFF_VERSIONS, []),
        hass.config.time_zone,
        last_costed_hour=end,
    )
    return plan, energy, cost


async def preview(hass, entry) -> dict:
    importer = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    writer = hass.data.get(f"{DOMAIN}_cost", {}).get(entry.entry_id)
    if importer is None or writer is None:
        return {"status": "unavailable", "reason": "Cost recording is not enabled"}
    saved = await _store(hass, importer.mac).async_load() or {}
    legacy = await _legacy_store(hass, importer.mac).async_load() or {}
    if saved.get("status") in ("prepared", "writing"):
        plan = saved["plan"]
        return {
            "status": "resume_required", "missing_hours": plan["missing_hours"],
            "missing_charges": plan["missing_charges"],
            "rows": len(plan["rows"]),
            "reason": "An interrupted, backup-protected missing-cost repair must resume",
        }
    try:
        plan, _, _ = await _inputs(hass, importer, writer, entry)
    except (ValueError, KeyError, TypeError) as err:
        return {"status": "blocked", "reason": str(err)}
    if plan.get("status") != "ready":
        return {"status": "complete", "missing_hours": 0}
    old = legacy.get("status") in ("prepared", "writing", "verified")
    return {
        "status": "ready", "digest": plan["digest"],
        "missing_hours": plan["missing_hours"],
        "missing_charges": plan["missing_charges"],
        "rows": len(plan["rows"]),
        "preserved_existing_hours": plan["preserved_existing_hours"],
        "legacy_interrupted": old,
        "reason": (
            "A separate incomplete historical journal exists. Explicit repair "
            "will supersede its status after a verified backup, retaining its "
            "original plan and prior backup" if old
            else "All missing charges have independent tariff and kWh evidence"
        ),
    }


async def apply(hass, entry, *, confirm: bool, digest: str | None = None,
                automatic=False) -> dict:
    """Reprice proven missing hours, preserving every other original charge.

    An interrupted journal is only resumed if every Recorder cost row still
    equals its frozen old or new snapshot. No double-shift is possible.
    """
    if not confirm:
        raise ValueError("Explicit approval is required")
    importer = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    writer = hass.data.get(f"{DOMAIN}_cost", {}).get(entry.entry_id)
    manager = hass.data.get(f"{DOMAIN}_migration", {}).get(entry.entry_id)
    cost_manager = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry.entry_id)
    if importer is None or writer is None or manager is None:
        raise ValueError("Recorder, cost writer or verified backup manager unavailable")
    if manager.busy or (cost_manager is not None and cost_manager.busy):
        raise ValueError("A historical migration is running")
    active = hass.data.setdefault(f"{DOMAIN}_cost_reconcile_active", {})
    if active.get(entry.entry_id):
        raise ValueError("Another cost correction is active")
    # Avoid overlapping cost correction with live imports and migrations.
    active[entry.entry_id] = True
    try:
        async with importer._lock:
            async with writer._lock:
                store = _store(hass, importer.mac)
                legacy_store = _legacy_store(hass, importer.mac)
                saved = await store.async_load() or {}
                legacy = await legacy_store.async_load() or {}
                interrupted = legacy.get("status") in ("prepared", "writing", "verified")
                if automatic and interrupted:
                    raise ValueError("Historical repair journal remains active. Manual approval is required")
                if saved.get("status") in ("prepared", "writing"):
                    if saved.get("settings_revision") != revision(entry.options):
                        raise ValueError("Tariff versions changed since the saved repair")
                    if int(saved["end_utc"]) != int(writer._last_hour) + HOUR:
                        raise ValueError("Live cost checkpoint moved since interrupted repair")
                    await manager._verify_prior_backup(saved["backup_id"])
                    plan = saved["plan"]
                else:
                    plan, energy, costs = await _inputs(hass, importer, writer, entry)
                    if plan.get("status") != "ready":
                        return {"applied": False, "message": "No independently priceable missing costs"}
                    if digest is not None and digest != plan["digest"]:
                        raise ValueError("Recorder changed since preview. Assess again")
                    if automatic and plan["missing_hours"] > 24:
                        raise ValueError("Large historical repair requires explicit approval")
                    # If a durable minute job is already queued for an old
                    # missing hour, retire it ONLY after independently pricing
                    # and verifying exactly that row. Newer jobs remain queued.
                    source = {int(row["start"]): row["source"] for row in plan["rows"]}
                    if any(
                        int(job["start"]) < int(plan["end_utc"])
                        and source.get(int(job["start"])) != "meter_tariff"
                        for job in writer._pending
                    ):
                        raise ValueError("Queued cost jobs overlap unverified repair rows")
                    # Back up before persisting the plan or touching statistics.
                    backup_id = await manager._backup("before filling verified missing PalPulse costs")
                    await manager._verify_prior_backup(backup_id)
                    check, _, _ = await _inputs(hass, importer, writer, entry)
                    if check.get("digest") != plan["digest"]:
                        raise ValueError("Recorder or tariff changed during backup. No costs modified")
                    saved = {
                        "status": "prepared", "backup_id": backup_id,
                        "plan": plan, "rows_committed": 0,
                        "settings_revision": revision(entry.options),
                        "energy_digest": energy_fingerprint(
                            energy, int(plan["start_utc"]), int(plan["end_utc"])
                        ),
                        "end_utc": plan["end_utc"],
                        "legacy_superseded_on_complete": bool(interrupted),
                    }
                    await store.async_save(saved)
                # Recheck *all* original and newly written monetary rows before
                # any resuming write, not just the next twelve rows.
                plan = saved["plan"]
                current = await _read(
                    hass, (importer.statistic_id, writer.statistic_id),
                    int(plan["start_utc"]) - HOUR, int(plan["end_utc"]),
                )
                energy = current.get(importer.statistic_id, [])
                if energy_fingerprint(
                    energy, int(plan["start_utc"]), int(plan["end_utc"]),
                ) != saved.get("energy_digest"):
                    raise ValueError("Meter energy changed since the saved correction")
                existing = {
                    int(row["start"]): row
                    for row in current.get(writer.statistic_id, [])
                }
                anchor = existing.get(int(plan["anchor_utc"]))
                if anchor is None or not _same(anchor.get("sum"), plan["anchor_sum"]):
                    raise ValueError("Saved monetary anchor changed; no writes made")
                for item in plan["rows"]:
                    stamp = int(item["start"])
                    row = existing.get(stamp)
                    if row is None:
                        if item["old_sum"] is not None:
                            raise ValueError(f"Original cost row vanished at UTC {stamp}")
                    else:
                        old_match = _same(row.get("sum"), item["old_sum"]) and _same(row.get("state"), item["old_state"])
                        new_match = _same(row.get("sum"), item["new_sum"]) and _same(row.get("state"), item["new_state"])
                        if not (old_match or new_match):
                            raise ValueError(f"Unreviewed cost change at UTC {stamp}; correction refused")
                # If the first process crashed after writing and before its
                # journal commit, exact target comparisons make retry idempotent.
                metadata = {
                    "statistic_id": writer.statistic_id, "source": DOMAIN,
                    "name": "PalPulse electricity cost",
                    "unit_of_measurement": None, "unit_class": None,
                    "mean_type": StatisticMeanType.NONE, "has_sum": True,
                }
                saved["status"] = "writing"
                await store.async_save(saved)
                for offset in range(0, len(plan["rows"]), BATCH):
                    chunk = plan["rows"][offset:offset+BATCH]
                    writes = [
                        {
                            "start": datetime.fromtimestamp(item["start"], timezone.utc),
                            "state": float(item["new_state"]), "sum": float(item["new_sum"]),
                        }
                        for item in chunk
                        if item["start"] not in existing or not (
                            _same(existing[item["start"]].get("sum"), item["new_sum"])
                            and _same(existing[item["start"]].get("state"), item["new_state"])
                        )
                    ]
                    if writes:
                        async_add_external_statistics(hass, metadata, writes)
                        await asyncio.wait_for(
                            get_instance(hass).async_block_till_done(), timeout=180,
                        )
                    checked = await _read(
                        hass, (importer.statistic_id, writer.statistic_id),
                        int(chunk[0]["start"]), int(chunk[-1]["start"]) + HOUR,
                    )
                    verified = {
                        int(row["start"]): row
                        for row in checked.get(writer.statistic_id, [])
                    }
                    if any(
                        item["start"] not in verified or
                        not _same(verified[item["start"]].get("sum"), item["new_sum"]) or
                        not _same(verified[item["start"]].get("state"), item["new_state"])
                        for item in chunk
                    ):
                        raise ValueError("Recorder did not confirm missing cost batch. Saved journal retained")
                    saved["rows_committed"] = offset + len(chunk)
                    await store.async_save(saved)
                writer._sum = float(plan["last_sum"])
                repaired = {
                    int(item["start"]) for item in plan["rows"]
                    if item["source"] == "meter_tariff"
                }
                writer._unpriced_hours.difference_update(repaired)
                writer._pending = [
                    job for job in writer._pending
                    if int(job["start"]) not in repaired
                ]
                writer._error = None
                await writer._store.async_save(writer._snapshot())
                if saved.get("legacy_superseded_on_complete"):
                    legacy["status"] = "superseded"
                    legacy["superseded_by"] = "verified_missing_cost_repair"
                    legacy["superseded_backup"] = saved["backup_id"]
                    await legacy_store.async_save(legacy)
                saved["status"] = "complete"
                await store.async_save(saved)
                _LOGGER.warning(
                    "PalPulse restored %d missing cost hours with verified backup %s",
                    plan["missing_hours"], saved["backup_id"],
                )
                return {
                    "applied": True, "missing_hours": plan["missing_hours"],
                    "recalculated_rows": len(plan["rows"]),
                    "backup_id": saved["backup_id"],
                    "message": "Missing costs restored, original charges preserved and cumulative totals verified",
                }
    finally:
        active.pop(entry.entry_id, None)
