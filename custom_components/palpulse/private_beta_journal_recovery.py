"""TEMPORARY PRIVATE-BETA COMPATIBILITY: verify stale cost repair journals.

Remove this module and its launch hook before public release after the
private-version journals have completed or been reviewed. Never weaken the
normal tariff revision or Recorder writer checks for new installations.

The 1.0.1b2 journal stores a digest of *all* integration options as its
settings revision, despite the journal depending only on date-effective
electricity pricing. This adapter can rebind the revision ONLY after an
independent audit proves that the current effective tariff, source energy,
original saved rows, pending Recorder writes and backup still agree with
the frozen, signed monetary plan. No cost Recorder mutation happens here.
"""
from __future__ import annotations

import math

from .cost_reconcile_plan import build_cost_plan, energy_fingerprint, HOUR
from .cost_reconcile_runtime import _equal, _read
from .tariff_history import CONF_TARIFF_VERSIONS, captured_tariff, validate_versions
from .panel_settings import revision

MAX_PRIVATE_AUDIT_HOURS = 24 * 100


def _same(a, b):
    return _equal(a, b)


def prove_unchanged_pricing(saved, energy_rows, options, zone_name):
    """Pure read-only financial equivalence test for a frozen 1.0.1b2 plan.

    Cannot trust a revision update, an option name, or a difference of zero
    between overall totals. Every individual originally approved charge must
    still be identical, including TOU/free periods and one daily supply fee.
    """
    plan = saved.get("plan") or {}
    start, end = int(plan["start_utc"]), int(plan["end_utc"])
    rows = [r for r in plan.get("rows", [])
            if start <= int(r["start"]) < end]
    count = (end - start) // HOUR
    if (not rows or len(rows) != count
            or count > MAX_PRIVATE_AUDIT_HOURS
            or any(int(r["start"]) != start + i * HOUR
                   for i, r in enumerate(rows))):
        raise ValueError("Original cost plan does not cover consecutive approved hours")
    if plan.get("tail_carried_forward"):
        raise ValueError("The original repair already extends into later cost hours")
    if not plan.get("repair_safe", False) or not plan.get("baseline_verified", False):
        raise ValueError("Original repair was not independently marked safe")
    if any(r.get("source") != "verified" for r in rows):
        raise ValueError("Saved plan includes energy hours that were not fully verified")
    expected_energy = plan.get("energy_digest")
    fingerprint = energy_fingerprint(energy_rows, start, end)
    if not expected_energy or expected_energy != fingerprint:
        raise ValueError("Energy changed since the original protected repair")

    versions = validate_versions(options.get(CONF_TARIFF_VERSIONS, []))
    if not versions:
        raise ValueError("The original dated tariff history is not available")
    if versions[-1]["options"] != captured_tariff(options):
        raise ValueError("Current settings do not match their newest saved tariff")
    if plan.get("timezone") != zone_name:
        raise ValueError("Home Assistant's billing timezone has changed")

    # Reconstruct only the immutable ORIGINAL monetary rows. Current Recorder
    # rows may already hold some repair targets. Never use them to decide how
    # much the frozen tariff was meant to charge.
    original = []
    anchor = plan.get("anchor_utc")
    if anchor is not None:
        if anchor >= start or not _same(plan.get("anchor_sum"), plan.get("anchor_sum")):
            raise ValueError("Original cumulative monetary anchor cannot be verified")
        original.append({
            "start": int(anchor),
            "sum": float(plan["anchor_sum"]),
            "state": float(plan.get("anchor_state", plan["anchor_sum"])),
        })
    elif not _same(plan.get("anchor_sum"), 0.0):
        raise ValueError("Original monetary baseline is missing")
    for row in rows:
        if row.get("old_sum") is None:
            if row.get("old_state") is not None:
                raise ValueError("Saved cost row has only one original counter")
            continue
        if not (_same(row.get("old_sum"), row.get("old_sum"))
                and _same(row.get("old_state"), row.get("old_state"))):
            raise ValueError("An original cost row has invalid cumulative counters")
        original.append({
            "start": int(row["start"]),
            "sum": float(row["old_sum"]),
            "state": float(row["old_state"]),
        })
    rebuilt = build_cost_plan(
        energy_rows, original, versions, start, end, zone_name=zone_name,
    )
    if (not rebuilt.get("repair_safe")
            or rebuilt["anchor_utc"] != plan.get("anchor_utc")
            or not _same(rebuilt["anchor_sum"], plan.get("anchor_sum"))):
        raise ValueError("The original monetary baseline no longer verifies")
    original_rows = rebuilt["rows"]
    if len(original_rows) != len(rows):
        raise ValueError("The independently priced period changed length")
    for old, new in zip(rows, original_rows, strict=True):
        stamp = int(old["start"])
        if (int(new["start"]) != stamp
                or new["source"] != "verified"
                or old["source"] != new["source"]
                or not all(_same(old.get(key), new.get(key)) for key in (
                    "increment", "kwh", "old_sum", "old_state", "new_sum"
                ))):
            raise ValueError(
                f"Historical price or original energy/charge differs at UTC {stamp}"
            )
        if saved.get("day_recalculation"):
            offset = float(plan["anchor_state"]) - float(plan["anchor_sum"])
            if not _same(old.get("new_state"),
                         round(float(new["new_sum"]) + offset, 9)):
                raise ValueError(
                    f"Day cost state baseline changed at UTC {stamp}"
                )
        elif not _same(old.get("new_state"), new.get("new_state")):
            raise ValueError(
                f"Original cumulative monetary target differs at UTC {stamp}"
            )
    if (not _same(plan.get("last_sum"), rebuilt.get("last_sum"))
            or plan.get("last_supply_date") != rebuilt.get("last_supply_date")
            or plan.get("free_used") != rebuilt.get("free_used")
            or plan.get("free_uncertain_dates") != rebuilt.get("free_uncertain_dates")):
        raise ValueError("Daily supply, free allowance, or final monetary cost differs")
    return {
        "outcome": "verified_same_pricing",
        "hours": len(rows), "energy_unchanged": True,
        "prices_unchanged": True,
        "source": "original_snapshot_and_recorder_energy",
    }


