"""Date-effective Australian tariff profiles for PalPulse.

The effective date is the retailer's *local billing date*, not the Home
Assistant configuration-save time. The profiles are append-only historical
intent until an administrator explicitly edits the same effective date.
Neither creating nor changing a profile rewrites Recorder rows.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Mapping
from zoneinfo import ZoneInfo

from .pricing import (
    CONF_PRICING_MODE, CONF_FLAT_CENTS, CONF_PEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
    CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS,
    WINDOW_FIELDS, validate_pricing,
)

CONF_TARIFF_START = "tariff_plan_start_date"
CONF_TARIFF_VERSIONS = "tariff_versions"
MAX_TARIFF_VERSIONS = 32
RATE_FIELDS = frozenset((
    CONF_PRICING_MODE, CONF_FLAT_CENTS, CONF_PEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
    CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS, *WINDOW_FIELDS,
))


def validate_start_date(value: object, *, today: date | None = None) -> str:
    """Accept only real ISO dates; never guess a historical start date."""
    if not isinstance(value, str):
        raise ValueError("Tariff effective date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid tariff effective date; use YYYY-MM-DD") from exc
    if value != parsed.isoformat():
        raise ValueError("Tariff effective date must be YYYY-MM-DD")
    if today is not None and parsed > today:
        raise ValueError("Future tariff activation is not supported in this beta")
    if parsed.year < 2020:
        raise ValueError("Tariff start date is implausibly old")
    return value


def captured_tariff(options: Mapping[str, object]) -> dict:
    return {key: options[key] for key in RATE_FIELDS if key in options}


def validate_versions(versions: object) -> list[dict]:
    """Validate stored versions as a bounded, unique, sorted list."""
    if not isinstance(versions, list) or len(versions) > MAX_TARIFF_VERSIONS:
        raise ValueError("Invalid tariff version history")
    result = []
    seen = set()
    for record in versions:
        if not isinstance(record, dict):
            raise ValueError("Invalid tariff version")
        effective = validate_start_date(record.get("effective_from"))
        options = record.get("options")
        if not isinstance(options, dict) or set(options) - RATE_FIELDS:
            raise ValueError("Tariff profile contains unsupported fields")
        validate_pricing(options)
        zone_name = record.get("timezone", "Australia/Melbourne")
        if not isinstance(zone_name, str):
            raise ValueError("Invalid tariff timezone")
        ZoneInfo(zone_name)
        if effective in seen:
            raise ValueError("Duplicate effective tariff date")
        seen.add(effective)
        result.append({
            "effective_from": effective,
            "options": captured_tariff(options),
            "timezone": zone_name,
        })
    return sorted(result, key=lambda p: p["effective_from"])


def upsert_tariff_version(
    versions: object, effective_from: str,
    options: Mapping[str, object], timezone_name: str,
    *, today: date | None = None,
) -> list[dict]:
    """Store a deliberate rate revision without altering older dates."""
    zone = ZoneInfo(timezone_name)
    effective_from = validate_start_date(
        effective_from, today=today or datetime.now(zone).date(),
    )
    snapshot = captured_tariff(options)
    validate_pricing(snapshot)
    history = [p for p in validate_versions(versions or [])
               if p["effective_from"] != effective_from]
    history.append({
        "effective_from": effective_from,
        "options": snapshot,
        "timezone": timezone_name,
    })
    return validate_versions(history)


def effective_tariff(
    versions: object, utc_start: int,
) -> dict | None:
    """Choose one saved contract as-of the measurement's billing-local date."""
    if utc_start % 3600:
        raise ValueError("Expected a UTC hour boundary")
    match = None
    for profile in validate_versions(versions or []):
        local = datetime.fromtimestamp(
            utc_start, timezone.utc,
        ).astimezone(ZoneInfo(profile["timezone"]))
        if local.date().isoformat() >= profile["effective_from"]:
            match = profile
    return match
