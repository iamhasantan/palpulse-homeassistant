"""Build conservative hourly cost history from a daily-reset monetary sensor.

The source's Recorder hourly *state* is a recorded running total for the
local billing day, not a lifetime cumulative total. We never stitch this
sensor to a lifetime counter or apply today's tariff to past consumption.

The hour containing a recorded increase receives that increase. If a day's
first local-midnight hour is absent, one of its hours is missing, or its
daily running total falls within that day, the whole ambiguous day is skipped.
This is observed historical cost, not verified retail billing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .migration_plan import (
    HOUR_SECONDS, MAX_HOURS, MigrationPlan, MigrationValidationError,
    _read_rows,
)

@dataclass(frozen=True)
class DailyCostPlan(MigrationPlan):
    """Migration result with daily-reset source diagnostics."""

    skipped_days: int = 0
    detected_midnight_resets: int = 0
    imported_days: int = 0


def _read_daily_states(rows: list[dict]) -> list[tuple[int, float]]:
    if len(rows) > MAX_HOURS:
        raise MigrationValidationError("Daily cost history exceeds the safety limit")
    result = []
    previous = -1
    for row in sorted(rows, key=lambda r: r["start"]):
        try:
            stamp = int(row["start"])
            state = float(row["state"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MigrationValidationError(
                "Daily cost source needs Recorder hourly states, not price entities "
                "or statistics containing only averages"
            ) from exc
        if (
            stamp < 1_000_000_000 or stamp % HOUR_SECONDS or stamp <= previous
            or not math.isfinite(state) or state < -0.00001
        ):
            raise MigrationValidationError("Daily cost source has invalid hourly states")
        result.append((stamp, max(0.0, state)))
        previous = stamp
    return result


def plan_daily_cost_migration(
    source_rows: list[dict], target_rows: list[dict], timezone_name: str,
) -> DailyCostPlan:
    """Plan an opt-in daily-reset source import without inventing missing costs.

    Requires actual evidence of a local-midnight reset. The first imported
    local day must start at 00:00, with consecutive UTC hours until its last
    observation. Source state is assumed to be that hour's latest daily total.
    Imported cost rows remain strictly before the existing target baseline.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise MigrationValidationError("Invalid local billing timezone") from exc
    target = _read_rows(target_rows, "PalPulse historical cost target")
    if not target:
        raise MigrationValidationError(
            "PalPulse has no cost baseline yet. Configure a custom electricity "
            "tariff and wait for a verified live cost hour before importing"
        )
    cutover, anchor_state, anchor_sum = target[0]
    if abs(anchor_state) > 1e-6 or abs(anchor_sum) > 1e-6:
        raise MigrationValidationError("PalPulse cost target has no zero-sum baseline")
    source = _read_daily_states(source_rows)
    eligible = [(stamp, value) for stamp, value in source if stamp < cutover]
    if not eligible:
        raise MigrationValidationError("No older daily cost Recorder states found")

    by_day: dict[str, list[tuple[int, float]]] = {}
    for stamp, value in eligible:
        local = datetime.fromtimestamp(stamp, timezone.utc).astimezone(zone)
        by_day.setdefault(local.date().isoformat(), []).append((stamp, value))

    accepted: list[tuple[int, float]] = []
    skipped = 0
    resets = 0
    previous_last: tuple[str, float, int] | None = None
    for local_date, day in sorted(by_day.items()):
        day.sort()
        first_local = datetime.fromtimestamp(
            day[0][0], timezone.utc
        ).astimezone(zone)
        if previous_last is not None:
            prior_date, prior_value, prior_stamp = previous_last
            if (
                day[0][0] == prior_stamp + HOUR_SECONDS
                and day[0][1] < prior_value - 0.001
            ):
                resets += 1
        previous_last = (local_date, day[-1][1], day[-1][0])
        if (
            first_local.hour != 0
            or any(b[0] != a[0] + HOUR_SECONDS for a, b in zip(day, day[1:]))
            or any(b[1] < a[1] - 1e-5 for a, b in zip(day, day[1:]))
        ):
            skipped += 1
            continue
        previous = 0.0
        for stamp, value in day:
            delta = value - previous
            if delta < -1e-5:
                raise MigrationValidationError("Daily cost unexpectedly decreased")
            accepted.append((stamp, max(0.0, delta)))
            previous = value

    if not accepted:
        raise MigrationValidationError(
            "No complete contiguous local-day cost history found. "
            "Ambiguous days were skipped; no costs will be guessed"
        )
    if resets == 0:
        raise MigrationValidationError(
            "No midnight reset was visible. Source may be a lifetime "
            "cumulative cost sensor, not a daily-reset cost sensor"
        )

    # At the start of the existing PalPulse cost series, the cumulative sum
    # must still be zero. Shift backward by the total of *observed* deltas.
    total = math.fsum(delta for _, delta in accepted)
    rolling = -total
    rows: list[tuple[int, float, float]] = []
    for stamp, delta in accepted:
        rolling += delta
        amount = round(rolling, 10)
        rows.append((stamp, amount, amount))
    if rows[-1][0] >= cutover or any(row[2] > anchor_sum + 1e-5 for row in rows):
        raise MigrationValidationError("Daily cost history exceeds target cutover")
    gaps = sum(
        b[0] != a[0] + HOUR_SECONDS
        for a, b in zip(rows, rows[1:])
    )
    digest = hashlib.sha256(json.dumps(
        [cutover, anchor_state, anchor_sum, timezone_name, rows],
        separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    return DailyCostPlan(
        cutover=cutover,
        first_hour=rows[0][0],
        hours=tuple(rows),
        digest=digest,
        gaps=gaps,
        large_steps=sum(delta > 50 for _, delta in accepted),
        decreases=(),
        anchor_state=anchor_state,
        anchor_sum=anchor_sum,
        skipped_days=skipped,
        detected_midnight_resets=resets,
        imported_days=len({
            datetime.fromtimestamp(ts, timezone.utc).astimezone(zone).date()
            for ts, _ in accepted
        }),
    )
