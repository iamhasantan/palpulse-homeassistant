"""Read-only audit of recent PalPulse hourly Recorder evidence.

Use a conservative grace period before calling any unrecorded hour a gap.
Never fill a missing statistic with zero or infer usage from a chart.
"""
from __future__ import annotations

HOUR = 3600
# Allow the following full hour plus the normal 15-minute replay grace.
SETTLE_SECONDS = 2 * HOUR + 15 * 60


def summarize_recent_hours(
    energy_rows: list[dict],
    cost_rows: list[dict],
    *,
    now: int,
    last_completed_hour: int | None,
    cost_enabled: bool,
    unpriced_hours=(),
    pending_cost_hours=(),
    lookback_hours: int = 72,
) -> dict:
    """Classify only mature hours since observed installation history.

    This is evidence of missing Recorder rows, not an estimate of consumption.
    Known unpriceable and queued costs are not misrepresented as recorded costs.
    """
    due = ((int(now) - SETTLE_SECONDS) // HOUR) * HOUR
    start_limit = due - (max(1, min(168, int(lookback_hours))) - 1) * HOUR
    energy = {int(row["start"]) for row in energy_rows if row.get("start") is not None}
    costs = {int(row["start"]) for row in cost_rows if row.get("start") is not None}
    known = [stamp for stamp in energy if stamp <= due]
    if last_completed_hour is not None and int(last_completed_hour) <= due:
        known.append(int(last_completed_hour))
    result = {
        "status": "collecting",
        "expected_through_utc_hour": due,
        "window_start_utc_hour": None,
        "checked_hours": 0,
        "missing_energy_hours": [],
        "missing_cost_hours": [],
        "queued_cost_hours": [],
        "unpriceable_cost_hours": [],
        "missing_energy_count": 0,
        "missing_cost_count": 0,
        "queued_cost_count": 0,
        "unpriceable_cost_count": 0,
        "cost_enabled": bool(cost_enabled),
        "settle_minutes": SETTLE_SECONDS // 60,
    }
    if not known:
        return result

    first = max(start_limit, min(known))
    if first > due:
        return result
    result["window_start_utc_hour"] = first
    unpriced = {int(stamp) for stamp in unpriced_hours}
    pending = {int(stamp) for stamp in pending_cost_hours}
    for stamp in range(first, due + HOUR, HOUR):
        result["checked_hours"] += 1
        if stamp not in energy:
            result["missing_energy_hours"].append(stamp)
        elif cost_enabled and stamp not in costs:
            if stamp in unpriced:
                result["unpriceable_cost_hours"].append(stamp)
            elif stamp in pending:
                result["queued_cost_hours"].append(stamp)
            else:
                result["missing_cost_hours"].append(stamp)
    for category in ("missing_energy", "missing_cost", "queued_cost", "unpriceable_cost"):
        result[category + "_count"] = len(result[category + "_hours"])
        # Keep the newest actionable timestamps without bloating the sidebar.
        result[category + "_hours"] = result[category + "_hours"][-24:]
    if result["missing_energy_count"] or result["missing_cost_count"] or result["unpriceable_cost_count"]:
        result["status"] = "attention"
    elif result["queued_cost_count"]:
        result["status"] = "recovering"
    else:
        result["status"] = "healthy"
    return result
