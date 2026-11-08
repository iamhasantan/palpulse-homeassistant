"""Pure accounting for timestamped Powerpal native-minute tariff costs.

Cost is calculated from each proven minute's pulse count and the LOCAL time of
that measurement, never from the hour when the HA event happened to arrive.
The first sample is interpreted as the start of its Powerpal minute. The
retailer's interval convention needs checking against the actual bill.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from .pricing import (
    CONF_PRICING_MODE, MODE_HOME_ASSISTANT,
    daily_supply_dollars, price_for_datetime,
)


def is_cost_enabled(options: dict) -> bool:
    """No costs are generated in Home Assistant's default pricing mode."""
    return options.get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT) != MODE_HOME_ASSISTANT


def cost_for_verified_hour(
    samples: Iterable,
    calibration: float,
    options: dict,
    time_zone: str,
) -> tuple[float, frozenset[str]]:
    """Return (usage dollars, local billing dates observed) for a proven hour.

    Caller MUST supply exactly the 60 validated, consecutive native-minute
    records from the verified hour. This function independently checks the
    minute count/timestamps to prevent accidental pricing of partial hours.
    """
    if calibration <= 0:
        raise ValueError("Invalid meter calibration")
    minute_samples = sorted(samples, key=lambda sample: sample.timestamp)
    if len(minute_samples) != 60:
        raise ValueError("Only complete native-minute hours can be priced")
    first = minute_samples[0].timestamp
    if any(
        sample.timestamp != first + i * 60
        for i, sample in enumerate(minute_samples)
    ):
        raise ValueError("Non-consecutive records cannot be priced")
    local_zone = ZoneInfo(time_zone)
    total = Decimal("0")
    observed_dates: set[str] = set()
    for sample in minute_samples:
        if sample.pulses < 0:
            raise ValueError("Negative pulses cannot be priced")
        local_time = datetime.fromtimestamp(
            sample.timestamp, tz=timezone.utc
        ).astimezone(local_zone)
        observed_dates.add(local_time.date().isoformat())
        price = price_for_datetime(options, local_time)
        if price is None:
            raise ValueError("No configured rate for this period")
        total += (
            Decimal(sample.pulses)
            / Decimal(str(calibration))
            * Decimal(str(price))
        )
    return round(float(total), 9), frozenset(observed_dates)


def supply_charge_for_dates(
    observed_dates: frozenset[str],
    latest_charged_date: str | None,
    options: dict,
) -> tuple[float, str | None]:
    """Bill each local calendar day only once, even on 23/25-hour DST days.

    Existing historical days are never charged retroactively here. A separate
    explicit, previewable backfill is required for earlier dates.
    """
    if not observed_dates:
        return 0.0, latest_charged_date
    unseen = sorted(
        date for date in observed_dates
        if latest_charged_date is None or date > latest_charged_date
    )
    if not unseen:
        return 0.0, latest_charged_date
    daily_fee = daily_supply_dollars(options)
    if daily_fee is None:
        raise ValueError("Custom tariff not active")
    return round(daily_fee * len(unseen), 9), unseen[-1]
