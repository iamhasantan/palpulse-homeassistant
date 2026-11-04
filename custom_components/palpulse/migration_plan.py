"""Pure planning and validation for experimental legacy statistics migration.

Never changes either source. Generates only pre-cutover rows for the new
PalPulse statistic, anchored to its existing first row's cumulative sum.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

HOUR_SECONDS = 3600
MAX_HOURS = 100_000
MAX_STEP_WARNING_KWH = 15.0


class MigrationValidationError(ValueError):
    """Migration is not safe to perform."""


@dataclass(frozen=True)
class MigrationPlan:
    """The exact rows a migration can insert without touching existing rows."""

    cutover: int
    first_hour: int
    hours: tuple[tuple[int, float, float], ...]
    digest: str
    gaps: int
    large_steps: int
    anchor_state: float
    anchor_sum: float

    @property
    def count(self) -> int:
        return len(self.hours)


def _read_rows(rows: list[dict], label: str) -> list[tuple[int, float, float]]:
    """Normalize Recorder rows, requiring valid UTC-hour sums and raw states."""
    result: list[tuple[int, float, float]] = []
    last = -1
    if len(rows) > MAX_HOURS:
        raise MigrationValidationError(f"{label} exceeds the {MAX_HOURS} hour safety limit")
    for row in sorted(rows, key=lambda r: r["start"]):
        try:
            timestamp = int(row["start"])
            state = float(row["state"])
            total = float(row["sum"])
        except (ValueError, TypeError, KeyError) as err:
            raise MigrationValidationError(f"{label} has missing state/sum data") from err
        if (
            timestamp < 1_000_000_000
            or timestamp % HOUR_SECONDS
            or timestamp <= last
            or not math.isfinite(state)
            or not math.isfinite(total)
        ):
            raise MigrationValidationError(f"{label} has invalid or duplicate hourly data")
        result.append((timestamp, state, total))
        last = timestamp
    return result


def plan_migration(
    source_rows: list[dict], target_rows: list[dict]
) -> MigrationPlan:
    """Plan a read-only, exclusive-cutover, reversible historical import.

    The first PalPulse row is never modified, even if it is a zero-sum baseline.
    Original Powerpal rows are copied only before that timestamp. Their
    cumulative sums are shifted by a constant so the final copied sum meets
    the new statistic's first sum. Both existing datasets remain unchanged.
    """
    old = _read_rows(source_rows, "Original Powerpal")
    new = _read_rows(target_rows, "PalPulse")
    if not new:
        raise MigrationValidationError("PalPulse has no completed statistic yet")
    if not old:
        raise MigrationValidationError("Original Powerpal has no hourly statistics")
    cutover, anchor_state, anchor_sum = new[0]
    eligible = [row for row in old if row[0] < cutover]
    if not eligible:
        raise MigrationValidationError("No original Powerpal hours predate PalPulse")
    if len(eligible) > MAX_HOURS:
        raise MigrationValidationError("Too many source hours to import safely")

    # The first PalPulse row must be the baseline published by our importer.
    if not math.isclose(anchor_sum, 0.0, rel_tol=0.0, abs_tol=1e-6):
        raise MigrationValidationError(
            "PalPulse's first row is not its expected zero-sum baseline"
        )
    if eligible[-1][1] > anchor_state + 0.02:
        raise MigrationValidationError(
            "Legacy cumulative meter state exceeds PalPulse at cutover"
        )

    gaps = 0
    large_steps = 0
    for previous, current in zip(eligible, eligible[1:]):
        step = current[2] - previous[2]
        if step < -1e-6:
            raise MigrationValidationError(
                "Legacy cumulative energy decreases. Review before migrating"
            )
        if current[0] != previous[0] + HOUR_SECONDS:
            gaps += 1
        if step > MAX_STEP_WARNING_KWH:
            large_steps += 1

    offset = anchor_sum - eligible[-1][2]
    rows = tuple((ts, state, round(total + offset, 10))
                 for ts, state, total in eligible)
    # Verify no migrated sum exceeds the new first sum. Do not fill any gaps.
    if rows[-1][0] >= cutover or any(r[2] > anchor_sum + 1e-6 for r in rows):
        raise MigrationValidationError("Legacy data cannot be safely anchored")

    encoded = json.dumps([cutover, anchor_state, anchor_sum, rows],
                         separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return MigrationPlan(
        cutover=cutover,
        first_hour=rows[0][0],
        hours=rows,
        digest=digest,
        gaps=gaps,
        large_steps=large_steps,
        anchor_state=anchor_state,
        anchor_sum=anchor_sum,
    )


def validate_undo(expected: list[list], actual: list[dict],
                  start: int, end: int) -> None:
    """Abort undo if any target row differs from the migration journal."""
    expected_by_time = {int(r[0]): (float(r[1]), float(r[2])) for r in expected}
    if len(expected_by_time) != len(expected):
        raise MigrationValidationError("Corrupt migration journal has duplicate rows")
    for row in actual:
        stamp = int(row["start"])
        if not start <= stamp < end or stamp not in expected_by_time:
            raise MigrationValidationError("Unexpected target row in undo time range")
        state, summation = expected_by_time[stamp]
        if (
            not math.isclose(float(row["state"]), state, abs_tol=1e-5)
            or not math.isclose(float(row["sum"]), summation, abs_tol=1e-5)
        ):
            raise MigrationValidationError(
                "Imported row was changed since migration. Undo is blocked"
            )
