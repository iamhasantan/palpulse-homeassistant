"""Convert validated local-day daily-reset monetary states to lifetime sums.

A daily sensor's hourly mean is NOT its closing cost. Require Recorder 'state'
data, a visible midnight reset and complete 23/24/25-hour local billing days.
No historic prices are guessed or inferred from energy measurements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .migration_plan import HOUR_SECONDS, MAX_HOURS, MigrationValidationError


@dataclass(frozen=True)
class DailyCostConversion:
    rows: tuple[dict, ...]
    imported_days: int
    skipped_days: int
    observed_resets: int
    total_cost: float


def convert_daily_reset_costs(
    source_rows: list[dict], cutover: int, timezone_name: str,
) -> DailyCostConversion:
    """Make a stable virtual cumulative source, using only fully observed days.

    Source hourly state is the recorded running daily total at hour end. Days
    with missing hours, intraday decreases, or incomplete first/last hours
    are SKIPPED, never filled. Supports DST days of 23/25 actual hours.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise MigrationValidationError("Invalid local billing timezone") from exc
    if len(source_rows) > MAX_HOURS:
        raise MigrationValidationError("Daily cost source exceeds the safety limit")

    rows: list[tuple[int, float]] = []
    previous_stamp = -1
    for item in sorted(source_rows, key=lambda row: row["start"]):
        try:
            stamp = int(item["start"])
            value = float(item["state"])
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise MigrationValidationError(
                "Daily source lacks genuine hourly closing states. "
                "Price sensors and hourly-mean-only sources cannot be imported"
            ) from exc
        if (stamp < 1_000_000_000 or stamp % HOUR_SECONDS
                or stamp <= previous_stamp or not math.isfinite(value)
                or value < -1e-5):
            raise MigrationValidationError(
                "Daily source has invalid, negative, or duplicate hourly states"
            )
        previous_stamp = stamp
        if stamp < cutover:
            rows.append((stamp, max(0.0, value)))
    if not rows:
        raise MigrationValidationError("No previous daily cost Recorder states")

    days: dict[date, list[tuple[int, float]]] = {}
    for stamp, amount in rows:
        local_day = datetime.fromtimestamp(stamp, timezone.utc).astimezone(zone).date()
        days.setdefault(local_day, []).append((stamp, amount))

    accepted: list[tuple[int, float]] = []
    imported_days = skipped_days = resets = 0
    previous_last: tuple[int, float] | None = None
    for local_day in sorted(days):
        day_rows = days[local_day]
        start = int(datetime.combine(
            local_day, time.min, tzinfo=zone
        ).astimezone(timezone.utc).timestamp())
        end = int(datetime.combine(
            local_day + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(timezone.utc).timestamp())
        expected_stamps = list(range(start, end, HOUR_SECONDS))
        actual_stamps = [stamp for stamp, _ in day_rows]
        if previous_last is not None:
            prev_stamp, prev_amount = previous_last
            if (actual_stamps[0] == prev_stamp + HOUR_SECONDS
                    and day_rows[0][1] < prev_amount - 0.001):
                resets += 1
        previous_last = day_rows[-1]
        if (actual_stamps != expected_stamps
                or any(b[1] < a[1] - 1e-5
                       for a, b in zip(day_rows, day_rows[1:]))):
            skipped_days += 1
            continue
        previous_amount = 0.0
        for stamp, amount in day_rows:
            delta = amount - previous_amount
            if delta < -1e-5:
                raise MigrationValidationError("Negative cost in complete local day")
            accepted.append((stamp, max(0.0, delta)))
            previous_amount = amount
        imported_days += 1

    if resets == 0:
        raise MigrationValidationError(
            "No midnight reset observed. For a lifetime cumulative source, "
            "select cumulative mode, or provide more historical daily data"
        )
    if not accepted:
        raise MigrationValidationError(
            "No fully observed local billing day. Ambiguous periods were skipped"
        )
    running = 0.0
    virtual: list[dict] = []
    for stamp, delta in accepted:
        running = round(running + delta, 10)
        virtual.append({"start": stamp, "state": running, "sum": running})
    return DailyCostConversion(
        rows=tuple(virtual), imported_days=imported_days,
        skipped_days=skipped_days, observed_resets=resets, total_cost=running,
    )
