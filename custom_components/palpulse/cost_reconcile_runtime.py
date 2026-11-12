"""Guarded, journalled correction of PalPulse's own cumulative cost statistic.

A tariff edit NEVER changes Recorder. Admins must preview a bounded local-date
range, confirm its digest, and allow a verified Recorder-inclusive backup.
The writer is locked while comparing existing rows, importing corrected state
AND sum, and verifying each batch. A persisted plan makes crashes resumable.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
import time

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics, statistics_during_period,
)
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .cost_reconcile_plan import billing_range, build_cost_plan, energy_fingerprint, HOUR
from .tariff_history import (
    CONF_TARIFF_VERSIONS, captured_tariff, validate_versions,
)
from .panel_settings import revision

_LOGGER = logging.getLogger(__name__)
MAX_RECONCILE_ROWS = 350


def _queued_hours_covered(pending: list[dict], plan: dict) -> bool:
    """Only retire pending cost jobs that have independently proven charges."""
    indexed = {int(row["start"]): row for row in plan.get("rows", [])}
    return all(
        int(item["start"]) in indexed
        and indexed[int(item["start"])]["source"] in ("verified", "recorded_sum")
        for item in pending
    )


def _equal(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return math.isclose(float(a), float(b), abs_tol=1e-5, rel_tol=0)
    except (TypeError, ValueError, OverflowError):
        return False


async def _read(hass, statistic_ids: tuple[str, str], start: int, end: int):
    def work():
        return statistics_during_period(
            hass,
            datetime.fromtimestamp(start, timezone.utc),
            datetime.fromtimestamp(end, timezone.utc),
            set(statistic_ids), "hour", {"energy": "kWh"},
            {"state", "sum", "change"},
        )
    # A Recorder read belongs on Recorder's own executor.
    return await get_instance(hass).async_add_executor_job(work)


async def preview_cost_reconciliation(
    hass, entry, start_day: str, end_day: str,
) -> dict:
    """Read-only preview, independently recomputed when the user confirms."""
    from zoneinfo import ZoneInfo

    zone = hass.config.time_zone
    ZoneInfo(zone)
    start, end = billing_range(start_day, end_day, zone)
    # Current incomplete hour must NEVER be billed or tagged as a gap.
    end = min(end, int(time.time()) // HOUR * HOUR)
    if end <= start:
        raise ValueError("No completed hours in the selected date range")
    versions = entry.options.get(CONF_TARIFF_VERSIONS, [])
    latest = validate_versions(versions)[-1:] if versions else []
    if latest and latest[0]["options"] != captured_tariff(entry.options):
        raise ValueError(
            "Your current electricity rates differ from the newest dated "
            "tariff profile. Save the actual plan effective date and rates "
            "in the PalPulse Electricity tab before recalculating history"
        )
    meter = entry.data["meter_mac"]
    cost_id = f"{DOMAIN}:powerpal_{meter}_cost"
    energy_id = f"{DOMAIN}:powerpal_{meter}_energy"
    # Read two weeks before for the existing cumulative monetary anchor.
    rows = await _read(
        hass, (energy_id, cost_id), start - 14 * 86400, end,
    )
    energy = rows.get(energy_id, [])
    costs = rows.get(cost_id, [])
    result = build_cost_plan(
        energy, costs, versions, start, end, zone_name=zone,
    )
    result["start_date"] = start_day
    result["end_date"] = end_day
    return result


async def apply_cost_reconciliation(
    hass, entry, start_day: str, end_day: str, digest: str,
) -> dict:
    """Backup -> journal -> compare -> commit -> verify, with crash resumption."""
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Reconciliation preview digest is required")
    entry_id = entry.entry_id
    importer = hass.data.get(DOMAIN, {}).get(entry_id)
    writer = hass.data.get(f"{DOMAIN}_cost", {}).get(entry_id)
    manager = hass.data.get(f"{DOMAIN}_migration", {}).get(entry_id)
    cost_manager = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry_id)
    if importer is None or writer is None or manager is None:
        raise ValueError("Energy, cost recorder or verified backup manager unavailable")
    if manager.busy or (cost_manager is not None and cost_manager.busy):
        raise ValueError("An existing historical migration is active")
    active = hass.data.setdefault(f"{DOMAIN}_cost_reconcile_active", {})
    if active.get(entry_id):
        raise ValueError("Another cost reconciliation is already running")
    active[entry_id] = True
    store = None
    saved = None
    try:
        store = Store(hass, 1, f"{DOMAIN}_cost_reconcile_{importer.mac}")
        saved = await store.async_load() or {}
        incomplete = saved.get("status") in ("prepared", "writing", "verified")
        if incomplete:
            if (
                saved.get("digest") != digest
                or saved.get("start_date") != start_day
                or saved.get("end_date") != end_day
            ):
                raise ValueError("An earlier repair is incomplete. Resume that same preview first")
            await manager._verify_prior_backup(saved["backup_id"])
            if saved.get("settings_revision") != revision(entry.options):
                raise ValueError("Tariff settings changed during an interrupted repair. Review journal")
            plan = saved["plan"]
            if not plan.get("baseline_verified", True):
                raise ValueError("Saved cost correction has an unverified monetary baseline")
        else:
            plan = await preview_cost_reconciliation(hass, entry, start_day, end_day)
            if plan["digest"] != digest:
                raise ValueError("Recorder or tariff changed. Preview again before applying")
            if not plan.get("baseline_verified", True):
                raise ValueError(
                    "The earlier monetary baseline contains invalid Recorder rows. "
                    "Preview is read-only until those records are reviewed; "
                    "no historical cost rows were modified"
                )
            if not any(not _equal(row["old_sum"], row["new_sum"])
                       for row in plan["rows"]):
                return {
                    "applied": False, "message": "All verifiable costs already match.",
                    "changed_hours": 0,
                }
            # A reconciliation must reach the current cost checkpoint so the
            # live writer's durable cumulative baseline remains synchronized.
            if writer._last_hour is not None and (
                not plan["rows"] or writer._last_hour > plan["rows"][-1]["start"]
            ):
                raise ValueError("Cost checkpoint lies outside preview. Include the latest day")
            if writer._pending and not _queued_hours_covered(writer._pending, plan):
                raise ValueError(
                    "Queued cost jobs include hours this preview cannot independently price. "
                    "Recover their missing energy or review the conflict before proceeding"
                )
            # No Recorder mutation until backup has completed and been verified.
            backup_id = await manager._backup("before cost reconciliation")
            saved = {
                "status": "prepared", "digest": digest,
                "start_date": start_day, "end_date": end_day,
                "backup_id": backup_id, "plan": plan, "rows_committed": 0,
                "settings_revision": revision(entry.options),
            }
            await store.async_save(saved)

        async with importer._lock:
            async with writer._lock:
                if manager.busy or (cost_manager and cost_manager.busy):
                    raise ValueError("A migration started during reconciliation")
                if writer._pending and not _queued_hours_covered(writer._pending, plan):
                    raise ValueError("An unverified pending cost hour cannot be retired safely")
                if not incomplete:
                    # Detect a live measurement, plan edit or Recorder change
                    # while backup was running; do not write on stale evidence.
                    current = await preview_cost_reconciliation(
                        hass, entry, start_day, end_day,
                    )
                    if current["digest"] != digest:
                        raise ValueError("Recorder changed during backup. Preview again")
                else:
                    # Saved profile or session may have changed while interrupted.
                    # Require the still-selected contract to yield an identical
                    # preview when no rows have been committed yet.
                    if saved.get("rows_committed", 0) == 0:
                        current = await preview_cost_reconciliation(
                            hass, entry, start_day, end_day,
                        )
                        if current["digest"] != digest:
                            raise ValueError("Tariff or Recorder changed. Review journal")
                cost_id = writer.statistic_id
                energy_id = importer.statistic_id
                if not plan["rows"] or len(plan["rows"]) > MAX_RECONCILE_ROWS:
                    raise ValueError("No bounded historical cost rows to reconcile")
                max_written = int(plan["rows"][-1]["start"])
                if writer._last_hour is not None and writer._last_hour > max_written:
                    raise ValueError("Live cost checkpoint advanced past repair range")
                rows_before = await _read(
                    hass, (energy_id, cost_id),
                    int(plan["start_utc"]) - HOUR, int(plan["end_utc"]),
                )
                if energy_fingerprint(
                    rows_before.get(energy_id, []),
                    int(plan["start_utc"]), int(plan["end_utc"]),
                ) != plan.get("energy_digest"):
                    raise ValueError(
                        "Meter energy changed since this cost plan was prepared. "
                        "Do not resume a stale correction; review the saved journal"
                    )
                current_costs = {
                    int(row["start"]): row for row in rows_before.get(cost_id, [])
                }
                # Entire planned row set is checked before the first write.
                for item in plan["rows"]:
                    current_row = current_costs.get(int(item["start"]))
                    if current_row is None:
                        if item["old_sum"] is not None:
                            raise ValueError("Previously saved cost row vanished. No changes made")
                        continue
                    old_match = (
                        _equal(current_row.get("sum"), item["old_sum"])
                        and _equal(current_row.get("state"), item["old_state"])
                    )
                    new_match = (
                        _equal(current_row.get("sum"), item["new_sum"])
                        and _equal(current_row.get("state"), item["new_state"])
                    )
                    if not (old_match or new_match):
                        raise ValueError(
                            "Unexpected Recorder cost change at UTC "
                            f"{item['start']}. Refusing an overwrite"
                        )

                metadata = {
                    "statistic_id": cost_id, "source": DOMAIN,
                    "name": "PalPulse electricity cost",
                    "unit_of_measurement": None, "unit_class": None,
                    "mean_type": StatisticMeanType.NONE, "has_sum": True,
                }
                saved["status"] = "writing"
                await store.async_save(saved)
                changed = 0
                for offset in range(0, len(plan["rows"]), 12):
                    chunk = plan["rows"][offset:offset + 12]
                    pending = []
                    for item in chunk:
                        stamp = int(item["start"])
                        current_row = current_costs.get(stamp)
                        if current_row is None or not (
                            _equal(current_row.get("sum"), item["new_sum"])
                            and _equal(current_row.get("state"), item["new_state"])
                        ):
                            pending.append({
                                "start": datetime.fromtimestamp(stamp, timezone.utc),
                                "state": float(item["new_state"]),
                                "sum": float(item["new_sum"]),
                            })
                    if pending:
                        async_add_external_statistics(hass, metadata, pending)
                        await asyncio.wait_for(
                            get_instance(hass).async_block_till_done(),
                            timeout=180,
                        )
                        changed += len(pending)
                    checked = await _read(
                        hass, (energy_id, cost_id),
                        int(chunk[0]["start"]),
                        int(chunk[-1]["start"]) + HOUR,
                    )
                    for item in chunk:
                        new_by_start = {
                            int(row["start"]): row
                            for row in checked.get(cost_id, [])
                        }
                        row = new_by_start.get(int(item["start"]))
                        if row is None or not (
                            _equal(row.get("sum"), item["new_sum"])
                            and _equal(row.get("state"), item["new_state"])
                        ):
                            raise ValueError(
                                "Recorder failed to confirm a cost repair batch. "
                                "Resume the persisted journal; do not reset it"
                            )
                    saved["rows_committed"] = offset + len(chunk)
                    await store.async_save(saved)

                # Keep the live writer's durable checkpoint aligned with the
                # new cost statistic. A restart must not restore the old sum.
                writer._last_hour = max_written
                writer._sum = float(plan["last_sum"])
                if plan["last_supply_date"]:
                    writer._last_supply_date = plan["last_supply_date"]
                writer._free_used = dict(plan["free_used"])
                writer._free_uncertain_dates.update(plan["free_uncertain_dates"])
                # Every retired pending job now has a verified Recorder cost
                # row in the confirmed batch. Never discard a job whose
                # original hour was missing or could not be priced.
                if writer._pending:
                    writer._pending = [
                        job for job in writer._pending
                        if int(job["start"]) not in {
                            int(item["start"]) for item in plan["rows"]
                            if item["source"] in ("verified", "recorded_sum")
                        }
                    ]
                writer._unpriced_hours.difference_update(
                    int(item["start"]) for item in plan["rows"]
                    if item["source"] in ("verified", "recorded_sum")
                )
                writer._error = None
                await writer._store.async_save(writer._snapshot())
                saved["status"] = "complete"
                saved["rows_committed"] = len(plan["rows"])
                await store.async_save(saved)
                _LOGGER.warning(
                    "PalPulse reconciled %d cost rows for %s; backup %s",
                    changed, importer.mac, saved["backup_id"],
                )
                return {
                    "applied": True, "changed_hours": changed,
                    "backup_id": saved["backup_id"],
                    "message": (
                        "Cost rows verified and live checkpoint updated. "
                        "Review the Home Assistant Energy day totals; any "
                        "unverifiable hours remain explicitly flagged"
                    ),
                }
    except Exception:
        # If no writes have even been queued, an unchanged prepared journal
        # must not prevent a new preview after Recorder advanced during backup.
        # Once writing starts we always retain the journal for safe resumption.
        if (
            store is not None and isinstance(saved, dict)
            and saved.get("status") == "prepared"
            and not saved.get("rows_committed")
        ):
            saved["status"] = "aborted"
            try:
                await store.async_save(saved)
            except Exception:
                _LOGGER.exception("Unable to checkpoint aborted PalPulse cost repair")
        raise
    finally:
        active.pop(entry_id, None)
