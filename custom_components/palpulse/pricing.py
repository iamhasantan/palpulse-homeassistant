"""Optional user-defined electricity price schedules.

This module is deliberately independent of Home Assistant and Recorder.
No tariff calculation alters Powerpal energy readings or stored statistics.
Currency amounts in configuration are cents, with sensor output in dollars.
"""

from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Mapping

MODE_HOME_ASSISTANT = "home_assistant"
MODE_FLAT = "custom_flat"
MODE_TOU = "custom_time_of_use"
MODES = (MODE_HOME_ASSISTANT, MODE_FLAT, MODE_TOU)

CONF_PRICING_MODE = "pricing_mode"
CONF_FLAT_CENTS = "flat_cents_per_kwh"
CONF_PEAK_CENTS = "peak_cents_per_kwh"
CONF_SHOULDER_CENTS = "shoulder_cents_per_kwh"
CONF_OFFPEAK_CENTS = "offpeak_cents_per_kwh"
CONF_SUPPLY_CENTS = "daily_supply_cents"
CONF_FREE_CAP_KWH = "daily_free_cap_kwh"
CONF_FREE_EXCESS_CENTS = "free_excess_cents_per_kwh"
CONF_WEEKDAY_FREE = "weekday_free"
CONF_WEEKEND_FREE = "weekend_free"
CONF_WEEKDAY_PEAK = "weekday_peak"
CONF_WEEKDAY_SHOULDER = "weekday_shoulder"
CONF_WEEKEND_PEAK = "weekend_peak"
CONF_WEEKEND_SHOULDER = "weekend_shoulder"

WINDOW_FIELDS = (
    CONF_WEEKDAY_FREE,
    CONF_WEEKEND_FREE,
    CONF_WEEKDAY_PEAK,
    CONF_WEEKDAY_SHOULDER,
    CONF_WEEKEND_PEAK,
    CONF_WEEKEND_SHOULDER,
)
_WINDOW_RE = re.compile(r"^([0-2]\d):([0-5]\d)\s*-\s*([0-2]\d):([0-5]\d)$")


def parse_windows(value: str) -> tuple[tuple[int, int], ...]:
    """Parse comma-separated local-time half-open intervals.

    Windows must not cross midnight. Split overnight windows at 00:00,
    choosing the appropriate weekday or weekend fields for each day.
    """
    if not isinstance(value, str):
        raise ValueError("Tariff windows must be text")
    if not value.strip():
        return ()
    windows: list[tuple[int, int]] = []
    for part in value.split(","):
        match = _WINDOW_RE.fullmatch(part.strip())
        if match is None:
            raise ValueError("Use HH:MM-HH:MM separated by commas")
        h1, m1, h2, m2 = map(int, match.groups())
        if h1 >= 24 or h2 > 24 or (h2 == 24 and m2 != 0):
            raise ValueError("Invalid clock time")
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if not 0 <= start < end <= 1440:
            raise ValueError("Tariff windows must stay within one day")
        windows.append((start, end))
    windows.sort()
    if any(a[1] > b[0] for a, b in zip(windows, windows[1:])):
        raise ValueError("Overlapping tariff windows")
    return tuple(windows)


def validate_pricing(
    options: Mapping[str, object], *, require_excess_rate: bool = True,
) -> None:
    """Validate a tariff, optionally for a proven non-free billing interval.

    Saving a configured tariff always requires a positive capped-free excess
    rate. An already-recorded paid hour can be priced independently when it
    does not intersect any capped-free period. This never supplies or guesses
    the missing rate.
    """
    mode = options.get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT)
    if mode not in MODES:
        raise ValueError("Unknown electricity pricing mode")
    if mode == MODE_HOME_ASSISTANT:
        return
    rate_keys = (CONF_FLAT_CENTS,) if mode == MODE_FLAT else (
        CONF_PEAK_CENTS, CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS,
    )
    for key in (*rate_keys, CONF_SUPPLY_CENTS):
        if key not in options:
            raise ValueError(f"Missing {key}")
        try:
            value = float(options[key])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid {key}") from exc
        if not math.isfinite(value) or not 0 <= value <= 10000:
            raise ValueError(f"Invalid {key}")

    if mode == MODE_TOU:
        cap = float(options.get(CONF_FREE_CAP_KWH, 0))
        excess = float(options.get(CONF_FREE_EXCESS_CENTS, 0))
        if not all(math.isfinite(x) for x in (cap, excess)) or cap < 0 or excess < 0 or cap > 10000 or excess > 10000:
            raise ValueError("Invalid free electricity daily cap or excess rate")
        if require_excess_rate and cap > 0 and (CONF_FREE_EXCESS_CENTS not in options or excess <= 0):
            raise ValueError("A positive excess rate is required for a capped free window")
        schedules = {key: parse_windows(options.get(key, "")) for key in WINDOW_FIELDS}
        for day in ("weekday", "weekend"):
            peak = schedules[f"{day}_peak"]
            shoulder = schedules[f"{day}_shoulder"]
            if any(a[0] < b[1] and b[0] < a[1] for a in peak for b in shoulder):
                raise ValueError(f"Overlapping {day} peak and shoulder windows")


def price_for_datetime(
    options: Mapping[str, object], local_time: datetime, *,
    require_excess_rate: bool = True,
) -> float | None:
    """Return a verified local-timestamp price in dollars per kWh."""
    if local_time.tzinfo is None or local_time.utcoffset() is None:
        raise ValueError("A timezone-aware local datetime is required")
    validate_pricing(options, require_excess_rate=require_excess_rate)
    if not require_excess_rate and free_window_active(options, local_time):
        raise ValueError("Cannot omit capped-free excess validation inside the free window")
    mode = options.get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT)
    if mode == MODE_HOME_ASSISTANT:
        return None
    if mode == MODE_FLAT:
        return round(float(options[CONF_FLAT_CENTS]) / 100, 6)
    day = "weekday" if local_time.weekday() < 5 else "weekend"
    minute = local_time.hour * 60 + local_time.minute
    if any(start <= minute < end for start, end in parse_windows(options.get(f"{day}_free", ""))):
        return 0.0
    for window_type, key in (
        ("peak", CONF_PEAK_CENTS),
        ("shoulder", CONF_SHOULDER_CENTS),
    ):
        windows = parse_windows(options.get(f"{day}_{window_type}", ""))
        if any(start <= minute < end for start, end in windows):
            return round(float(options[key]) / 100, 6)
    return round(float(options[CONF_OFFPEAK_CENTS]) / 100, 6)


def daily_supply_dollars(
    options: Mapping[str, object], *, require_excess_rate: bool = True,
) -> float | None:
    """Daily supply fee, independent of free-cap pricing for paid-only hours."""
    validate_pricing(options, require_excess_rate=require_excess_rate)
    if options.get(CONF_PRICING_MODE, MODE_HOME_ASSISTANT) == MODE_HOME_ASSISTANT:
        return None
    return round(float(options[CONF_SUPPLY_CENTS]) / 100, 6)


def free_window_active(options: Mapping[str, object], local_time: datetime) -> bool:
    """Check a local-date free window, independent of ordinary zero-price tiers."""
    if options.get(CONF_PRICING_MODE) != MODE_TOU:
        return False
    day = "weekday" if local_time.weekday() < 5 else "weekend"
    minute = local_time.hour * 60 + local_time.minute
    return any(start <= minute < end for start, end in
               parse_windows(options.get(f"{day}_free", "")))
