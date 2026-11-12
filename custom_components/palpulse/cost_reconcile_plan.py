"""Pure, best-effort hourly electricity cost reconciliation.

A tariff effective date selects the rate in force for each local billing day.
Use the actual Recorder energy *sum changes*, not event arrival time. The
11:00-16:00 free cap is counted across each local day. A free-window gap
makes subsequent free allocations uncertain, but never blocks unrelated
paid-rate hours. Ambiguous prices retain legacy cost entries with a warning.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
from typing import Mapping
from zoneinfo import ZoneInfo

from .pricing import (
    CONF_PRICING_MODE, CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS,
    CONF_SUPPLY_CENTS, MODE_HOME_ASSISTANT,
    free_window_active, price_for_datetime, validate_pricing,
)
from .tariff_history import effective_tariff, validate_versions

HOUR = 3600
MAX_HOURS = 14 * 24 + 1
MAX_HOURLY_KWH = 100.0
TOLERANCE = 1e-5


def billing_range(start_day: str, end_day: str, zone_name: str,
                  *, today: date | None = None) -> tuple[int, int]:
    """Inclusive local dates, exclusive UTC end, max 14 days."""
    try:
        start, end = date.fromisoformat(start_day), date.fromisoformat(end_day)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select valid YYYY-MM-DD reconciliation dates") from exc
    if start.isoformat() != start_day or end.isoformat() != end_day:
        raise ValueError("Reconciliation dates must be YYYY-MM-DD")
    zone = ZoneInfo(zone_name)
    if today is None:
        today = datetime.now(zone).date()
    if end > today or end < start or (end - start).days > 13:
        raise ValueError("Use up to fourteen billing days, ending no later than today")
    since = datetime(start.year, start.month, start.day, tzinfo=zone)
    until_day = end + timedelta(days=1)
    until = datetime(until_day.year, until_day.month, until_day.day, tzinfo=zone)
    return int(since.timestamp()), int(until.timestamp())


def _num(row: Mapping, key: str) -> float | None:
    try:
        value = float(row[key])
        return value if math.isfinite(value) else None
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def _cost_band(utc_start: int, profile: Mapping) -> tuple[str, float | None, str]:
    options = profile["options"]
    zone = ZoneInfo(profile["timezone"])
    validate_pricing(options)
    band = None
    bill_date = None
    for minute in range(60):
        current = datetime.fromtimestamp(
            utc_start + minute * 60, timezone.utc
        ).astimezone(zone)
        local_day = current.date().isoformat()
        if bill_date is None:
            bill_date = local_day
        if local_day != bill_date:
            return "mixed_day", None, bill_date
        if free_window_active(options, current):
            candidate = ("free", None)
        else:
            rate = price_for_datetime(options, current)
            candidate = ("paid", rate)
        if band is None:
            band = candidate
        if candidate != band:
            return "mixed_band", None, bill_date
    return band[0], band[1], bill_date


def energy_fingerprint(rows: list[dict], start_utc: int, end_utc: int) -> str:
    """Immutable hash of raw Recorder energy across the correction interval."""
    values = sorted(
        (
            int(row["start"]), row.get("state"),
            row.get("sum"), row.get("change"),
        )
        for row in rows if start_utc - HOUR <= int(row["start"]) < end_utc
    )
    return hashlib.sha256(
        json.dumps(values, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def build_cost_plan(
    energy_rows: list[dict], cost_rows: list[dict], versions: list[dict],
    start_utc: int, end_utc: int, *, zone_name: str,
) -> dict:
    """Build immutable input/output row snapshots. Never write to Recorder.

    'verified' requires adjacent energy state+sum agreement.
    'recorded_sum' uses a nonnegative Recorder hourly change after a gap;
    the amount is explicitly labelled less certain. Missing hours never
    manufacture energy. Incomplete free-cap dates stay unpriced.
    """
    if (
        start_utc % HOUR or end_utc % HOUR
        or end_utc <= start_utc or end_utc - start_utc > MAX_HOURS * HOUR
    ):
        raise ValueError("Expected a bounded UTC-hour-aligned interval")
    history = validate_versions(versions)
    if not history:
        raise ValueError("Save the retailer's real tariff effective date first")
    ZoneInfo(zone_name)
    energies = {int(row["start"]): row for row in energy_rows}
    costs = {int(row["start"]): row for row in cost_rows}
    if len(energies) != len(energy_rows) or len(costs) != len(cost_rows):
        raise ValueError("Recorder returned duplicate statistic timestamps")
    earlier_costs = sorted(
        ((stamp, row) for stamp, row in costs.items() if stamp < start_utc),
        key=lambda x: x[0], reverse=True,
    )
    # A historical cost migration may preserve a signed cumulative offset.
    # It is not proof of a broken meter or invalid arithmetic. Never replace
    # an existing negative *finite* baseline with zero, which would create a
    # discontinuity and corrupt every subsequent recorded monetary total.
    anchor = next(
        ((stamp, row) for stamp, row in earlier_costs
         if _num(row, "sum") is not None),
        None,
    )
    baseline = _num(anchor[1], "sum") if anchor else 0.0
    baseline_issues = []
    baseline_verified = True
    if earlier_costs and anchor is None:
        baseline_verified = False
        baseline_issues.append(
            "Earlier Recorder cost rows exist, but none have a finite sum. "
            "Preview is available; historical writes are blocked until "
            "the corrupted monetary baseline is reconciled."
        )
    elif earlier_costs and anchor[0] != earlier_costs[0][0]:
        baseline_verified = False
        baseline_issues.append(
            "Latest earlier cost row has an invalid cumulative sum. "
            "An older valid anchor was used for the preview only. "
            "Do not apply before reviewing the discontinuity."
        )
    elif baseline < 0:
        baseline_issues.append(
            "The saved historical monetary baseline is negative. "
            "Its signed offset is preserved, never silently reset. "
            "Verify that this is a legitimate migration/credit before applying."
        )
    old_prior = float(baseline) if baseline_verified else None
    running = Decimal(str(baseline))
    free_used: dict[str, Decimal] = {}
    free_unknown: set[str] = set()
    supply_dates: set[str] = set()
    issues = [{"start": None, "reason": msg} for msg in baseline_issues]
    row_plans = []
    summaries: dict[str, dict] = {}
    missing_energy = 0
    safe_rows = 0
    retained_rows = 0

    for stamp in range(start_utc, end_utc, HOUR):
        local = datetime.fromtimestamp(stamp, timezone.utc).astimezone(
            ZoneInfo(zone_name)
        )
        day = local.date().isoformat()
        daily = summaries.setdefault(day, {
            "day": day, "recomputed": 0.0, "recorded": 0.0,
            "verified_hours": 0, "retained_hours": 0, "missing_hours": 0,
        })
        old = costs.get(stamp)
        old_sum = _num(old, "sum") if old is not None else None
        old_state = _num(old, "state") if old is not None else None
        if old is not None and (
            old_sum is None or old_state is None
            or (old_prior is not None and old_sum < old_prior - TOLERANCE)
        ):
            if not baseline_verified and old_sum is not None and old_state is not None:
                # The first trustworthy cost row is a usable future anchor,
                # but no charge can be inferred for the intervening interval.
                issues.append({
                    "start": stamp,
                    "reason": "Existing cost baseline cannot be verified",
                })
                old_prior = old_sum
            else:
                raise ValueError(f"Invalid existing cost counter at UTC {stamp}")
        old_increment = None
        if old_sum is not None:
            if old_prior is not None:
                old_increment = max(0.0, old_sum - old_prior)
                daily["recorded"] += old_increment
            else:
                issues.append({
                    "start": stamp,
                    "reason": "First cost row has no independently verified preceding total",
                })
            old_prior = old_sum

        energy = energies.get(stamp)
        previous = energies.get(stamp - HOUR)
        kwh = None
        quality = "missing"
        if energy is not None:
            current_sum = _num(energy, "sum")
            current_state = _num(energy, "state")
            previous_sum = _num(previous, "sum") if previous else None
            previous_state = _num(previous, "state") if previous else None
            observed_change = _num(energy, "change")
            if current_sum is not None and previous_sum is not None:
                delta = current_sum - previous_sum
                if (
                    -TOLERANCE <= delta <= MAX_HOURLY_KWH
                    and current_state is not None and previous_state is not None
                    and abs((current_state - previous_state) - delta) <= TOLERANCE
                ):
                    kwh, quality = max(0.0, delta), "verified"
                elif (
                    observed_change is not None
                    and -TOLERANCE <= observed_change <= MAX_HOURLY_KWH
                    and abs(delta - observed_change) <= TOLERANCE
                ):
                    kwh, quality = max(0.0, delta), "recorded_sum"
            elif (
                current_sum is not None and observed_change is not None
                and -TOLERANCE <= observed_change <= MAX_HOURLY_KWH
            ):
                kwh, quality = max(0.0, observed_change), "recorded_sum"

        profile = effective_tariff(history, stamp)
        if profile is None:
            raise ValueError(
                f"No dated tariff applies to {day}; adjust the plan start date"
            )
        options = profile["options"]
        mode = options.get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT)
        if mode == MODE_HOME_ASSISTANT:
            raise ValueError("Custom pricing must be enabled to reprice history")
        band, rate, band_day = _cost_band(stamp, profile)
        if band_day != day:
            issues.append({
                "start": stamp, "reason": "Profile timezone differs from Home Assistant",
            })
            band = "mixed_day"

        cost = None
        if kwh is None:
            missing_energy += 1
            daily["missing_hours"] += 1
            issues.append({"start": stamp, "reason": "No independently usable energy measurement"})
            if band == "free":
                free_unknown.add(day)
        elif band == "free":
            if day in free_unknown:
                issues.append({"start": stamp, "reason": "Daily free allowance unknown after missing data"})
            else:
                cap = Decimal(str(options.get(CONF_FREE_CAP_KWH, 0)))
                previously = free_used.get(day, Decimal(0))
                amount = Decimal(str(kwh))
                excess = max(Decimal(0), previously + amount - cap) - max(
                    Decimal(0), previously - cap,
                ) if cap else Decimal(0)
                cost = excess * Decimal(str(options.get(CONF_FREE_EXCESS_CENTS, 0))) / 100
                free_used[day] = previously + amount
        elif band == "paid":
            cost = Decimal(str(kwh)) * Decimal(str(rate))
        else:
            issues.append({"start": stamp, "reason": "Hour spans multiple tariff bands or billing dates"})

        if cost is not None and day not in supply_dates:
            cost += Decimal(str(options.get(CONF_SUPPLY_CENTS, 0))) / 100
            supply_dates.add(day)
        if cost is None:
            charge = Decimal(str(old_increment if old_increment is not None else 0))
            if old is not None:
                retained_rows += 1
                daily["retained_hours"] += 1
            else:
                # A missing new hour remains absent from the target statistic.
                continue
            source = "legacy_retained"
        else:
            charge = cost
            safe_rows += 1
            daily["verified_hours"] += 1
            source = quality

        running += charge
        daily["recomputed"] += float(charge)
        target = round(float(running), 9)
        row_plans.append({
            "start": stamp, "old_sum": old_sum, "old_state": old_state,
            "new_sum": target, "new_state": target,
            "increment": round(float(charge), 9), "source": source,
            "kwh": None if kwh is None else round(kwh, 9),
            "date": day,
        })
    result = {
        "start_utc": start_utc, "end_utc": end_utc, "timezone": zone_name,
        "energy_digest": energy_fingerprint(energy_rows, start_utc, end_utc),
        "anchor_utc": anchor[0] if anchor else None,
        "anchor_sum": float(baseline),
        "baseline_verified": baseline_verified,
        "baseline_issues": baseline_issues,
        "rows": row_plans,
        "day_totals": [summaries[key] for key in sorted(summaries)],
        "issues": issues, "verified_hours": safe_rows,
        "retained_hours": retained_rows, "missing_energy_hours": missing_energy,
        "free_uncertain_dates": sorted(free_unknown),
        "last_sum": round(float(running), 9),
        "free_used": {key: float(value) for key, value in free_used.items()},
        "last_supply_date": max(supply_dates) if supply_dates else None,
    }
    fingerprint = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    result["digest"] = hashlib.sha256(fingerprint.encode()).hexdigest()
    return result
