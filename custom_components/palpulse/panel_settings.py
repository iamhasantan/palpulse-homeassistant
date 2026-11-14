"""Strict, testable settings validation for the administrator-only sidebar.

No Recorder writes, firmware checkpoint resets or silent migration operations.
The existing pricing validator is the authority for all user-entered tariffs.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import math
import re
from collections.abc import Mapping

from .const import (
    CONF_ACK_SERVICE, CONF_DISABLE_ACK, CONF_AUTO_LINK_ENERGY_COST, CONF_LEGACY_ENERGY_SOURCE,
    DOMAIN,
)
from .tariff_history import (
    CONF_TARIFF_START, CONF_TARIFF_VERSIONS,
    upsert_tariff_version, validate_start_date, captured_tariff,
)
from .pricing import (
    CONF_PRICING_MODE, CONF_FLAT_CENTS, CONF_PEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
    CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS, WINDOW_FIELDS, MODES,
    validate_pricing,
)

COST_SOURCE = "legacy_cost_statistic_id"
COST_SOURCE_MODE = "legacy_cost_source_mode"
NUMERIC = frozenset((
    CONF_FLAT_CENTS, CONF_PEAK_CENTS, CONF_SHOULDER_CENTS,
    CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS, CONF_FREE_CAP_KWH,
    CONF_FREE_EXCESS_CENTS,
))
PRICING = frozenset((CONF_PRICING_MODE, *NUMERIC, *WINDOW_FIELDS))
EDITABLE = PRICING | frozenset((
    CONF_TARIFF_START,
    CONF_ACK_SERVICE, CONF_DISABLE_ACK, CONF_AUTO_LINK_ENERGY_COST,
    CONF_LEGACY_ENERGY_SOURCE, COST_SOURCE, COST_SOURCE_MODE,
))
SOURCE_RE = re.compile(r"^(?:sensor\.[a-z0-9_]+|[a-z0-9_]+:[a-z0-9_]+)$")
SERVICE_RE = re.compile(r"^esphome\.[a-z][a-z0-9_]+$")


def revision(options: Mapping[str, object]) -> str:
    """Optimistic concurrency token. Never expose arbitrary entry options."""
    payload = json.dumps(dict(options), sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return sha256(payload).hexdigest()[:24]


def pricing_revision(options: Mapping[str, object], zone_name: str) -> str:
    """Hash only contract-relevant inputs used by the historical cost plan.

    Normal user changes to meter settings, BLE acknowledgements, migration
    sources or dashboard options must not strand a running cost repair.
    Dates, historical tariff versions, current rates and the billing timezone
    are intentionally pinned. Recorder inputs are independently fingerprinted.
    """
    data = {
        "tariff_versions": options.get(CONF_TARIFF_VERSIONS, []),
        "current_tariff": captured_tariff(options),
        "billing_timezone": zone_name,
    }
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:24]


def visible_settings(options: Mapping[str, object]) -> dict:
    """Return only explicitly permitted, non-secret editable settings."""
    visible = {key: options[key] for key in EDITABLE if key in options}
    visible.setdefault(CONF_DISABLE_ACK, False)
    return visible


def normalize_settings(
    current: Mapping[str, object], changes: dict, *, meter: str,
    energy_journal: dict | None = None,
    cost_journal: dict | None = None,
    timezone_name: str = "Australia/Melbourne",
) -> dict:
    """Produce a full validated options snapshot, without mutating input."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("No settings were submitted")
    extras = set(changes) - EDITABLE
    if extras:
        raise ValueError("Unknown or read-only setting: " + ", ".join(sorted(extras)))
    updated = dict(current)

    for key, value in changes.items():
        if key == CONF_TARIFF_START:
            updated[key] = validate_start_date(
                value, today=datetime.now(ZoneInfo(timezone_name)).date(),
            )
        elif key in NUMERIC:
            if value is None or value == "":
                updated.pop(key, None)
                continue
            try:
                number = float(value)
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"{key} must be a number") from exc
            if not math.isfinite(number) or not 0 <= number <= 10000:
                raise ValueError(f"{key} must be between 0 and 10000")
            updated[key] = number
        elif key in WINDOW_FIELDS:
            if not isinstance(value, str):
                raise ValueError(f"{key} must use HH:MM-HH:MM")
            updated[key] = value.strip()
        elif key == CONF_PRICING_MODE:
            if value not in MODES:
                raise ValueError("Unknown pricing mode")
            updated[key] = value
        elif key == CONF_ACK_SERVICE:
            if not isinstance(value, str):
                raise ValueError("ESPHome ACK action must be text")
            action = value.strip()
            if action and not SERVICE_RE.fullmatch(action):
                raise ValueError("ESPHome ACK action must look like esphome.device_action")
            if action:
                updated[key] = action
            else:
                updated.pop(key, None)
        elif key == CONF_DISABLE_ACK:
            if type(value) is not bool:
                raise ValueError("Disable acknowledgements must be on or off")
            updated[key] = value
        elif key == CONF_AUTO_LINK_ENERGY_COST:
            if type(value) is not bool:
                raise ValueError("Automatic cost linking must be on or off")
            updated[key] = value
        elif key in (CONF_LEGACY_ENERGY_SOURCE, COST_SOURCE):
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a statistic or sensor ID")
            source = value.strip()
            if (not source and key == CONF_LEGACY_ENERGY_SOURCE) or (
                source and not SOURCE_RE.fullmatch(source)
            ):
                raise ValueError(f"Invalid source: {key}")
            target_type = "energy" if key == CONF_LEGACY_ENERGY_SOURCE else "cost"
            if source == f"{DOMAIN}:powerpal_{meter}_{target_type}":
                raise ValueError("A migration source cannot be its target")
            updated[key] = source
        elif key == COST_SOURCE_MODE:
            if value not in ("cumulative", "daily_reset"):
                raise ValueError("Unknown historical cost source type")
            updated[key] = value

    if any(key in changes for key in PRICING):
        validate_pricing(updated)
    # A rate revision without a start date preserves previous behaviour but
    # cannot retrospectively reprice anything. Once explicitly dated, each
    # further rate change updates that version until the date is advanced.
    if CONF_TARIFF_START in changes or any(key in changes for key in PRICING):
        effective = updated.get(CONF_TARIFF_START)
        if effective:
            updated[CONF_TARIFF_VERSIONS] = upsert_tariff_version(
                updated.get(CONF_TARIFF_VERSIONS, []),
                effective, updated, timezone_name,
            )

    if energy_journal and energy_journal.get("status") not in (None, "undone"):
        if updated.get(CONF_LEGACY_ENERGY_SOURCE) != current.get(CONF_LEGACY_ENERGY_SOURCE):
            raise ValueError("Energy migration has a journal. Undo it safely before changing sources.")
    if cost_journal and cost_journal.get("status") not in (None, "undone"):
        if (updated.get(COST_SOURCE, "") != current.get(COST_SOURCE, "")
                or updated.get(COST_SOURCE_MODE, "cumulative")
                   != current.get(COST_SOURCE_MODE, "cumulative")):
            raise ValueError("Cost migration has a journal. Undo it safely before changing sources.")
    return updated
