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
    LegacyDecreaseError,
    MigrationValidationError,
    plan_migration,
    validate_undo,
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

    async def async_load(self) -> None:
        """Keep undo state after a Home Assistant restart."""
        self.journal = await self._store.async_load() or {}
        if self.journal.get("status") in {"prepared", "applying", "undoing", "failed"}:
            self.last_message = (
                "Previous migration was interrupted. Review and use Undo. "
                "Do not run another migration"
            )
            self._notify(self.last_message)

    def _notify(self, message: str) -> None:
        self.last_message = message
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
        warning_text = "; ".join(warnings) if warnings else "No large jumps flagged"
        return (
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

    def _decrease_diagnostic(self, error: LegacyDecreaseError) -> str:
        """Explain the blocked source history without authorizing any writes."""
        try:
            time_zone = ZoneInfo(self.hass.config.time_zone)
            zone_label = self.hass.config.time_zone
        except (AttributeError, ZoneInfoNotFoundError, TypeError, KeyError):
            time_zone = timezone.utc
            zone_label = "UTC"
        lines = [
            "PREVIEW BLOCKED. Original Home Assistant Powerpal hourly cumulative "
            f"sum decreases {len(error.decreases)} time(s) before PalPulse's "
            "first data point.",
            "",
            f"First {min(10, len(error.decreases))} affected hours "
            f"({zone_label}):",
        ]
        for item in error.decreases[:10]:
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
            "These could be legitimate counter resets or bad/corrected "
            "Recorder rows. The integration cannot safely decide which. "
            "Original source readings have NOT been changed.",
            "No migration or backup was started by this read-only preview. "
            "Import remains blocked. Do not use manual Adjust statistic "
            "controls until the source data has been independently reviewed.",
        ])
        return "\n".join(lines)

    async def async_preview(self) -> None:
        """Report source issues without silently loosening import checks."""
        async with self._lock:
            self._preview = None
            self._preview_expires = 0.0
            if self.journal.get("status") not in {None, "undone"}:
                raise HomeAssistantError(
                    "An earlier migration exists. Undo it first; no data was changed"
                )
            try:
                plan = await self._plan()
            except LegacyDecreaseError as err:
                self._notify(self._decrease_diagnostic(err))
                return
            except MigrationValidationError as err:
                self._notify(
                    "PREVIEW BLOCKED. " + str(err) +
                    ". No data changed; no backup or import started."
                )
                return
            self._preview = plan
            self._preview_expires = time.monotonic() + PREVIEW_SECONDS
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

    async def async_migrate(self) -> None:
        """Write strictly before the first existing PalPulse hour."""
        async with self._lock:
            if self.journal.get("status") not in {None, "undone"}:
                raise HomeAssistantError("Migration already exists. Use Undo")
            preview = self._preview
            if preview is None or time.monotonic() > self._preview_expires:
                raise HomeAssistantError(
                    "Use Preview migration first. Preview expires after 10 minutes"
                )
            current = await self._plan()
            if current.digest != preview.digest:
                self._preview = None
                raise HomeAssistantError(
                    "Legacy records or cutover changed. Preview again"
                )
            # Fail closed if there are already PalPulse values in the write range.
            if await self._statistics(
                self.target_id, _utc(current.first_hour), _utc(current.cutover)
            ):
                raise HomeAssistantError("Target already has historical rows in range")
            # We never silently overwrite existing five-minute PalPulse records.
            if await self._statistics(
                self.target_id, _utc(current.first_hour),
                _utc(current.cutover), "5minute"
            ):
                raise HomeAssistantError("Target has short-term rows. Import blocked")

            backup_id = await self._backup("before migration")
            self.journal = {
                "version": 1,
                "status": "prepared",
                "source": self.source_id,
                "target": self.target_id,
                "start": current.first_hour,
                "cutover": current.cutover,
                "digest": current.digest,
                "rows": [list(row) for row in current.hours],
                "backup_before_migration": backup_id,
                "backup_before_undo": None,
            }
            # Durable recovery journal must exist BEFORE the first Recorder write.
            await self._save()
            metadata = {
                "statistic_id": self.target_id,
                "source": DOMAIN,
                "name": STAT_NAME,
                "unit_of_measurement": "kWh",
                "unit_class": "energy",
                "mean_type": StatisticMeanType.NONE,
                "has_sum": True,
            }
            self.journal["status"] = "applying"
            await self._save()
            try:
                for i in range(0, current.count, CHUNK_SIZE):
                    segment = current.hours[i:i + CHUNK_SIZE]
                    async_add_external_statistics(self.hass, metadata, [
                        {"start": _utc(ts), "state": state, "sum": summation}
                        for ts, state, summation in segment
                    ])
                    await get_instance(self.hass).async_block_till_done()

                present = await self._statistics(
                    self.target_id, _utc(current.first_hour), _utc(current.cutover)
                )
                validate_undo(self.journal["rows"], present,
                              current.first_hour, current.cutover)
                if len(present) != current.count:
                    raise HomeAssistantError("Recorder did not write every old hour")
            except Exception:
                self.journal["status"] = "failed"
                await self._save()
                self._notify(
                    "Migration incomplete. Original sensor was not touched. "
                    "Use Undo to remove any inserted pre-cutover rows. "
                    f"Backup: {backup_id}"
                )
                raise
            self.journal["status"] = "applied"
            await self._save()
            self._preview = None
            self._notify(
                f"Imported {current.count} legacy hours into {self.target_id}. "
                f"Original sensor untouched. Pre-migration backup: {backup_id}. "
                "Undo available via the Undo historical migration button"
            )

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
