"""Read-only plan to import legacy monetary statistics before cost cutover.

This does NOT recalculate historical bills from today's tariff. Copy only
existing, Recorder-backed hourly cost sums into the separate PalPulse cost
statistic, shifted by a constant to meet its existing zero-sum baseline.
"""

from __future__ import annotations

import hashlib
import json
from .migration_plan import (
    MigrationPlan, MigrationValidationError, _read_rows,
    diagnose_decreases, HOUR_SECONDS, MAX_HOURS,
)


def plan_cost_migration(source_rows: list[dict], target_rows: list[dict]) -> MigrationPlan:
    old = _read_rows(source_rows, "Legacy electricity costs")
    new = _read_rows(target_rows, "PalPulse electricity costs")
    if not old:
        raise MigrationValidationError(
            "No legacy cost statistics with hourly state/sum found. "
            "Choose the original cumulative cost statistic, not a price-per-kWh sensor"
        )
    if not new:
        raise MigrationValidationError(
            "PalPulse has no cost statistic yet. Enable custom tariffs and "
            "wait for one complete native-minute hour"
        )
    cutover, anchor_state, anchor_sum = new[0]
    if abs(anchor_sum) > 1e-6 or abs(anchor_state) > 1e-6:
        raise MigrationValidationError(
            "First PalPulse cost row is not its zero-sum baseline"
        )
    eligible = [r for r in old if r[0] < cutover]
    if not eligible:
        raise MigrationValidationError("No old cost statistics predate PalPulse costs")
    if len(eligible) > MAX_HOURS:
        raise MigrationValidationError("Legacy cost history exceeds safety row limit")

    gaps = sum(b[0] > a[0] + HOUR_SECONDS for a, b in zip(eligible, eligible[1:]))
    decreases = diagnose_decreases(eligible)
    # Shift only the cumulative sum; the source's recorded hourly states
    # remain unchanged. This preserves source-to-source hourly cost changes.
    offset = anchor_sum - eligible[-1][2]
    rows = tuple((t, state, round(summation + offset, 10))
                 for t, state, summation in eligible)
    if any(row[2] > anchor_sum + 1e-6 for row in rows):
        raise MigrationValidationError(
            "Legacy cost sums exceed the cutover anchor after rebasing. "
            "Source may have resets or adjustments requiring review"
        )
    digest = hashlib.sha256(json.dumps(
        [cutover, anchor_state, anchor_sum, rows],
        separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    return MigrationPlan(
        cutover=cutover,
        first_hour=rows[0][0],
        hours=rows,
        digest=digest,
        gaps=gaps,
        large_steps=sum(
            b[2] - a[2] > 50 for a, b in zip(eligible, eligible[1:])
        ),
        decreases=decreases,
        anchor_state=anchor_state,
        anchor_sum=anchor_sum,
    )