async def verify_private_journal(hass, entry, saved):
    """Read-only comparison of original/target Recorder rows and tariff.

    The caller separately verifies all saved backups and pins the settings
    revision before updating the journal. A fresh revision check and the
    existing writer's locked batch checks still apply at commit time.
    """
    plan = saved.get("plan") or {}
    rows = plan.get("rows") or []
    if not rows:
        raise ValueError("No saved monetary rows to recover")
    importer = hass.data.get("palpulse", {}).get(entry.entry_id)
    writer = hass.data.get("palpulse_cost", {}).get(entry.entry_id)
    if importer is None or writer is None:
        raise ValueError("Meter or cost Recorder is unavailable for verification")
    start, end = int(plan["start_utc"]), int(plan["end_utc"])
    # Wait until previously queued recorder work has completed before asking
    # whether a row is missing, still original, or already exactly corrected.
    from homeassistant.helpers.recorder import get_instance
    await get_instance(hass).async_block_till_done()
    async with importer._lock:
        async with writer._lock:
            records = await _read(
                hass, (importer.statistic_id, writer.statistic_id),
                start - HOUR, end,
            )
            evidence = prove_unchanged_pricing(
                saved, records.get(importer.statistic_id, []),
                entry.options, hass.config.time_zone,
            )
            cost = {
                int(x["start"]): x
                for x in records.get(writer.statistic_id, [])
            }
            counts = {"original": 0, "already_corrected": 0, "originally_missing": 0}
            for row in rows:
                stamped = int(row["start"])
                current = cost.get(stamped)
                if current is None:
                    if row.get("old_sum") is not None:
                        raise ValueError(
                            f"Previously recorded cost vanished at UTC {stamped}"
                        )
                    counts["originally_missing"] += 1
                    continue
                original = (_same(current.get("sum"), row.get("old_sum"))
                            and _same(current.get("state"), row.get("old_state")))
                target = (_same(current.get("sum"), row.get("new_sum"))
                          and _same(current.get("state"), row.get("new_state")))
                if original:
                    counts["original"] += 1
                elif target:
                    counts["already_corrected"] += 1
                else:
                    raise ValueError(
                        f"Unrecognised Recorder cost row at UTC {stamped}; "
                        "the protected journal and backup must be preserved"
                    )
            if saved.get("rows_committed", 0) and not counts["already_corrected"]:
                # The journal's count is a progress marker, not independent
                # proof that every write completed before an HA shutdown.
                # Never discard the journal on the strength of that count.
                evidence["progress_note"] = "Journal progress requires writer recheck"
            return dict(evidence, recorder_rows=counts,
                        saved_revision_changed=True)
