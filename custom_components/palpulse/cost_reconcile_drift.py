"""Read-only audit of changed energy during an interrupted cost repair.

Never assume a Recorder energy digest mismatch is harmless. Compare the saved
cost plan against an independent reconstruction from the immutable ORIGINAL
cost rows, the present meter energy and the date-effective tariff. No writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math

from .cost_reconcile_plan import build_cost_plan, energy_fingerprint

TOLERANCE = 1e-5


def same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return math.isfinite(float(a)) and math.isfinite(float(b)) and math.isclose(
            float(a), float(b), rel_tol=0, abs_tol=TOLERANCE,
        )
    except (TypeError, ValueError, OverflowError):
        return False


def audit_changed_energy(saved: dict, energy: list[dict],
                         versions: list[dict], zone_name: str) -> dict:
    """Classify real financial differences, returning specific affected hours.

    Original cost snapshots must be used instead of Recorder's current cost
    totals, because some of those totals were already changed by the repair.
    """
    plan = saved.get("plan") or {}
    start, end = int(plan["start_utc"]), int(plan["end_utc"])
    rows = [
        row for row in plan["rows"]
        if start <= int(row["start"]) < end
    ]
    if not rows:
        raise ValueError("The saved cost journal has no original hours to audit")
    if plan.get("tail_carried_forward"):
        return {
            "status": "manual_review", "can_replan": False,
            "reason": "An earlier repair already carried changes into newer live "
                      "costs. These must be verified before another replan",
            "changed_hours": [], "affected_count": 0,
        }
    original_costs = []
    anchor = plan.get("anchor_utc")
    if anchor is not None:
        if not same(plan.get("anchor_sum"), plan.get("anchor_sum")):
            raise ValueError("Saved historical monetary anchor is invalid")
        original_costs.append({
            "start": int(anchor), "sum": plan["anchor_sum"],
            "state": plan["anchor_sum"],
        })
    elif not same(plan.get("anchor_sum"), 0):
        raise ValueError("Saved historical monetary anchor is absent")
    for row in rows:
        if row.get("old_sum") is None:
            continue
        if not same(row.get("old_sum"), row.get("old_sum")) or not same(
            row.get("old_state"), row.get("old_state")
        ):
            raise ValueError(
                f"Saved original monetary counter is invalid at UTC {row['start']}"
            )
        original_costs.append({
            "start": int(row["start"]),
            "sum": row["old_sum"], "state": row["old_state"],
        })
    new = build_cost_plan(
        energy, original_costs, versions, start, end, zone_name=zone_name,
    )
    expected = {int(row["start"]): row for row in rows}
    current = {int(row["start"]): row for row in new["rows"]}
    changed = []
    for stamp in sorted(set(expected) | set(current)):
        old = expected.get(stamp)
        fresh = current.get(stamp)
        if old is not None and fresh is not None and all(
            same(old.get(k), fresh.get(k))
            for k in ("new_sum", "new_state", "increment", "kwh", "old_sum", "old_state")
        ) and old.get("source") == fresh.get("source"):
            continue
        local = datetime.fromtimestamp(stamp, timezone.utc).astimezone(
            ZoneInfo(zone_name)
        )
        changed.append({
            "start": stamp,
            "local_hour": local.strftime("%d %b %Y, %I:%M %p"),
            "previous_kwh": old.get("kwh") if old else None,
            "current_kwh": fresh.get("kwh") if fresh else None,
            "old_charge": old.get("increment") if old else None,
            "revised_charge": fresh.get("increment") if fresh else None,
            "reason": (
                "Previously absent from the saved monetary plan" if old is None
                else "Newly missing or uncertain metering" if fresh is None
                else "Meter usage or the independently reconstructed charge changed"
            ),
        })
    current_digest = energy_fingerprint(energy, start, end)
    if current_digest == plan.get("energy_digest"):
        return {
            "status": "unchanged", "can_replan": False,
            "reason": "The original energy snapshot is already current",
            "changed_hours": [], "affected_count": 0,
        }
    same_target = not changed
    return {
        "status": "equivalent" if same_target else "material_change",
        "can_replan": (
            bool(new.get("repair_safe"))
            and bool(new.get("baseline_verified"))
            and all(stamp in current for stamp in expected)
        ),
        "same_targets": same_target,
        "affected_count": len(changed),
        "changed_hours": changed[:40],
        "missing_energy_hours": new["missing_energy_hours"],
        "blocked_conflicts": len(new.get("blocking_conflicts", [])),
        "digest": current_digest,
        "reason": (
            "Energy changed, but every saved hourly charge remains identical. "
            "The old plan can be safely revalidated"
            if same_target else
            f"{len(changed)} cost hour(s) now differ. Rebuild the original "
            "cost repair from current energy and verify another backup before "
            "changing any more monetary rows"
        ),
        "_plan": new,
    }
