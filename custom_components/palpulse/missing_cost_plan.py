"""Auditable missing-cost-only rebase plan. No Home Assistant writes.

Preserve the recorded charge of every existing hourly cost and add only
independently priced missing hours. Both state and sum are rebased forward.
A missing free-window hour is never assumed to have consumed zero kWh.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from .cost_reconcile_plan import _cost_band
from .pricing import CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS, CONF_SUPPLY_CENTS
from .tariff_history import effective_tariff, validate_versions

HOUR = 3600
EPS = 1e-5
MAX_HOURS = 168


def finite(row, field):
    try:
        n = float(row[field])
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, KeyError, OverflowError):
        return None


def _kwh(rows, stamp):
    current, previous = rows.get(stamp), rows.get(stamp - HOUR)
    if current is None or previous is None:
        return None
    state, prior_state = finite(current, "state"), finite(previous, "state")
    total, prior_total = finite(current, "sum"), finite(previous, "sum")
    if None in (state, prior_state, total, prior_total):
        return None
    a, b = state - prior_state, total - prior_total
    if -EPS <= a <= 100 and -EPS <= b <= 100 and abs(a - b) <= EPS:
        return max(0.0, b)
    return None


def _hash(energy, costs, options, begin, finish):
    def records(rows):
        return [
            [stamp, row.get("state"), row.get("sum"), row.get("change")]
            for stamp, row in sorted(rows.items())
            if begin - 24 * HOUR <= stamp <= finish
        ]
    data = [begin, finish, records(energy), records(costs), options]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str,
                                    separators=(",", ":")).encode()).hexdigest()


def build_missing_cost_plan(energy_rows, cost_rows, versions, zone_name, *,
                            last_costed_hour, max_hours=MAX_HOURS):
    """Plan all rebased cumulative rows, or return an explanatory refusal.

    Preserved cost rows use their ORIGINAL finite incremental monetary changes.
    Missing cost rows use genuine meter deltas and the date-effective tariff.
    The next existing monetary row's recorded per-hour change is never
    mistaken for an accumulated charge spanning a missing interval.
    """
    history = validate_versions(versions)
    zone = ZoneInfo(zone_name)
    energy = {int(row["start"]): row for row in energy_rows}
    costs = {int(row["start"]): row for row in cost_rows}
    if len(energy) != len(energy_rows) or len(costs) != len(cost_rows):
        raise ValueError("Duplicate Recorder statistics; manual review required")
    if last_costed_hour is None:
        raise ValueError("No durable monetary checkpoint to rebase")
    end = int(last_costed_hour)
    if end % HOUR:
        raise ValueError("Non-hourly monetary checkpoint")
    start_limit = end - (min(max(24, int(max_hours)), MAX_HOURS) - 1) * HOUR
    earliest = max(start_limit, min(costs) if costs else start_limit)
    # The preceding cost hour is an immutable monetary anchor.
    gaps = [
        stamp for stamp in range(earliest, end + HOUR, HOUR)
        if stamp in energy and stamp not in costs and any(old < stamp for old in costs)
    ]
    if not gaps:
        return {"status": "nothing_to_repair", "missing_hours": 0, "rows": []}
    start = gaps[0]
    anchors = [stamp for stamp in costs if stamp < start]
    if not anchors:
        raise ValueError("Missing cost hours have no earlier monetary anchor")
    anchor_stamp = max(anchors)
    if anchor_stamp != start - HOUR:
        raise ValueError("First missing cost hour lacks an adjacent cost anchor")
    anchor_sum = finite(costs[anchor_stamp], "sum")
    anchor_state = finite(costs[anchor_stamp], "state")
    if anchor_sum is None or anchor_state is None or abs(anchor_sum-anchor_state) > EPS:
        raise ValueError("Original cost anchor is invalid")

    # Confirm all hours in the window, including the hour preceding first gap.
    usage = {}
    for stamp in range(start, end + HOUR, HOUR):
        kwh = _kwh(energy, stamp)
        if kwh is None:
            raise ValueError(
                f"UTC hour {stamp} is missing independently verified kWh. "
                "No monetary correction was prepared"
            )
        usage[stamp] = kwh
    amounts = {}
    meta = {}
    day_first = {}
    for stamp in range(start, end + HOUR, HOUR):
        profile = effective_tariff(history, stamp)
        if profile is None:
            raise ValueError(f"No historical tariff version for UTC {stamp}")
        band, rate, day = _cost_band(stamp, profile)
        if band not in ("free", "paid"):
            raise ValueError(f"Tariff changes within UTC hour {stamp}")
        day_first.setdefault(day, stamp)
        options = profile["options"]
        cap = Decimal(str(options.get(CONF_FREE_CAP_KWH, 0)))
        if band == "free":
            # Independently account for *every earlier free-window hour* that
            # contributed to this local day's 15-kWh allocation.
            earliest_today = datetime.fromisoformat(day).replace(tzinfo=zone)
            day_start = int(earliest_today.timestamp())
            consumed = Decimal(0)
            for earlier in range(day_start, stamp, HOUR):
                earlier_profile = effective_tariff(history, earlier)
                if earlier_profile is None:
                    raise ValueError(f"Unknown historical free allocation on {day}")
                earlier_band, _, earlier_day = _cost_band(earlier, earlier_profile)
                if earlier_day == day and earlier_band == "free":
                    amount = _kwh(energy, earlier)
                    if amount is None:
                        raise ValueError(
                            f"Unverified earlier free-window kWh on {day}; "
                            "missing cost cannot be allocated safely"
                        )
                    consumed += Decimal(str(amount))
            amount = Decimal(str(usage[stamp]))
            excess = (max(Decimal(0), consumed + amount - cap)
                      - max(Decimal(0), consumed - cap)) if cap else Decimal(0)
            base = excess * Decimal(str(options.get(CONF_FREE_EXCESS_CENTS, 0))) / 100
        else:
            base = Decimal(str(usage[stamp])) * Decimal(str(rate))
        amounts[stamp] = base
        meta[stamp] = {"day": day, "band": band, "supply": Decimal(str(
            options.get(CONF_SUPPLY_CENTS, 0))) / 100}

    # Supply is already included if the first repair day had a costed hour
    # before the monetary anchor. For later days prefer the first ORIGINAL
    # row whose own change independently proves it carried the supply fee.
    supply_owner = {}
    for day in day_first:
        first = day_first[day]
        local_anchor_day = datetime.fromtimestamp(anchor_stamp, timezone.utc).astimezone(zone).date().isoformat()
        if day == local_anchor_day:
            earlier_today = [
                t for t in costs if t < start and
                datetime.fromtimestamp(t, timezone.utc).astimezone(zone).date().isoformat() == day
            ]
            if not earlier_today:
                raise ValueError("First partial billing day lacks a proved earlier supply charge")
            supply_owner[day] = None
            continue
        candidate = None
        for stamp in range(first, end + HOUR, HOUR):
            if meta[stamp]["day"] != day or stamp not in costs:
                continue
            old_change = finite(costs[stamp], "change")
            expected = amounts[stamp] + meta[stamp]["supply"]
            if old_change is not None and abs(old_change - float(expected)) < EPS:
                candidate = stamp
                break
        # If a day already has original cost rows but no row proves
        # whether the supply fee was applied, adding it again could double
        # bill. Refuse instead of guessing its placement.
        has_original = any(
            stamp in costs and meta[stamp]["day"] == day
            for stamp in range(first, end + HOUR, HOUR)
        )
        if has_original and candidate is None:
            raise ValueError(
                f"Daily supply charge on {day} cannot be located in the "
                "original monetary rows; manual review required"
            )
        supply_owner[day] = candidate if candidate is not None else first

    running = Decimal(str(anchor_sum))
    plan_rows = []
    missing_total = Decimal(0)
    for stamp in range(start, end + HOUR, HOUR):
        old = costs.get(stamp)
        day = meta[stamp]["day"]
        intended = amounts[stamp]
        if supply_owner[day] == stamp:
            intended += meta[stamp]["supply"]
        if old is None:
            charge = intended
            missing_total += charge
            source = "meter_tariff"
        else:
            state, total = finite(old, "state"), finite(old, "sum")
            if state is None or total is None or abs(state - total) > EPS:
                raise ValueError(f"Invalid existing cost row at UTC {stamp}")
            observed_change = finite(old, "change")
            if observed_change is None or observed_change < -EPS:
                raise ValueError(f"Cannot establish original hourly cost at UTC {stamp}")
            prior = costs.get(stamp - HOUR)
            if prior is not None:
                prior_sum = finite(prior, "sum")
                if prior_sum is None or abs((total - prior_sum) - observed_change) > EPS:
                    raise ValueError(
                        f"Original cost change at UTC {stamp} disagrees with "
                        "consecutive Recorder monetary sums"
                    )
            elif Decimal(str(observed_change)) > intended + Decimal(str(EPS)):
                # Recorder's reported change immediately after a hole might
                # include an unjournalled catch-up amount. Charging all
                # missing hours again would double bill that catch-up.
                raise ValueError(
                    f"Existing cost at UTC {stamp} may include missing-hour "
                    "catch-up. Automatic cumulative repair refused"
                )
            charge = Decimal(str(max(0.0, observed_change)))
            # The old monetary row is left as an *increment*; only its
            # cumulative baseline may shift due to independently proven gaps.
            source = "original_charge_retained"
        running += charge
        target = round(float(running), 9)
        plan_rows.append({
            "start": stamp,
            "old_sum": finite(old, "sum") if old else None,
            "old_state": finite(old, "state") if old else None,
            "new_sum": target, "new_state": target,
            "charge": round(float(charge), 9),
            "kwh": round(usage[stamp], 9),
            "source": source, "day": day,
        })
    if not any(row["source"] == "meter_tariff" for row in plan_rows):
        return {"status": "nothing_to_repair", "missing_hours": 0, "rows": []}
    return {
        "status": "ready",
        "start_utc": start, "end_utc": end + HOUR,
        "anchor_utc": anchor_stamp, "anchor_sum": anchor_sum,
        "missing_hours": len(gaps),
        "missing_charges": round(float(missing_total), 9),
        "rows": plan_rows, "last_sum": plan_rows[-1]["new_sum"],
        "digest": _hash(energy, costs, history, start, end),
        "tariff_version_count": len(history),
        "preserved_existing_hours": len(plan_rows)-len(gaps),
    }
