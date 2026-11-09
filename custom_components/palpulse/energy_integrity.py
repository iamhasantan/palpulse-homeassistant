"""Pure, read-only diagnostics for unexpected PalPulse energy sum reversals.

A raw Powerpal meter-state increase concurrent with a very large negative
Recorder sum change indicates a cumulative baseline discontinuity. This
module only reports candidates; it never changes Recorder statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class EnergySumDiscontinuity:
    hour_start: int
    previous_state: float
    state: float
    previous_sum: float
    summation: float
    meter_delta: float
    recorded_delta: float
    correction: float


def detect_large_energy_resets(
    rows: list[dict], *,
    negative_threshold_kwh: float = 50.0,
    maximum_meter_delta_kwh: float = 100.0,
) -> tuple[EnergySumDiscontinuity, ...]:
    """Find sharp sum decreases contradicted by increasing raw meter states.

    Require consecutive UTC hourly rows, finite numeric values, and a
    plausible raw-state difference. Never guess consumption across a gap.
    """
    output: list[EnergySumDiscontinuity] = []
    ordered = sorted(rows, key=lambda item: item["start"])
    for previous, current in zip(ordered, ordered[1:]):
        try:
            before = int(previous["start"])
            after = int(current["start"])
            old_state, new_state = float(previous["state"]), float(current["state"])
            old_sum, new_sum = float(previous["sum"]), float(current["sum"])
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
        if after != before + 3600 or not all(
            math.isfinite(x) for x in (old_state, new_state, old_sum, new_sum)
        ):
            continue
        state_delta = new_state - old_state
        sum_delta = new_sum - old_sum
        if (
            sum_delta < -negative_threshold_kwh
            and 0 <= state_delta <= maximum_meter_delta_kwh
        ):
            output.append(EnergySumDiscontinuity(
                hour_start=after,
                previous_state=old_state,
                state=new_state,
                previous_sum=old_sum,
                summation=new_sum,
                meter_delta=state_delta,
                recorded_delta=sum_delta,
                correction=state_delta - sum_delta,
            ))
    return tuple(output)
