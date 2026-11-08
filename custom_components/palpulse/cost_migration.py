"""Backup-first, opt-in legacy monetary statistics migration.

Uses the proven usage migration's durable journal, reconciliation, preview
arming, and targeted undo, but with separate source, target and store.
Historical electricity energy is NEVER rewritten by this workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_list_statistic_ids
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .cost_migration_plan import plan_cost_migration
from .migration import LegacyMigration, MigrationValidationError, EARLIEST, _utc


class LegacyCostMigration(LegacyMigration):
    """Import recorded old costs without assuming historical tariff prices."""

    def __init__(self, hass, entry, target_id):
        super().__init__(hass, entry, target_id)
        self.source_id = entry.options["legacy_cost_statistic_id"].strip()
        if not self.source_id or self.source_id == target_id:
            raise MigrationValidationError("Select a different legacy cost statistic")
        self._store = Store(
            hass, 1, f"{DOMAIN}_cost_migration_{entry.entry_id}"
        )
        self._notification_title = "PalPulse experimental historical cost migration"
        self._notification_id = f"palpulse_cost_migration_{entry.entry_id}"
        self._progress_noun = "historical electricity costs"

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

    async def _plan(self):
        available = await async_list_statistic_ids(
            self.hass, {self.source_id, self.target_id}, "sum"
        )
        index = {item["statistic_id"]: item for item in available}
        for stat_id in (self.source_id, self.target_id):
            meta = index.get(stat_id)
            if not meta or not meta.get("has_sum"):
                raise MigrationValidationError(
                    f"No cumulative cost sums for {stat_id}. "
                    "A current-price sensor or daily reset without sum data cannot be imported"
                )
            unit = meta.get("statistics_unit_of_measurement")
            if unit not in (None, self.hass.config.currency, "$"):
                raise MigrationValidationError(
                    f"{stat_id} has unsupported cost unit {unit!r}"
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
        return (
            f"READ-ONLY cost preview: {plan.count:,} original hourly cost rows "
            f"from {self.source_id} would be copied to {self.target_id} "
            f"before { _utc(plan.cutover).isoformat() }. "
            f"Old data contains {plan.gaps} gaps, "
            f"{len(plan.decreases)} decreases and {plan.large_steps} large cost changes. "
            "Original costs, PalPulse energy history and all existing costs at "
            "or after cutover will not be touched. Amounts are copied as "
            "recorded, NOT recalculated with the current Red Energy tariff. "
            "Cumulative sums are rebased to the new cost statistic's "
            "zero-sum baseline, retaining hourly deltas. "
            "Review the source carefully: fixed supply charges may already "
            "be included in its total. Preview expires after 10 minutes. "
            "Import and Undo each require a verified Recorder-inclusive backup."
        )
