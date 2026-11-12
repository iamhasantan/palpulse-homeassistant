"""Backup-first, journalled repair of an isolated skipped PalPulse UTC hour.

Invoked only after an explicit admin confirmation. Does not approximate
multi-hour outages, overwrite existing rows, infer tariff costs or move
firmware ACK cursors. Requires a completed Home Assistant Recorder backup.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import time

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .historical_gap import plan_recoverable_gaps

HOUR = 3600
WINDOW = 7 * 24 * HOUR


def _matches(a, b):
    return math.isclose(float(a), float(b), abs_tol=1e-5, rel_tol=0)


async def _read_energy(hass, statistic_id: str) -> list[dict]:
    end = int(time.time()) // HOUR * HOUR + HOUR
    start = end - WINDOW

    def read():
        return statistics_during_period(
            hass,
            datetime.fromtimestamp(start, timezone.utc),
            datetime.fromtimestamp(end, timezone.utc),
            {statistic_id}, "hour", {"energy": "kWh"}, {"state", "sum"},
        ).get(statistic_id, [])

    return await get_instance(hass).async_add_executor_job(read)


async def preview_gaps(hass, importer) -> list[dict]:
    """The report is read-only; candidates are revalidated before every write."""
    return [
        plan.as_dict() for plan in plan_recoverable_gaps(
            await _read_energy(hass, importer.statistic_id),
        )
    ]


async def repair_gap(hass, entry_id: str, gap_start: int) -> dict:
    """Apply one exact single-hour repair, or refuse it without modifying data.

    Order of writes: verified backup, durable journal, adjust all *following*
    cumulative sums, confirm adjustment, insert missing row, confirm row.
    If interrupted, a subsequent explicitly confirmed call can resume using
    the verified backup and saved journal without applying the offset twice.
    """
    importer = hass.data.get(DOMAIN, {}).get(entry_id)
    manager = hass.data.get(f"{DOMAIN}_migration", {}).get(entry_id)
    cost_manager = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry_id)
    if importer is None or manager is None:
        raise ValueError("PalPulse meter or backup manager is unavailable")
    if manager.busy or (cost_manager is not None and cost_manager.busy):
        raise ValueError("A historical migration is active. Finish it before gap repair")
    if importer.health.get("status") == "Halted":
        raise ValueError("Meter is halted. Resolve the counter issue before repairing history")

    store = Store(hass, 1, f"{DOMAIN}_hour_gap_repair_{importer.mac}")
    existing = await store.async_load() or {}
    if existing.get("status") not in (None, "complete"):
        if int(existing.get("gap_start", -1)) != gap_start:
            raise ValueError("Another gap repair is incomplete. Resume that repair first")
        await manager._verify_prior_backup(existing.get("backup_id"))
        backup_id = existing["backup_id"]
    else:
        candidates = await preview_gaps(hass, importer)
        if not any(int(x["gap_start"]) == gap_start for x in candidates):
            raise ValueError("This hour is not currently provable from adjacent Recorder rows")
        # Never hold the live importer lock during a potentially lengthy backup.
        backup_id = await manager._backup("before exact hourly energy gap repair")

    async with importer._lock:
        if manager.busy or (cost_manager is not None and cost_manager.busy):
            raise ValueError("A migration started while preparing the repair")
        rows = await _read_energy(hass, importer.statistic_id)
        by_hour = {int(row["start"]): row for row in rows}
        if existing.get("status") not in (None, "complete"):
            plan = existing["plan"]
        else:
            candidates = [
                item.as_dict() for item in plan_recoverable_gaps(rows)
                if item.gap_start == gap_start
            ]
            if len(candidates) != 1:
                raise ValueError("Recorder changed during backup. No repair started")
            plan = candidates[0]
            existing = {
                "status": "prepared",
                "gap_start": gap_start,
                "backup_id": backup_id,
                "plan": plan,
            }
            await store.async_save(existing)

        prev = by_hour.get(int(plan["prior_start"]))
        following = by_hour.get(int(plan["following_start"]))
        if prev is None or following is None:
            raise ValueError("Repair anchor is no longer present. Leave journal for review")
        if not _matches(prev["sum"], plan["prior_sum"]):
            raise ValueError("Prior Recorder sum changed. Manual review required")
        if not _matches(following["state"], plan["following_state"]):
            raise ValueError("Following physical meter state changed. Manual review required")

        recorder = get_instance(hass)
        before = _matches(following["sum"], plan["following_sum"])
        after = _matches(following["sum"], plan["adjusted_following_sum"])
        if not (before or after):
            raise ValueError("Following Recorder sum does not match either safe checkpoint")

        gap_row = by_hour.get(gap_start)
        if gap_row is not None and not (
            _matches(gap_row["state"], plan["inferred_gap_state"])
            and _matches(gap_row["sum"], plan["inferred_gap_sum"])
        ):
            raise ValueError("Missing hour is now occupied by different data. Refusing overwrite")

        if before:
            if gap_row is not None:
                raise ValueError("Gap exists but following sums were not adjusted. Review required")
            recorder.async_adjust_statistics(
                importer.statistic_id,
                datetime.fromtimestamp(plan["following_start"], timezone.utc),
                float(plan["missing_kwh"]), "kWh",
            )
            await recorder.async_block_till_done()
            rows = await _read_energy(hass, importer.statistic_id)
            updated = {
                int(row["start"]): row for row in rows
            }.get(int(plan["following_start"]))
            if updated is None or not _matches(
                updated["sum"], plan["adjusted_following_sum"]
            ):
                raise ValueError(
                    "Recorder did not verify the sum adjustment. "
                    "Do not retry blindly; inspect the repair journal"
                )
        existing["status"] = "adjusted"
        await store.async_save(existing)

        if gap_row is None:
            # _verified_import refuses to replace an existing different row.
            await importer._verified_import([{
                "start": datetime.fromtimestamp(gap_start, timezone.utc),
                "state": float(plan["inferred_gap_state"]),
                "sum": float(plan["inferred_gap_sum"]),
            }])
        confirmed = await importer._recorder_hour(gap_start)
        if len(confirmed) != 1 or not (
            _matches(confirmed[0]["state"], plan["inferred_gap_state"])
            and _matches(confirmed[0]["sum"], plan["inferred_gap_sum"])
        ):
            raise ValueError("Recorder did not confirm the restored hour")

        # Future live energy imports use the last Recorder row as their sum
        # anchor. The pulse baseline stays unchanged; only its accounted sum
        # offset is now higher by the exact restored kWh amount.
        if gap_start not in importer._repaired_gap_hours:
            importer._sum_offset += float(plan["missing_kwh"])
            importer._gap_count = max(0, importer._gap_count - 1)
            importer._repaired_gap_hours.add(gap_start)
            importer._status_message = (
                f"Restored exact historical UTC hour {gap_start} from "
                "Recorder counter evidence; tariff cost unchanged"
            )
            await importer._store.async_save(importer._snapshot())
        existing["status"] = "complete"
        await store.async_save(existing)
        importer._notify_health()
        return {
            "repaired": True,
            "gap_start": gap_start,
            "kwh": plan["missing_kwh"],
            "backup_id": backup_id,
            "message": (
                "Restored the verified energy hour and shifted subsequent "
                "cumulative sums. Cost history is unchanged; its own repair "
                "needs separate tariff and Recorder verification."
            ),
        }
