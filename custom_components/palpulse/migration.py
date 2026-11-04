"""Opt-in experimental migration with backup-first writes and targeted undo.

Source sensor statistics are always read-only. Only the external PalPulse
statistic can be written, and only strictly before its first existing hour.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import logging
import time

from homeassistant.components import backup, persistent_notification
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STAT_NAME
from .migration_plan import (
    MigrationPlan,
    LegacyDecrease,
    MigrationValidationError,
    plan_migration,
    validate_undo,
    missing_migration_rows,
    validate_original_snapshot,
)

_LOGGER = logging.getLogger(__name__)
LEGACY_STATISTIC_ID = "sensor.powerpal_proxy_powerpal_total_energy"
EARLIEST = datetime(2010, 1, 1, tzinfo=timezone.utc)
PREVIEW_SECONDS = 600
CHUNK_SIZE = 240


def _utc(stamp: int) -> datetime:
    return datetime.fromtimestamp(stamp, tz=timezone.utc)


class LegacyMigration:
    """Back up, import only pre-cutover rows, and precisely undo those rows."""

    def __init__(self, hass: HomeAssistant, entry, target_id: str) -> None:
        self.hass = hass
        self.entry = entry
        self.target_id = target_id
        self.source_id = LEGACY_STATISTIC_ID
        self._store = Store(hass, 1, f"{DOMAIN}_migration_{entry.entry_id}")
        self._lock = asyncio.Lock()
        self._preview: MigrationPlan | None = None
        self._preview_expires = 0.0
        self.journal: dict = {}
        self.last_message = "No migration has been previewed"
        self._stage = "idle"
        self._completed = 0
        self._total = 0
        self._listeners = set()

    @property
    def progress(self) -> dict:
        """Human-readable, live and reboot-recoverable migration snapshot."""
        status = self.journal.get("status", "never_run")
        total = self._total or self.journal.get("total_rows", len(self.journal.get("rows", [])))
        done = self._completed
        percent = round(100 * done / total, 1) if total else None
        return {
            "stage": self._stage,
            "journal_status": status,
            "completed_rows": done,
            "total_rows": total,
            "percent": percent,
            "resume_available": status in {"prepared", "applying", "failed"},
            "last_message": self.last_message,
            "last_error": self.journal.get("last_error"),
            "pre_migration_backup": self.journal.get("backup_before_migration"),
            "pre_undo_backup": self.journal.get("backup_before_undo"),
            "last_updated": self.journal.get("updated_at"),
        }

    def subscribe(self, callback):
        """Notify HA entities when a stage or completed row count changes."""
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def _stage_update(self, stage: str, *, done: int | None = None,
                      total: int | None = None, message: str | None = None) -> None:
        self._stage = stage
        if done is not None:
            self._completed = done
        if total is not None:
            self._total = total
        if message is not None:
            self.last_message = message
        for callback in tuple(self._listeners):
            callback()

    async def _checkpoint(self, stage: str, completed: int, total: int) -> None:
        """Durably record progress AFTER confirmed Recorder writes."""
        self.journal["stage"] = stage
        self.journal["confirmed_rows"] = completed
        self.journal["total_rows"] = total
        self.journal["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._save()
        self._stage_update(stage, done=completed, total=total)

    async def async_load(self) -> None:
        """Keep undo state after a Home Assistant restart."""
        self.journal = await self._store.async_load() or {}
        status = self.journal.get("status")
        self._total = int(self.journal.get("total_rows", len(self.journal.get("rows", []))))
        self._completed = int(self.journal.get("confirmed_rows", 0))
        if status in {"prepared", "applying", "failed"}:
            self._stage = "interrupted"
            self._notify(
                "An earlier migration attempt was detected. "
                "Recorded progress may be behind actual Recorder writes. "
                "Press Import or Resume historical migration to safely inspect "
                "and reconcile existing rows before continuing. Do not use Undo "
                "unless you intend to remove already imported hours."
            )
        elif status == "undoing":
            self._stage = "interrupted_undo"
            self._notify(
                "An earlier Undo was interrupted. Review the PalPulse target "
                "statistic and use Undo only after confirming its backup."
            )
        elif status == "applied":
            self._stage = "completed"
            self.last_message = "Historical migration completed"
        elif status == "undone":
            self._stage = "undone"
            self.last_message = "Historical migration was undone"

    def _notify(self, message: str) -> None:
        self.last_message = message
        self._stage_update(self._stage, message=message)
        persistent_notification.async_create(
            self.hass, message, title="PalPulse experimental history migration",
            notification_id=f"palpulse_migration_{self.entry.entry_id}",
        )

    async def _statistics(self, statistic_id: str,
                          start: datetime, end: datetime,
                          period: str = "hour") -> list[dict]:
        def read() -> list[dict]:
            result = statistics_during_period(
                self.hass, start, end, {statistic_id}, period,
                {"energy": "kWh"}, {"sum", "state"},
            )
            return result.get(statistic_id, [])
        return await self.hass.async_add_executor_job(read)

    async def _plan(self) -> MigrationPlan:
        available = await async_list_statistic_ids(
            self.hass, {self.source_id, self.target_id}, "sum"
        )
        by_id = {item["statistic_id"]: item for item in available}
        for statistic_id in (self.source_id, self.target_id):
            metadata = by_id.get(statistic_id)
            if not metadata or not metadata.get("has_sum"):
                raise MigrationValidationError(
                    f"Statistics with hourly sums not found: {statistic_id}"
                )
            if (metadata.get("unit_class") != "energy" or
                    metadata.get("statistics_unit_of_measurement") not in
                    {"kWh", "Wh", "MWh"}):
                raise MigrationValidationError(
                    f"Unsupported legacy energy unit for {statistic_id}"
                )
        end = datetime.now(timezone.utc)
        # Include the current completed UTC hour but exclude future rows.
        end = _utc((int(end.timestamp()) // 3600 + 2) * 3600)
        target_rows = await self._statistics(self.target_id, EARLIEST, end)
        source_rows = await self._statistics(self.source_id, EARLIEST, end)
        return plan_migration(source_rows, target_rows)

    def _summary(self, plan: MigrationPlan) -> str:
        warnings = []
        if plan.gaps:
            warnings.append(f"{plan.gaps} gaps in old hourly data (not filled)")
        if plan.large_steps:
            warnings.append(
                f"{plan.large_steps} large legacy consumption jumps "
                "(copied as recorded, not corrected)"
            )
        if plan.decreases:
            warnings.append(
                f"{len(plan.decreases)} historical cumulative-sum decrease(s) "
                "(kept exactly as recorded, including negative hourly deltas)"
            )
        warning_text = "; ".join(warnings) if warnings else "No large jumps flagged"
        message = (
            f"Preview only. {plan.count} old hourly rows from "
            f"{_utc(plan.first_hour).isoformat()} to "
            f"{_utc(plan.cutover).isoformat()} (exclusive) will be added to "
            f"{self.target_id}.\n\n"
            f"Original {self.source_id} will NEVER be changed. "
            "Existing PalPulse hours at and after the cutover will remain intact. "
            "Old cumulative sums will be shifted to meet PalPulse's zero baseline. "
            f"{warning_text}.\n\n"
            "The migration button is armed for ten minutes. A fresh Home Assistant "
            "backup with the Recorder database is mandatory before importing. "
            "Undo makes another backup before removing only the imported hours."
        )
        if plan.decreases:
            message += "\n\n" + self._decrease_diagnostic(plan.decreases)
        return message

    def _decrease_diagnostic(self, decreases: tuple[LegacyDecrease, ...]) -> str:
        """Warn about original Recorder drops without changing their values."""
        try:
            time_zone = ZoneInfo(self.hass.config.time_zone)
            zone_label = self.hass.config.time_zone
        except (AttributeError, ZoneInfoNotFoundError, TypeError, KeyError):
            time_zone = timezone.utc
            zone_label = "UTC"
        lines = [
            "REVIEW BEFORE IMPORT. Original Powerpal's hourly cumulative sum "
            f"decreases {len(decreases)} time(s) before the PalPulse cutover.",
            "",
            f"First {min(10, len(decreases))} affected hours "
            f"({zone_label}):",
        ]
        for item in decreases[:10]:
            hour = datetime.fromtimestamp(
                item.later_hour, timezone.utc
            ).astimezone(time_zone).strftime("%Y-%m-%d %H:%M %Z")
            lines.append(
                f"- {hour}: cumulative sum {item.before_sum:.5f} -> "
                f"{item.after_sum:.5f} kWh "
                f"(change {item.difference:+.5f} kWh); "
                f"raw meter state {item.before_state:.5f} -> "
                f"{item.after_state:.5f} kWh"
            )
        lines.extend([
            "",
            "Home Assistant can record these decreases for total_increasing "
            "entities. Preview does not change the original sensor or its "
            "history. The migration will preserve all original hour-to-hour "
            "sum changes, including negative values. It will NOT smooth, "
            "correct or guess missing consumption.",
            "If you accept importing this historic behaviour, press the "
            "separate Import button within ten minutes. Only then will "
            "PalPulse create and verify a Recorder-inclusive HA backup "
            "BEFORE writing any PalPulse history. Undo also creates a backup.",
        ])
        return "\n".join(lines)

    async def async_preview(self) -> None:
        """Read-only plan with visible status and a short-lived import arm."""
        async with self._lock:
            self._preview = None
            self._preview_expires = 0.0
            if self.journal.get("status") not in {None, "undone"}:
                message = (
                    "An existing migration was detected. Use Import to inspect "
                    "and safely resume it, or Undo to remove only migrated rows."
                )
                self._stage_update("blocked", message=message)
                self._notify(message)
                return
            self._stage_update("planning", done=0, total=0,
                               message="Inspecting original and PalPulse statistics")
            try:
                plan = await self._plan()
            except Exception as err:
                self._stage_update("blocked", message=str(err))
                self._notify(
                    "PREVIEW BLOCKED. " + str(err) +
                    ". No data changed; no backup or import started."
                )
                return
            self._preview = plan
            self._preview_expires = time.monotonic() + PREVIEW_SECONDS
            self._stage_update("ready", done=0, total=plan.count)
            self._notify(self._summary(plan))

    async def _backup(self, reason: str) -> str:
        """Await a completed HA backup INCLUDING the Recorder database.

        Unlike backup.create_automatic, this await doesn't finish until the
        backup manager has completed its backup task. Never proceed on failure.
        """
        manager = backup.async_get_manager(self.hass)
        settings = manager.config.data.create_backup
        agent_ids = list(settings.agent_ids)
        if not agent_ids:
            # Use one local agent, not all cloud agents, if no backup
            # destination has been selected by the administrator.
            local = next(iter(manager.local_backup_agents), None)
            if local:
                agent_ids = [local]
        if not agent_ids:
            raise HomeAssistantError(
                "No backup destination is available. Configure HA Backups first"
            )
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        created = await manager.async_create_backup(
            agent_ids=agent_ids,
            include_addons=None,
            include_all_addons=False,
            include_database=True,   # Mandatory, even if automatic backups exclude it
            include_folders=None,
            include_homeassistant=True,
            name=f"PalPulse {reason} {stamp}",
            password=settings.password,
            with_automatic_settings=False,  # Do not apply auto-backup retention
        )
        backup_id = created.backup_job_id
        if not backup_id:
            raise HomeAssistantError("Backup did not return an identifier")
        # The manager can finish without throwing even if an upload agent failed.
        # Require a stored, retrievable backup that actually includes Recorder.
        verified, agent_errors = await manager.async_get_backup(backup_id)
        if (
            verified is None
            or not verified.database_included
            or not verified.homeassistant_included
            or not any(id_ in verified.agents for id_ in agent_ids)
        ):
            raise HomeAssistantError(
                "Backup could not be verified with its Recorder database. "
                f"No statistics were modified. Agent errors: {agent_errors}"
            )
        _LOGGER.warning("PalPulse %s verified HA backup %s", reason, backup_id)
        return backup_id

    async def _save(self) -> None:
        await self._store.async_save(self.journal)

    async def _verify_prior_backup(self, backup_id: str) -> None:
        """Require the original Recorder-inclusive backup before resuming."""
        if not backup_id:
            raise HomeAssistantError("No verified pre-import backup in journal")
        manager = backup.async_get_manager(self.hass)
        original, errors = await manager.async_get_backup(backup_id)
        if (
            original is None
            or not original.database_included
            or not original.homeassistant_included
            or not original.agents
        ):
            raise HomeAssistantError(
                f"Original Recorder-inclusive backup {backup_id} is not available: "
                f"{errors}. Resume blocked."
            )

    async def _resume_locked(self) -> None:
        """Reconcile committed rows, then insert ONLY rows still missing."""
        journal = self.journal
        if journal.get("status") not in {"prepared", "applying", "failed"}:
            raise HomeAssistantError("No incomplete historical import to resume")
        if journal.get("source") != self.source_id or journal.get("target") != self.target_id:
            raise HomeAssistantError("Stored migration journal belongs to another meter")
        expected = journal.get("rows")
        start = int(journal.get("start") or 0)
        cutover = int(journal.get("cutover") or 0)
        if not expected or start <= 0 or cutover <= start:
            raise HomeAssistantError("Incomplete or corrupt migration journal")
        if expected[0][0] != start or expected[-1][0] >= cutover:
            raise HomeAssistantError("Migration journal range is invalid")
        self._stage_update(
            "reconciling", total=len(expected),
            message="Verifying existing Recorder rows before any new writes"
        )
        # A queued write from an interrupted button press must finish first.
        await asyncio.wait_for(
            get_instance(self.hass).async_block_till_done(), timeout=180
        )
        await self._verify_prior_backup(journal.get("backup_before_migration"))
        original = await self._statistics(self.source_id, _utc(start), _utc(cutover))
        validate_original_snapshot(expected, original)
        existing = await self._statistics(self.target_id, _utc(start), _utc(cutover))
        missing = missing_migration_rows(expected, existing, start, cutover)
        done = len(expected) - len(missing)
        # An earlier journal counter can lag Recorder when HA exited after
        # committing a chunk. Recorder is authoritative, not the counter.
        await self._checkpoint("reconciled", done, len(expected))
        if missing:
            five_minute = await self._statistics(
                self.target_id, _utc(start), _utc(cutover), "5minute"
            )
            if five_minute:
                raise HomeAssistantError(
                    "PalPulse has five-minute statistics in the import range. "
                    "Resume blocked to avoid overwriting data."
                )
        metadata = {
            "statistic_id": self.target_id,
            "source": DOMAIN,
            "name": STAT_NAME,
            "unit_of_measurement": "kWh",
            "unit_class": "energy",
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
        }
        journal["status"] = "applying"
        journal["last_error"] = None
        await self._checkpoint("importing", done, len(expected))
        for index in range(0, len(missing), CHUNK_SIZE):
            chunk = missing[index:index + CHUNK_SIZE]
            # Do not resubmit any row already present in Recorder.
            async_add_external_statistics(self.hass, metadata, [
                {"start": _utc(ts), "state": state, "sum": summation}
                for ts, state, summation in chunk
            ])
            await asyncio.wait_for(
                get_instance(self.hass).async_block_till_done(), timeout=180
            )
            # Verify this batch was ACTUALLY committed before advancing the
            # durable row count. Crashes between DB and journal commits are safe.
            self._stage_update("verifying", message=(
                f"Checking imported batch {index // CHUNK_SIZE + 1}"
            ))
            batch_rows = await self._statistics(
                self.target_id, _utc(chunk[0][0]), _utc(chunk[-1][0] + 3600)
            )
            still_missing = missing_migration_rows(
                expected, batch_rows, start, cutover
            )
            missing_times = {row[0] for row in still_missing}
            if any(row[0] in missing_times for row in chunk):
                raise HomeAssistantError(
                    "Recorder did not commit all rows in the latest batch. "
                    "Import paused for safe reconciliation."
                )
            done += len(chunk)
            await self._checkpoint("importing", done, len(expected))
        self._stage_update(
            "verifying", done=done, total=len(expected),
            message="Verifying every historical row against the saved journal",
        )
        final = await self._statistics(self.target_id, _utc(start), _utc(cutover))
        if missing_migration_rows(expected, final, start, cutover):
            raise HomeAssistantError(
                "Final verification found missing migrated hours. Resume required."
            )
        journal["status"] = "applied"
        journal["last_error"] = None
        await self._checkpoint("completed", len(expected), len(expected))
        self._preview = None
        self._preview_expires = 0.0
        self._notify(
            f"Historical import complete: {len(expected)} hourly records "
            f"verified in {self.target_id}. The original source remains "
            f"untouched. Backup: {journal['backup_before_migration']}. "
            "Undo remains available."
        )

    async def async_migrate(self) -> None:
        """Start a planned import, or safely resume an interrupted attempt."""
        async with self._lock:
            try:
                if self.journal.get("status") in {"prepared", "applying", "failed"}:
                    await self._resume_locked()
                    return
                if self.journal.get("status") not in {None, "undone"}:
                    raise HomeAssistantError(
                        "Migration already completed. Use Undo before starting another"
                    )
                preview = self._preview
                if preview is None or time.monotonic() > self._preview_expires:
                    raise HomeAssistantError(
                        "Use Preview migration first. Preview expires after 10 minutes"
                    )
                self._stage_update(
                    "planning", done=0, total=preview.count,
                    message="Rechecking source and cutoff before making any changes",
                )
                current = await self._plan()
                if current.digest != preview.digest:
                    self._preview = None
                    raise HomeAssistantError(
                        "Source statistics or cutover changed. Preview again"
                    )
                if await self._statistics(
                    self.target_id, _utc(current.first_hour), _utc(current.cutover)
                ):
                    raise HomeAssistantError(
                        "Target already has historical rows in import range"
                    )
                if await self._statistics(
                    self.target_id, _utc(current.first_hour),
                    _utc(current.cutover), "5minute"
                ):
                    raise HomeAssistantError(
                        "Target already has short-term data in range"
                    )
                self._stage_update(
                    "backing_up", done=0, total=current.count,
                    message="Creating and verifying a Recorder-inclusive HA backup",
                )
                self._notify(
                    f"Historical migration started. Backing up Home Assistant "
                    f"including Recorder before importing {current.count} hours. "
                    "Watch the PalPulse Migration Status and Progress entities."
                )
                backup_id = await self._backup("before migration")
                self.journal = {
                    "version": 2,
                    "status": "prepared",
                    "stage": "prepared",
                    "source": self.source_id,
                    "target": self.target_id,
                    "start": current.first_hour,
                    "cutover": current.cutover,
                    "digest": current.digest,
                    "rows": [list(row) for row in current.hours],
                    "confirmed_rows": 0,
                    "total_rows": current.count,
                    "backup_before_migration": backup_id,
                    "backup_before_undo": None,
                }
                await self._checkpoint("prepared", 0, current.count)
                # The journal is now durable BEFORE any statistic is changed.
                await self._resume_locked()
            except Exception as err:
                self._stage_update("failed", message=str(err))
                if self.journal.get("status") in {"prepared", "applying", "failed"}:
                    self.journal["status"] = "failed"
                    self.journal["last_error"] = str(err)
                    self.journal["stage"] = "failed"
                    await self._save()
                self._notify(
                    "Historical migration stopped. " + str(err) +
                    ". Original source is untouched. If a backup and journal "
                    "exist, press Import again to reconcile and safely resume. "
                    "Inspect progress entities before retrying."
                )
                raise

    async def async_resume(self) -> None:
        """Explicit resume button; never starts a new unpreviewed migration."""
        async with self._lock:
            try:
                await self._resume_locked()
            except Exception as err:
                self._stage_update("blocked", message=str(err))
                self._notify("Resume blocked: " + str(err) +
                             ". No existing rows were overwritten.")
                raise

    async def async_undo(self) -> None:
        """Remove ONLY journalled target hours before cutover, with a new backup."""
        async with self._lock:
            journal = self.journal
            if journal.get("status") not in {"prepared", "applying", "applied",
                                             "failed", "undoing"}:
                raise HomeAssistantError("No active legacy migration to undo")
            if journal.get("source") != self.source_id or (
                journal.get("target") != self.target_id
            ):
                raise HomeAssistantError("Migration journal identity mismatch")
            start, end = int(journal["start"]), int(journal["cutover"])
            expected = journal["rows"]
            if not expected or min(int(r[0]) for r in expected) != start or (
                max(int(r[0]) for r in expected) >= end
            ):
                raise HomeAssistantError("Unsafe or corrupt migration journal")

            actual = await self._statistics(self.target_id, _utc(start), _utc(end))
            try:
                validate_undo(expected, actual, start, end)
            except MigrationValidationError as err:
                raise HomeAssistantError(
                    "Undo stopped rather than deleting changed data: " + str(err)
                ) from err
            if await self._statistics(self.target_id, _utc(start),
                                      _utc(end), "5minute"):
                raise HomeAssistantError(
                    "Undo blocked: new short-term statistics exist in import range"
                )
            backup_id = await self._backup("before undo")
            journal["backup_before_undo"] = backup_id
            journal["status"] = "undoing"
            await self._save()

            # A recorder range clear never touches source statistics or any
            # PalPulse hour at/after cutover. The on_done callback is called
            # from Recorder's worker thread.
            done = asyncio.Event()
            def finished() -> None:
                self.hass.loop.call_soon_threadsafe(done.set)

            get_instance(self.hass).async_clear_statistics(
                [self.target_id], start_time=_utc(start),
                end_time=_utc(end), on_done=finished,
            )
            try:
                await asyncio.wait_for(done.wait(), timeout=90)
                remaining = await self._statistics(
                    self.target_id, _utc(start), _utc(end)
                )
                if remaining:
                    raise HomeAssistantError("Some imported hours remain after Undo")
            except Exception:
                journal["status"] = "failed"
                await self._save()
                self._notify(
                    "Undo did not finish cleanly. Review the target statistic "
                    f"before retrying. Backup: {backup_id}"
                )
                raise

            journal["status"] = "undone"
            await self._save()
            self._preview = None
            self._notify(
                f"Undo completed. Only migrated PalPulse hours before "
                f"{_utc(end).isoformat()} were removed. Original Powerpal "
                f"history and PalPulse's existing/live hours are untouched. "
                f"Pre-undo backup: {backup_id}"
            )
