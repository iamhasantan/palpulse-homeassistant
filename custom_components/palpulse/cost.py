"""Pure cost accounting from proven one-minute Powerpal observations.

Cost uses the measurement's actual timestamp in the configured HA timezone,
not ESPHome event arrival time, preserving free/peak/shoulder transitions.
No cost estimate is made for an incomplete Powerpal hour.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from .pricing import (
    CONF_PRICING_MODE, CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS,
    MODE_HOME_ASSISTANT, daily_supply_dollars, free_window_active,
    price_for_datetime, validate_pricing,
)



def free_dates_between(
    options: Mapping[str, object], time_zone: str,
    start_utc: int, end_utc: int,
) -> frozenset[str]:
    """Find capped-free local dates touched by an unpriced UTC minute range.

    Iterate UTC minutes, not wall-clock minutes, so DST folds and skipped
    hours cannot manufacture or omit a free allowance.
    """
    if end_utc < start_utc or start_utc % 60 or end_utc % 60:
        raise ValueError("Expected ordered, UTC minute-aligned boundaries")
    if end_utc == start_utc or not float(options.get(CONF_FREE_CAP_KWH, 0)):
        return frozenset()
    zone = ZoneInfo(time_zone)
    return frozenset(
        local.date().isoformat()
        for instant in range(start_utc, end_utc, 60)
        if free_window_active(
            options,
            (local := datetime.fromtimestamp(instant, timezone.utc).astimezone(zone)),
        )
    )


def is_cost_enabled(options: Mapping[str, object]) -> bool:
    """Leave native Home Assistant pricing alone unless custom mode is chosen."""
    return options.get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT) != MODE_HOME_ASSISTANT


def price_verified_hour(
    samples: Iterable,
    calibration: float,
    options: Mapping[str, object],
    time_zone: str,
    previous_free_kwh: Mapping[str, float] | None = None,
) -> tuple[float, frozenset[str], dict[str, float]]:
    """Cost and daily capped-free allocation from 60 verified minute records.

    previous_free_kwh is the free-window consumption already observed that
    local day, INCLUDING units billed after the cap is reached. This is tracked
    per-date to avoid a new free allowance for each separate UTC hour.
    """
    if not is_cost_enabled(options):
        raise ValueError("Custom pricing has not been enabled")
    if not 0 < calibration <= 1_000_000:
        raise ValueError("Invalid calibration")
    records = sorted(samples, key=lambda record: record.timestamp)
    if len(records) != 60:
        raise ValueError("Only complete native-minute hours may be priced")
    first = records[0].timestamp
    if any(item.timestamp != first + i * 60 for i, item in enumerate(records)):
        raise ValueError("Missing or irregular native-minute samples")
    zone = ZoneInfo(time_zone)
    # Validate the rest of the tariff first. A missing excess rate matters
    # only if verified consumption actually exceeds the remaining allowance.
    validate_pricing(options, require_excess_rate=False)
    used_by_date = {
        str(key): Decimal(str(value))
        for key, value in (previous_free_kwh or {}).items()
    }
    cap = Decimal(str(options.get(CONF_FREE_CAP_KWH, 0)))
    overflow = Decimal(str(options.get(CONF_FREE_EXCESS_CENTS, 0))) / Decimal(100)
    total = Decimal(0)
    dates = set()
    for record in records:
        if record.pulses < 0:
            raise ValueError("Negative pulses")
        local = datetime.fromtimestamp(record.timestamp, timezone.utc).astimezone(zone)
        date_key = local.date().isoformat()
        dates.add(date_key)
        kwh = Decimal(record.pulses) / Decimal(str(calibration))
        if free_window_active(options, local):
            prior = used_by_date.get(date_key, Decimal(0))
            if cap:
                free_kwh = min(kwh, max(Decimal(0), cap - prior))
                if kwh > free_kwh and overflow <= 0:
                    raise ValueError("A positive excess rate is required for a capped free window")
                total += (kwh - free_kwh) * overflow
            # cap == 0 means an unlimited free interval.
            used_by_date[date_key] = prior + kwh
        else:
            rate = price_for_datetime(options, local, require_excess_rate=False)
            if rate is None:
                raise ValueError("Current tariff rate unavailable")
            total += kwh * Decimal(str(rate))
    return (
        round(float(total), 9),
        frozenset(dates),
        {key: float(value) for key, value in used_by_date.items()},
    )


class SparseTariffBoundaryError(ValueError):
    """Hourly counter energy cannot be assigned to multiple price bands."""


def price_counter_verified_hour(
    hour,
    calibration: float,
    options: Mapping[str, object],
    time_zone: str,
    previous_free_kwh: Mapping[str, float] | None = None,
) -> tuple[float, frozenset[str], dict[str, float]]:
    """Exact cost from sparse native minutes if the *whole* hour has one band.

    First and last native minute counters must already have been verified by
    counter_verified_sparse_hour. No energy or tariff allocation is estimated.
    This works for a full free hour, including a capped allowance, or a
    constant-rate paid hour, but never for a mixed tariff/date interval.
    """
    if not is_cost_enabled(options) or not 0 < calibration <= 1_000_000:
        raise ValueError("Custom pricing and valid calibration are required")
    if not 1 <= hour.missing_minutes <= 58 or hour.consumption_pulses < 0:
        raise ValueError("Expected a counter-verified sparse native hour")
    validate_pricing(options, require_excess_rate=False)
    zone = ZoneInfo(time_zone)
    first_date = None
    first_band = None
    for minute in range(60):
        local = datetime.fromtimestamp(
            hour.hour_start + minute * 60, timezone.utc
        ).astimezone(zone)
        date_key = local.date().isoformat()
        if first_date is None:
            first_date = date_key
        elif first_date != date_key:
            raise SparseTariffBoundaryError(
                "Sparse UTC hour spans two local billing dates"
            )
        if free_window_active(options, local):
            band = ("free", None)
        else:
            rate = price_for_datetime(
                options, local, require_excess_rate=False,
            )
            if rate is None:
                raise ValueError("Current tariff rate unavailable")
            band = ("paid", rate)
        if first_band is None:
            first_band = band
        elif band != first_band:
            raise SparseTariffBoundaryError(
                "Sparse UTC hour crosses multiple tariff bands"
            )

    kwh = Decimal(hour.consumption_pulses) / Decimal(str(calibration))
    used = {
        str(key): Decimal(str(value))
        for key, value in (previous_free_kwh or {}).items()
    }
    if first_band[0] == "free":
        prior = used.get(first_date, Decimal(0))
        cap = Decimal(str(options.get(CONF_FREE_CAP_KWH, 0)))
        if cap:
            free_kwh = min(kwh, max(Decimal(0), cap - prior))
            paid_kwh = kwh - free_kwh
            overflow = Decimal(str(options.get(CONF_FREE_EXCESS_CENTS, 0))) / Decimal(100)
            if paid_kwh > 0 and overflow <= 0:
                raise ValueError(
                    "A positive excess rate is required for a capped free window"
                )
            amount = paid_kwh * overflow
        else:
            amount = Decimal(0)
        used[first_date] = prior + kwh
    else:
        amount = kwh * Decimal(str(first_band[1]))

    return (
        round(float(amount), 9),
        frozenset({first_date}),
        {key: float(value) for key, value in used.items()},
    )


def cost_for_verified_hour(
    samples: Iterable, calibration: float, options: Mapping[str, object],
    time_zone: str
) -> tuple[float, frozenset[str]]:
    """Compatibility wrapper for isolated hourly costing and simple tests."""
    amount, dates, _ = price_verified_hour(samples, calibration, options, time_zone)
    return amount, dates


def supply_charge_for_dates(
    observed_dates: frozenset[str], latest_charged_date: str | None,
    options: Mapping[str, object], *, require_excess_rate: bool = True,
) -> tuple[float, str | None]:
    """Add a daily fee once per local calendar day, including DST transitions.

    The fee defaults to zero. No fee is guessed from a different plan or
    included in other imported cost sources unless the user configured one.
    """
    if not observed_dates:
        return 0.0, latest_charged_date
    unseen = sorted(date for date in observed_dates
                    if latest_charged_date is None or date > latest_charged_date)
    if not unseen:
        return 0.0, latest_charged_date
    fee = daily_supply_dollars(options, require_excess_rate=require_excess_rate)
    if fee is None:
        raise ValueError("Custom tariff not active")
    return round(fee * len(unseen), 9), unseen[-1]
