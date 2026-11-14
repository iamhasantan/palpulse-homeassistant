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
from .panel_settings import revision, pricing_revision

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
        "day_recalculation": bool(saved.get("day_recalculation")),
        "legacy_recovery": (
            "verified" if (saved.get("private_beta_recovery") or {}).get("outcome")
            == "verified_same_pricing" else None
        ),
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


async def assess_changed_energy(hass, entry) -> dict:
    """Read-only recovery decision for a saved interrupted cost correction."""
    from .cost_reconcile_runtime import _read
    from .cost_reconcile_drift import audit_changed_energy
    from .tariff_history import CONF_TARIFF_VERSIONS
    saved = await _store(hass, entry.data["meter_mac"]).async_load() or {}
    if saved.get("status") not in INCOMPLETE:
        return {
            "status": "no_repair", "can_replan": False,
            "reason": "There is no interrupted repair to inspect",
        }
    plan = saved.get("plan") or {}
    if saved.get("day_recalculation"):
        return {
            "status": "day_energy_changed", "can_replan": False,
            "reason": (
                "This day-level repair requires the same fully verified energy "
                "as its saved original preview. Resume the journal if evidence "
                "is unchanged; if Recorder energy changed, review and resolve "
                "the interrupted repair before starting a new daily preview"
            ),
        }
    if plan.get("repair_safe") is False:
        return {
            "status": "unsafe_original", "can_replan": False,
            "reason": "The original saved repair was explicitly marked unsafe. "
                      "Recover missing source energy before considering repricing",
        }
    importer = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    writer = hass.data.get(f"{DOMAIN}_cost", {}).get(entry.entry_id)
    if importer is None or writer is None:
        raise ValueError("Live meter and cost Recorder must be available")
    if (
        saved.get("pricing_revision") != pricing_revision(
            entry.options, hass.config.time_zone,
        ) if saved.get("pricing_revision") is not None
        else saved.get("settings_revision") != revision(entry.options)
    ):
        return {
            "status": "tariff_changed", "can_replan": False,
            "reason": "The saved tariff was edited since this repair started. "
                      "Historical prices require a separate manual review",
        }
    rows = await _read(
        hass, (importer.statistic_id,),
        int(plan["start_utc"]) - 3600, int(plan["end_utc"]),
    )
    report = audit_changed_energy(
        saved, rows.get(importer.statistic_id, []),
        entry.options.get(CONF_TARIFF_VERSIONS, []),
        hass.config.time_zone,
    )
    # Internal write plan and energy fingerprint are never shown publicly.
    return {k: v for k, v in report.items() if k != "_plan"}


async def launch(
    hass, entry, start_day: str | None, end_day: str | None,
    digest: str | None, *, resume: bool = False,
    rebase_energy: bool = False, expected_energy_digest: str | None = None,
    day_recalculation: bool = False,
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
            day_recalculation = bool(saved.get("day_recalculation"))
            if rebase_energy and day_recalculation:
                return {"started": False, "message": "Day repair needs original energy evidence; resume without energy rebase"}
            if rebase_energy and (
                not isinstance(expected_energy_digest, str)
                or len(expected_energy_digest) != 64
            ):
                return {"started": False, "message": "Assess changed energy again before fixing"}
            if not incomplete:
                return {
                    "started": False,
                    "message": "No interrupted cost repair remains. Preview your desired dates",
                }
            # The original protected plan and its backup are authoritative.
            start_day, end_day, digest = (
                saved.get("start_date"), saved.get("end_date"), saved.get("digest"),
            )
            if (saved.get("pricing_revision") is not None
                    and saved["pricing_revision"] != pricing_revision(
                        entry.options, hass.config.time_zone,
                    )):
                return {
                    "started": False,
                    "resume_required": True,
                    "message": (
                        "The actual historical electricity tariff changed "
                        "during this protected repair. Review the original "
                        "journal before any further monetary writes"
                    ),
                }
            if (saved.get("pricing_revision") is None
                    and saved.get("settings_revision") != revision(entry.options)):
                # PRIVATE BETA COMPATIBILITY ONLY. Previous versions hashed all
                # entry options, not just pricing, and strand safe journals when
                # unrelated options change. Compare the entire original
                # independently priced plan to the current historical tariff,
                # current source energy and original/target Recorder rows.
                # Never clear a partially written journal on a guessed rate.
                from .private_beta_journal_recovery import verify_private_journal
                previous_revision = saved.get("settings_revision")
                current_revision = revision(entry.options)
                try:
                    importer = hass.data.get(DOMAIN, {}).get(entry.entry_id)
                    manager = hass.data.get(f"{DOMAIN}_migration", {}).get(entry.entry_id)
                    if importer is None or manager is None:
                        raise ValueError("Meter or backup manager is unavailable")
                    await manager._verify_prior_backup(saved["backup_id"])
                    for backup_id in saved.get("tail_backups", []):
                        await manager._verify_prior_backup(backup_id)
                    proof = await verify_private_journal(hass, entry, saved)
                    if revision(entry.options) != current_revision:
                        raise ValueError("Settings changed during recovery verification")
                except (ValueError, KeyError, TypeError, RuntimeError) as err:
                    if await _abort_only_if_untouched(hass, entry, saved):
                        return {
                            "started": False,
                            "message": (
                                "The obsolete repair was independently verified "
                                "untouched and safely retired. No Recorder rows "
                                "were changed. Preview the selected day again"
                            ),
                            "legacy_journal_retired": True,
                        }
                    return {
                        "started": False, "resume_required": True,
                        "message": (
                            "Protected repair preserved. Automatic recovery "
                            f"requires review: {err}"
                        )[:500],
                    }
                # Rebinding ONLY this proven legacy journal does not mutate
                # the signed row plan, backup references or Recorder values.
                # The normal runner will reverify all those inputs under lock.
                saved["private_beta_recovery"] = {
                    **proof, "original_settings_revision": previous_revision,
                }
                saved["settings_revision"] = current_revision
                saved["pricing_revision"] = pricing_revision(
                    entry.options, hass.config.time_zone,
                )
                await _store(hass, entry.data["meter_mac"]).async_save(saved)
                _LOGGER.warning(
                    "PalPulse private-beta legacy journal %s safely "
                    "revalidated for %s; original %s",
                    saved.get("digest"), entry.entry_id, previous_revision,
                )
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
            if day_recalculation and start_day != end_day:
                return {"started": False, "message": "Select exactly one completed local billing day"}
            if day_recalculation:
                from .cost_reconcile_runtime import preview_single_day_recalculation
                fresh = await preview_single_day_recalculation(hass, entry, start_day)
            else:
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
            "end_date": end_day, "day_recalculation": bool(day_recalculation),
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
                    allow_energy_rebase=rebase_energy,
                    expected_energy_digest=expected_energy_digest,
                    day_recalculation=day_recalculation,
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
