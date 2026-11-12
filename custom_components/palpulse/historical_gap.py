"""Read-only plans for a single skipped UTC-hour PalPulse energy statistic.

The live importer used to omit an incomplete hour, then resume with the
next verified hour's consumption only. Its raw meter state still advanced.
The *difference* between physical counter movement and recorded sum movement
therefore proves the unaccounted consumption, for exactly one missing hour.

Plans are NOT authority to write. The writer must re-read and compare the
source rows under a lock, create a verified Recorder-inclusive backup,
journal the repair and verify every Recorder operation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Iterable, Mapping

HOUR_SECONDS = 3600
TOLERANCE_KWH = 1e-5
MAX_SINGLE_HOUR_KWH = 100.0


@dataclass(frozen=True)
class GapRepairPlan:
    gap_start: int
    prior_start: int
    following_start: int
    prior_sum: float
    following_sum: float
    following_state: float
    missing_kwh: float
    inferred_gap_state: float
    inferred_gap_sum: float
    adjusted_following_sum: float

    def as_dict(self) -> dict:
        return asdict(self)


def plan_recoverable_gaps(
    raw_rows: Iterable[Mapping[str, object]],
) -> list[GapRepairPlan]:
    """List only isolated gaps proven by matching adjacent raw states and sums.

    Two or more missing hours cannot be assigned by the counter to individual
    clock hours. Discontinuities, already-corrected sums and implausibly large
    differences are intentionally excluded.
    """
    rows = sorted(raw_rows, key=lambda item: int(item["start"]))
    plans = []
    previous = None
    by_stamp = {int(row["start"]): row for row in rows}
    if len(by_stamp) != len(rows):
        return []

    for following in rows:
        if previous is None:
            previous = following
            continue
        prior = previous
        previous = following
        start = int(prior["start"])
        finish = int(following["start"])
        if finish - start != 2 * HOUR_SECONDS:
            continue
        try:
            prior_state = float(prior["state"])
            next_state = float(following["state"])
            prior_sum = float(prior["sum"])
            next_sum = float(following["sum"])
            raw_delta = next_state - prior_state
            imported_next = next_sum - prior_sum
            missing = raw_delta - imported_next
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not all(map(math.isfinite, (
            prior_state, next_state, prior_sum, next_sum,
            raw_delta, imported_next, missing,
        ))):
            continue
        if (
            raw_delta <= 0
            or imported_next < -TOLERANCE_KWH
            or missing <= TOLERANCE_KWH
            or missing > MAX_SINGLE_HOUR_KWH
            or next_state < prior_state
        ):
            continue

        # The adjacent preserved hours must behave as ordinary cumulative
        # meter reads. This avoids crossing a change in calibration or a
        # previously applied Recorder sum correction.
        coherent = True
        for older_start, newer_start in (
            (start - HOUR_SECONDS, start),
            (finish, finish + HOUR_SECONDS),
        ):
            older = by_stamp.get(older_start)
            newer = by_stamp.get(newer_start)
            if older is None or newer is None:
                coherent = False
                break
            try:
                d_state = float(newer["state"]) - float(older["state"])
                d_sum = float(newer["sum"]) - float(older["sum"])
                if not (
                    math.isfinite(d_state) and math.isfinite(d_sum)
                    and d_state >= -TOLERANCE_KWH
                    and d_sum >= -TOLERANCE_KWH
                    and math.isclose(
                        d_state, d_sum, abs_tol=TOLERANCE_KWH, rel_tol=0
                    )
                ):
                    coherent = False
                    break
            except (KeyError, ValueError, TypeError, OverflowError):
                coherent = False
                break
        if not coherent:
            continue

        gap_state = next_state - imported_next
        gap_sum = prior_sum + missing
        if gap_state < prior_state - TOLERANCE_KWH:
            continue
        plans.append(GapRepairPlan(
            gap_start=start + HOUR_SECONDS,
            prior_start=start,
            following_start=finish,
            prior_sum=prior_sum,
            following_sum=next_sum,
            following_state=next_state,
            missing_kwh=round(missing, 9),
            inferred_gap_state=round(gap_state, 9),
            inferred_gap_sum=round(gap_sum, 9),
            adjusted_following_sum=round(next_sum + missing, 9),
        ))
    return plans
