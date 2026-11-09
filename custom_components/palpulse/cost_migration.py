"""Separate backup-first monetary history migration for PalPulse.

Two explicit source modes:
- cumulative: copy genuine Recorder hourly monetary 'sum' statistics;
- daily_reset: derive cumulative hourly changes from a previous daily-running
  cost sensor's confirmed hourly states and complete local calendar days.

No historical energy, legacy source, or live cost rows are ever rewritten.
The parent migration provides read-only preview, backups, journal and Undo.
"""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_list_statistic_ids
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .cost_migration_plan import plan_cost_migration
from .daily_cost_plan import convert_daily_reset_costs
from .migration import LegacyMigration, MigrationValidationError, EARLIEST, _utc

CONF_LEGACY_COST_STATISTIC_ID = "legacy_cost_statistic_id"
CONF_LEGACY_COST_SOURCE_MODE = "legacy_cost_source_mode"
SOURCE_CUMULATIVE = "cumulative"
SOURCE_DAILY_RESET = "daily_reset"
SOURCE_MODES = (SOURCE_CUMULATIVE, SOURCE_DAILY_RESET)


class LegacyCostMigration(LegacyMigration):
    """Existing cost statistics are read-only; only PalPulse cost is imported."""

    def __init__(self, hass, entry, target_id):
        super().__init__(hass, entry, target_id)
        options = getattr(entry, "options", {})
        self.source_id = str(options.get(
            CONF_LEGACY_COST_STATISTIC_ID, ""
        )).strip()
        self.source_mode = options.get(
            CONF_LEGACY_COST_SOURCE_MODE, SOURCE_CUMULATIVE
        )
        self._store = Store(
            hass, 1, f"{DOMAIN}_cost_migration_{entry.entry_id}"
        )
        self._notification_title = "PalPulse experimental historical cost migration"
        self._notification_id = f"palpulse_cost_migration_{entry.entry_id}"
        self._progress_noun = "historical electricity costs"
        self._daily_diagnostics = None

    async def async_readiness(self) -> dict[str, str]:
        """Read-only prerequisite checks for the historical-cost GUI wizard.

        This deliberately DOES NOT manufacture a cost statistic or choose a
        historical cutover. The original migration planner remains the final
        validation authority once the live statistic genuinely exists.
        """
        writer = self.hass.data.get(f"{DOMAIN}_cost", {}).get(self.entry.entry_id)
        writer_status = writer.status if writer is not None else None
        last_error = (
            str(writer_status.get("last_error") or "")[:600]
            if writer_status else ""
        )
        base = {
            "source": self.source_id or "Not configured",
            "target": self.target_id,
            "writer_status": (
                "Error: " + last_error if last_error
                else "Active" if writer_status and writer_status.get("last_costed_hour") is not None
                else "Waiting" if writer_status else "Not enabled"
            ),
        }

        def result(status: str, guidance: str) -> dict[str, str]:
            return {**base, "status": status, "guidance": guidance}

        if self.journal.get("status") not in (None, "undone"):
            return result(
                "previous_import",
                "A historical cost migration journal already exists. Review its "
                "status, Resume or Undo rather than starting a new Import.",
            )
        if not self.source_id:
            return result(
                "source_missing",
                "Choose an existing cumulative cost statistic or daily-reset "
                "monetary sensor first. No import has started.",
            )
        if self.source_id == self.target_id or self.source_mode not in SOURCE_MODES:
            return result(
                "source_invalid",
                "The previous cost source or its format is invalid. Reselect "
                "the actual historical monetary source.",
            )
        # The selected historical source and PalPulse's live destination
        # are different Recorder statistics and must be tested independently.
        try:
            available = await async_list_statistic_ids(
                self.hass, {self.source_id, self.target_id}
            )
        except Exception as err:
            return result(
                "recorder_unavailable",
                f"Recorder could not be queried ({type(err).__name__}). "
                "Review Home Assistant Recorder logs and check again.",
            )
        by_id = {item["statistic_id"]: item for item in available}
        source = by_id.get(self.source_id)
        if source is None:
            return result(
                "source_not_recorded",
                "Recorder has no historical statistics for the selected old "
                "cost source. Check that it recorded monetary hourly values "
                "and choose a different source if necessary.",
            )
        if self.source_mode == SOURCE_CUMULATIVE and not source.get("has_sum"):
            return result(
                "source_invalid",
                "The selected cost source lacks cumulative hourly sums. "
                "Choose daily-reset mode only if it has real hourly closing states.",
            )
        if source.get("unit_class") not in (None, "monetary") or (
            source.get("statistics_unit_of_measurement")
            not in (None, self.hass.config.currency, "$")
        ):
            return result(
                "source_invalid",
                "The old cost statistic has an incompatible unit or class. "
                "Choose a monetary history sensor, not a price-per-kWh sensor.",
            )

        # An existing external statistic cannot be replaced with an invented
        # first row. The normal cost recorder must verify and write a full hour.
        from .cost import is_cost_enabled

        if not is_cost_enabled(self.entry.options):
            return result(
                "pricing_disabled",
                "PalPulse custom electricity pricing is not enabled. Configure "
                "verified paid rates and the correct Red Energy time windows, "
                "then save and allow genuine cost history to begin.",
            )
        if last_error:
            return result(
                "cost_writer_error",
                "The live cost writer has stopped: " + last_error + ". "
                "Fix this before checking again. Do not fabricate a baseline.",
            )
        if writer is None:
            return result(
                "writer_unavailable",
                "The live cost recorder is not loaded. Check integration errors "
                "and reload PalPulse after configuring custom pricing.",
            )

        target = by_id.get(self.target_id)
        if target is None:
            return result(
                "waiting_for_cost",
                "No live PalPulse monetary statistic has been recorded yet. "
                "Costing starts only after a complete, timestamp-verified "
                "hour. A capped free-power offer can also require waiting "
                "until the next local billing day after a partial/gapped period. "
                "Check Live cost writer status and try again later.",
            )
        if not target.get("has_sum"):
            return result(
                "target_invalid",
                "The PalPulse cost statistic exists without cumulative sums. "
                "Do not import. Check Recorder diagnostics.",
            )
        end = _utc(
            (int(datetime.now(timezone.utc).timestamp()) // 3600 + 2) * 3600
        )
        try:
            rows = await super()._statistics(self.target_id, EARLIEST, end)
        except Exception as err:
            return result(
                "recorder_unavailable",
                f"PalPulse cost history could not be read ({type(err).__name__}). "
                "Check Recorder logs before trying again.",
            )
        if not rows:
            return result(
                "waiting_for_cost",
                "PalPulse has registered its cost statistic, but there are "
                "no verified hourly cost rows yet. Wait for a complete "
                "hour and use Check again.",
            )
        return result(
            "ready",
            f"The old source and PalPulse live cost statistic are available "
            f"({len(rows):,} cost rows currently recorded). Continue to the "
            "read-only Preview. Import still requires separate confirmation "
            "and a verified Recorder-inclusive backup.",
        )

    def _metadata(self) -> dict:
        return {
            "statistic_id": self.target_id,
            "source": DOMAIN,
            "name": "PalPulse electricity cost",
            "unit_of_measurement": None,
            "unit_class": None,
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
        }

    async def _daily_cutover(self) -> int:
        """Preserve the ORIGINAL cutover after imports change earliest target."""
        if cutover := self.journal.get("cutover"):
            return int(cutover)
        end = _utc((int(datetime.now(timezone.utc).timestamp()) // 3600 + 2) * 3600)
        target = await super()._statistics(self.target_id, EARLIEST, end)
        if not target:
            raise MigrationValidationError(
                "PalPulse has no live cost statistic yet. Configure custom "
                "pricing and wait for a verified complete hour"
            )
        return int(target[0]["start"])

    async def _statistics(self, statistic_id, start, end, period="hour"):
        """Daily-reset source is virtualized identically for plan AND resume.

        The deterministic complete-day conversion is recomputed from the entire
        unmodified source before the ORIGINAL cutover. A range-limited read
        must not rebase it differently during crash recovery.
        """
        if (
            statistic_id != self.source_id
            or self.source_mode != SOURCE_DAILY_RESET
            or period != "hour"
        ):
            return await super()._statistics(statistic_id, start, end, period)
        cutover = await self._daily_cutover()
        original = await super()._statistics(
            self.source_id, EARLIEST, _utc(cutover)
        )
        converted = convert_daily_reset_costs(
            original, cutover, self.hass.config.time_zone
        )
        self._daily_diagnostics = converted
        first = int(start.timestamp())
        last = int(end.timestamp())
        return [
            dict(row) for row in converted.rows
            if first <= row["start"] < last
        ]

    async def _plan(self):
        if not self.source_id:
            raise MigrationValidationError(
                "No historical electricity cost source selected. Go to "
                "Settings > Devices & services > Integrations > "
                "PalPulse for Home Assistant > Configure > "
                "Historical electricity costs > Choose your previous cost "
                "source, then run Preview"
            )
        if self.source_id == self.target_id:
            raise MigrationValidationError(
                "Previous cost source must differ from the PalPulse cost statistic"
            )
        if self.source_mode not in SOURCE_MODES:
            raise MigrationValidationError("Unknown previous cost source type")
        available = await async_list_statistic_ids(
            self.hass, {self.source_id, self.target_id}
        )
        index = {item["statistic_id"]: item for item in available}
        for stat_id in (self.source_id, self.target_id):
            meta = index.get(stat_id)
            if not meta:
                raise MigrationValidationError(
                    f"Recorder has no historical monetary statistic: {stat_id}"
                )
            if (
                stat_id == self.target_id
                or self.source_mode == SOURCE_CUMULATIVE
            ) and not meta.get("has_sum"):
                raise MigrationValidationError(
                    f"{stat_id} has no cumulative hourly sum statistics. "
                    "Select daily-reset mode only for a source whose "
                    "recorded hourly state represents the running daily cost"
                )
            unit = meta.get("statistics_unit_of_measurement")
            if unit not in (None, self.hass.config.currency, "$"):
                raise MigrationValidationError(
                    f"{stat_id} has unsupported monetary unit {unit!r}"
                )
            if meta.get("unit_class") not in (None, "monetary"):
                raise MigrationValidationError(
                    f"{stat_id} is not a monetary statistic"
                )
        end = _utc((int(datetime.now(timezone.utc).timestamp()) // 3600 + 2) * 3600)
        original = await self._statistics(self.source_id, EARLIEST, end)
        target = await self._statistics(self.target_id, EARLIEST, end)
        return plan_cost_migration(original, target)

    def _summary(self, plan):
        daily_text = ""
        if self.source_mode == SOURCE_DAILY_RESET and self._daily_diagnostics:
            info = self._daily_diagnostics
            daily_text = (
                f"Daily-reset source: {info.imported_days} fully observed "
                f"local billing days, {info.skipped_days} skipped days and "
                f"{info.observed_resets} midnight resets. Missing/incomplete "
                "days are NOT estimated. "
            )
        return (
            f"READ-ONLY historical cost preview. {plan.count:,} hourly "
            f"cost rows from {self.source_id} ({self.source_mode}) would be "
            f"copied to {self.target_id} before {_utc(plan.cutover).isoformat()}. "
            f"{daily_text}"
            f"Recorded history contains {plan.gaps} gaps, "
            f"{len(plan.decreases)} decreases and "
            f"{plan.large_steps} unusually large cost steps. "
            "Original electricity usage and costs stay unchanged. "
            "No current tariff is applied to old observations. "
            "Review the preview, including supply charges and any skipped days. "
            "The preview expires after 10 minutes. Import and Undo each "
            "require a verified Recorder-inclusive Home Assistant backup."
        )
