"""Audit old-format partial cost repairs before resuming Recorder writes.

Do not assume the absence of a newer repair_safe flag means a repair is
unsafe. Do not trust its absence either. Reprice every saved intended write
from the original cost snapshots, actual hourly energy and dated contract.
No Recorder, NVS or repair journal mutation occurs in this module.
"""
from __future__ import annotations

import math

from .cost_reconcile_plan import build_cost_plan, energy_fingerprint

EPSILON = 1e-5


class UnsafeLegacyRepair(ValueError):
    """A saved repair cannot independently be proven safe."""


def _same(left: object, right: object) -> bool:
    try:
        a, b = float(left), float(right)
        return math.isfinite(a) and math.isfinite(b) and math.isclose(
            a, b, abs_tol=EPSILON, rel_tol=0,
        )
    except (TypeError, ValueError, OverflowError):
        return False


def audit_legacy_repair(
    plan: dict, energy_rows: list[dict], tariff_versions: list[dict],
    *, zone_name: str,
) -> dict:
    """Independently verify an original partial repair and all of its hours."""
    start, end = int(plan["start_utc"]), int(plan["end_utc"])
    saved_rows = [
        row for row in plan.get("rows", [])
        if start <= int(row["start"]) < end
    ]
    if not saved_rows:
        raise UnsafeLegacyRepair("Saved repair has no original cost hours")
    stamps = [int(row["start"]) for row in saved_rows]
    if stamps != sorted(set(stamps)):
        raise UnsafeLegacyRepair("Saved cost hours are not uniquely ordered")
    if energy_fingerprint(energy_rows, start, end) != plan.get("energy_digest"):
        raise UnsafeLegacyRepair(
            "Energy changed since the original repair. Leave its saved "
            "backup and partial Recorder rows unchanged"
        )

    original_costs = []
    anchor_at, anchor_sum = plan.get("anchor_utc"), plan.get("anchor_sum")
    if anchor_at is not None:
        if (int(anchor_at) >= start or int(anchor_at) % 3600
                or not _same(anchor_sum, anchor_sum)):
            raise UnsafeLegacyRepair("Original monetary baseline is invalid")
        original_costs.append({
            "start": int(anchor_at),
            "state": float(anchor_sum), "sum": float(anchor_sum),
        })
    elif not _same(anchor_sum, 0):
        raise UnsafeLegacyRepair("Original monetary baseline is missing")

    for row in saved_rows:
        old_sum, old_state = row.get("old_sum"), row.get("old_state")
        if old_sum is None and old_state is None:
            continue
        if old_sum is None or old_state is None or not (
            _same(old_sum, old_sum) and _same(old_state, old_state)
        ):
            raise UnsafeLegacyRepair(
                f"Cost at UTC {row['start']} has an invalid original value"
            )
        original_costs.append({
            "start": int(row["start"]),
            "state": float(old_state), "sum": float(old_sum),
        })

    independently_priced = build_cost_plan(
        energy_rows, original_costs, tariff_versions,
        start, end, zone_name=zone_name,
    )
    if not independently_priced.get("repair_safe", False):
        blocked = independently_priced.get("blocking_conflicts", [])
        first = blocked[0] if blocked else None
        detail = (
            f" First unsafe UTC hour {first['start']}: {first['reason']}"
            if first else ""
        )
        raise UnsafeLegacyRepair(
            f"Old repair contains {len(blocked)} unverified monetary break(s)."
            f"{detail} No saved Recorder rows were modified"
        )
    rebuilt_rows = independently_priced["rows"]
    if len(rebuilt_rows) != len(saved_rows):
        raise UnsafeLegacyRepair(
            f"Original plan has {len(saved_rows)} write hours but independently "
            f"verified pricing has {len(rebuilt_rows)}. No changes made"
        )
    for old, fresh in zip(saved_rows, rebuilt_rows):
        stamp = int(old["start"])
        if stamp != int(fresh["start"]):
            raise UnsafeLegacyRepair(
                f"Original cost-hour alignment changed at UTC {stamp}"
            )
        if old.get("source") != fresh.get("source"):
            raise UnsafeLegacyRepair(
                f"Original billing evidence differs at UTC {stamp}: "
                f"{old.get('source')} versus {fresh.get('source')}. "
                "No Recorder changes were made"
            )
        for field in ("old_sum", "old_state", "new_sum",
                      "new_state", "increment"):
            a, b = old.get(field), fresh.get(field)
            if a is None or b is None:
                if a is not None or b is not None:
                    raise UnsafeLegacyRepair(
                        f"Old {field} disagrees at UTC {stamp}"
                    )
            elif not _same(a, b):
                raise UnsafeLegacyRepair(
                    f"Original {field} differs from independent tariff "
                    f"reconstruction at UTC {stamp}"
                )
    if not _same(plan.get("last_sum"), saved_rows[-1]["new_sum"]):
        raise UnsafeLegacyRepair("Saved final monetary amount is inconsistent")
    return {
        "verified": True,
        "checked_hours": len(saved_rows),
        "conflicts_checked": len(
            independently_priced.get("cost_counter_conflicts", []),
        ),
        "message": "Original hourly costs independently verified for resume",
    }
