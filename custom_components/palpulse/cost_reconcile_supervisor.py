"""Single-action, resumable historical cost-repair supervision.

One contract-wide preview and confirmation schedules a guarded Recorder repair
as a Home Assistant task. The existing backup, pinned tariff revision, journal
and per-batch read-after-write checks remain authoritative. Never automatically
discard a journal when Recorder contains even one changed monetary row.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.helpers.storage import Store
from homeassistant.helpers.recorder import get_instance

from .const import DOMAIN
from .cost_reconcile_runtime import (
    _equal, _read, apply_cost_reconciliation, preview_cost_reconciliation,
)
from .panel_settings import revision

_LOGGER = logging.getLogger(__name__)
INCOMPLETE = frozenset(("prepared", "writing", "verified"))


def _store(hass, mac: str):
    return Store(hass, 1, f"{DOMAIN}_cost_reconcile_{mac}")


def _jobs(hass):
    return hass.data.setdefault(f"{DOMAIN}_cost_reconcile_jobs", {})


def _public_saved(saved):
    """Do not send whole plans, input samples or internal backup payloads."""
    if not isinstance(saved, dict) or not saved:
        return None
    plan = saved.get("plan") or {}
    result = {
        "status": saved.get("status", "unknown"),
        "start_date": saved.get("start_date"),
        "end_date": saved.get("end_date"),
        "rows_committed": int(saved.get("rows_committed", 0)),
        "total_rows": len(plan.get("rows", [])),
        "resume_required": saved.get("status") in INCOMPLETE,
        "last_error": saved.get("last_error"),
        "failed_stage": saved.get("failed_stage"),
    }
    return result


async def status(hass, entry) -> dict:
    """Reload durable repair state after browser close or HA restart."""
    job = _jobs(hass).get(entry.entry_id)
    if job and job.get("status") == "running":
        result = dict(job)
        result["resume_required"] = False
        return result
    saved = await _store(hass, entry.data["meter_mac"]).async_load()
    result = _public_saved(saved) or {
        "status": "idle", "resume_required": False,
    }
    if job and job.get("status") == "failed":
        # Never report a failed live retry as merely an old paused journal.
        result["status"] = "failed"
        result["resume_required"] = result.get("resume_required", False)
        result["last_error"] = job.get("last_error")
        result["failed_stage"] = job.get("failed_stage")
    if job and job.get("status") == "complete":
        result["status"] = "complete"
    return result


async def _abort_only_if_untouched(hass, entry, saved: dict) -> bool:
    """Supersede an old failed preview only if Recorder still has its old rows.

    Even zero journalled commits are insufficient proof; a queued Recorder
    batch might have succeeded before Home Assistant went down. Check *all*
    original rows against the live Recorder table before clearing the journal.
    """
    plan = saved.get("plan") or {}
    planned = plan.get("rows") or []
    if saved.get("status") not in INCOMPLETE or not planned:
        return False
    writer = hass.data.get(f"{DOMAIN}_cost", {}).get(entry.entry_id)
    importer = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if writer is None or importer is None:
        return False

    # Ensure a previously queued Recorder write is visible before comparison.
    await get_instance(hass).async_block_till_done()
    async with importer._lock:
        async with writer._lock:
            first = min(int(item["start"]) for item in planned)
            last = max(int(item["start"]) for item in planned) + 3600
            current = await _read(hass, (writer.statistic_id,), first, last)
            by_hour = {
                int(row["start"]): row
                for row in current.get(writer.statistic_id, [])
            }
            for item in planned:
                row = by_hour.get(int(item["start"]))
                if row is None:
                    if item.get("old_sum") is not None:
                        return False
                elif not (
                    _equal(row.get("sum"), item.get("old_sum"))
                    and _equal(row.get("state"), item.get("old_state"))
                ):
                    return False
            saved["status"] = "aborted"
            saved["last_error"] = (
                "An older unfinished repair was safely replaced. Every "
                "Recorder cost row still matched its original saved value; "
                "no earlier write was discarded"
            )
            await _store(hass, entry.data["meter_mac"]).async_save(saved)
            return True


async def launch(
    hass, entry, start_day: str | None, end_day: str | None,
    digest: str | None, *, resume: bool = False,
) -> dict:
    """Return immediately; HA processes the verified Recorder job itself."""
    entry_id = entry.entry_id
    mutex = hass.data.setdefault(f"{DOMAIN}_cost_reconcile_launch_locks", {}).setdefault(
        entry_id, asyncio.Lock(),
    )
    async with mutex:
        jobs = _jobs(hass)
        if jobs.get(entry_id, {}).get("status") == "running":
            return {"started": False, "message": "A cost repair is already running"}
        saved = await _store(hass, entry.data["meter_mac"]).async_load() or {}
        incomplete = saved.get("status") in INCOMPLETE

        if resume:
            if not incomplete:
                return {
                    "started": False,
                    "message": "No interrupted cost repair remains. Preview your desired dates",
                }
            # The original protected plan and its backup are authoritative.
            start_day, end_day, digest = (
                saved.get("start_date"), saved.get("end_date"), saved.get("digest"),
            )
            if saved.get("settings_revision") != revision(entry.options):
                return {
                    "started": False, "message": (
                        "Tariff settings changed since the interrupted repair. "
                        "No Recorder rows were changed by this request"
                    ),
                }
        else:
            if not all(isinstance(x, str) and x for x in (
                start_day, end_day, digest,
            )):
                return {"started": False, "message": "Preview a contract date range first"}
            if incomplete:
                if not await _abort_only_if_untouched(hass, entry, saved):
                    previous = _public_saved(saved)
                    return {
                        "started": False, "resume_required": True,
                        "previous": previous,
                        "message": (
                            "An earlier correction already changed Recorder rows "
                            "or cannot be proven untouched. Choose Resume previous "
                            "repair. It must finish before a new date range begins"
                        ),
                    }
            # The contract and Recorder snapshot must still match the preview.
            fresh = await preview_cost_reconciliation(
                hass, entry, start_day, end_day,
            )
            if fresh["digest"] != digest:
                return {
                    "started": False, "message": (
                        "The contract or recorded data changed since Preview. "
                        "Preview again to avoid applying stale costs"
                    ),
                }
            if not fresh.get("repair_safe", False):
                return {
                    "started": False,
                    "message": (
                        "Some cost-counter breaks cannot be priced from verified "
                        "energy. Restore those hours or review the conflict table "
                        "before correcting Recorder"
                    ),
                }

        jobs[entry_id] = {
            "status": "running", "start_date": start_day,
            "end_date": end_day,
            "rows_committed": int(saved.get("rows_committed", 0)) if incomplete else 0,
            "total_rows": len((saved.get("plan") or {}).get("rows", [])) if incomplete else 0,
            "last_error": None, "stage": "verifying_saved_backup" if incomplete else "creating_backup",
        }

        async def worker() -> None:
            def progress(done: int, total: int) -> None:
                jobs[entry_id]["rows_committed"] = done
                jobs[entry_id]["total_rows"] = total
                jobs[entry_id]["stage"] = "verifying_recorder_batches"

            def on_stage(value: str) -> None:
                jobs[entry_id]["stage"] = value

            try:
                result = await apply_cost_reconciliation(
                    hass, entry, start_day, end_day, digest,
                    progress=progress, stage=on_stage,
                )
                jobs[entry_id].update({
                    "status": "complete", "result": result,
                    "message": result.get("message"),
                })
            except Exception as err:
                _LOGGER.exception("PalPulse full-contract cost repair stopped safely")
                message = f"{type(err).__name__}: {err}"[:500]
                jobs[entry_id].update({
                    "status": "failed", "last_error": message,
                    "failed_stage": jobs[entry_id].get("stage"),
                })
                # Keep the original persisted plan, digest and backup intact.
                # Only record an error for diagnostics across HA restarts.
                try:
                    latest = await _store(hass, entry.data["meter_mac"]).async_load()
                    if isinstance(latest, dict) and latest.get("status") in INCOMPLETE:
                        latest["last_error"] = message
                        latest["failed_stage"] = jobs[entry_id].get("failed_stage")
                        await _store(hass, entry.data["meter_mac"]).async_save(latest)
                except Exception:
                    _LOGGER.exception("Unable to persist cost repair failure reason")

        # HA owns the running task. Closing the browser will not cancel a repair.
        hass.async_create_task(worker())
        return {
            "started": True, "message": (
                "Verified cost correction started. PalPulse will keep progress "
                "and resume information if you close this page"
            ),
            "start_date": start_day, "end_date": end_day,
        }
