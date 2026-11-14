"""Strict daily cost recalc checks on top of existing Recorder-only plans."""
from __future__ import annotations

import hashlib
import json
import math

from .cost_reconcile_plan import HOUR
from .missing_cost_plan import validate_cost_anchor


def complete_day_plan(plan: dict, cost_rows: list[dict], start: int, end: int) -> dict:
    """Reject missing kWh, stale baselines and unsafe tail handoff, without writes."""
    rows = plan["rows"]
    hours = (end - start) // HOUR
    if (hours not in (23, 24, 25) or plan["start_utc"] != start
            or plan["end_utc"] != end or len(rows) != hours
            or any(row.get("source") != "verified" for row in rows)):
        raise ValueError(
            "Every local billing hour requires matching adjacent Recorder "
            "energy state and sum counters; unknown hours cannot be priced"
        )
    if not plan.get("baseline_verified") or plan.get("blocking_conflicts"):
        raise ValueError("The historical monetary baseline or original cost rows are unsafe")
    if plan.get("anchor_utc") != start - HOUR:
        raise ValueError("Previous billing hour must contain a verified monetary anchor")
    costs = {int(row["start"]): row for row in cost_rows}
    if len(costs) != len(cost_rows):
        raise ValueError("Duplicate original monetary hours")
    state, summed, diagnosis = validate_cost_anchor(costs, start - HOUR)
    if abs(summed - plan["anchor_sum"]) > 1e-5:
        raise ValueError("Cost anchor changed during assessment")
    final = costs.get(end - HOUR)
    if final is None:
        raise ValueError("Original final billing hour is absent, so later costs cannot be rebased safely")
    try:
        old_final_sum, old_final_state = float(final["sum"]), float(final["state"])
    except (KeyError, TypeError, ValueError, OverflowError) as err:
        raise ValueError("Final billing hour contains an invalid monetary counter") from err
    if not math.isfinite(old_final_sum) or not math.isfinite(old_final_state):
        raise ValueError("Final billing hour has non-finite monetary counters")
    offset = state - summed
    if abs((old_final_state - old_final_sum) - offset) > 1e-5:
        raise ValueError("End-of-day state/sum offset differs from the historical anchor")

    hourly = []
    sum_of_known_original = 0.0
    all_old_verified = True
    for row in rows:
        stamp = int(row["start"])
        old, prior = costs.get(stamp), costs.get(stamp - HOUR)
        original = None
        if old is not None and prior is not None:
            try:
                values = [float(old[k]) for k in ("sum", "state", "change")]
                values += [float(prior[k]) for k in ("sum", "state")]
                amount = values[2]
                if (all(math.isfinite(x) for x in values) and amount >= -1e-5
                        and abs(values[0] - values[3] - amount) <= 1e-5
                        and abs(values[1] - values[4] - amount) <= 1e-5):
                    original = max(0.0, amount)
                    sum_of_known_original += original
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        if original is None:
            all_old_verified = False
        hourly.append({
            "start": stamp, "kwh": row["kwh"],
            "original_cost": original, "recalculated_cost": row["increment"],
            "original_sum": row["old_sum"], "recalculated_sum": row["new_sum"],
        })
        row["new_state"] = round(float(row["new_sum"]) + offset, 9)
    plan.update({
        "day_recalculation": True, "repair_safe": True,
        "day_hours": hours, "anchor_state": state, "anchor_diagnostic": diagnosis,
        "original_daily_cost": (
            round(sum_of_known_original, 9) if all_old_verified else None
        ),
        "recalculated_daily_cost": round(
            sum(float(row["increment"]) for row in rows), 9
        ),
        "original_cost_complete": all_old_verified, "hourly_preview": hourly,
        "final_old_sum": old_final_sum, "final_old_state": old_final_state,
    })
    plan["digest"] = hashlib.sha256(json.dumps(
        {k: v for k, v in plan.items() if k != "digest"},
        sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return plan
