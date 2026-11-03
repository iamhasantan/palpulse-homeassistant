"""Pure timestamp/accounting primitives for PalPulse imports.

This module intentionally has no Home Assistant imports, allowing cheap unit tests.
Records represent Powerpal's native, 60-second cadence. Statistics are hourly UTC,
which is also the boundary expected by Home Assistant Recorder.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Mapping

HOUR_SECONDS = 3600
RECORD_SECONDS = 60
RECORDS_PER_HOUR = 60
MAX_BACKFILL_AGE = 7 * 86400  # older backfills require a separate reviewed import
FUTURE_TOLERANCE = 120
MAC_RE = re.compile(r"^[0-9a-f]{12}$", re.IGNORECASE)


def normalize_mac(value: str) -> str:
    """Canonical 12-digit hex MAC, without separators."""
    mac = value.strip().replace(":", "").replace("-", "").lower()
    if not MAC_RE.fullmatch(mac):
        raise ValueError("Expected a 6-byte Powerpal MAC address")
    return mac


@dataclass(frozen=True, slots=True)
class Record:
    timestamp: int
    pulses: int
    total_pulses: int
    calibration: float

    def same_measurement(self, other: Record) -> bool:
        return (
            self.timestamp == other.timestamp
            and self.pulses == other.pulses
            and self.total_pulses == other.total_pulses
            and self.calibration == other.calibration
        )


@dataclass(frozen=True, slots=True)
class CompletedHour:
    hour_start: int
    first_timestamp: int
    last_timestamp: int
    first_pulses: int
    first_total: int
    last_total: int

    @property
    def consumption_pulses(self) -> int:
        return self.last_total - (self.first_total - self.first_pulses)


def parse_event(data: Mapping[str, object], target_mac: str, now: int) -> Record | None:
    """Discard unrelated events; reject corrupt or implausible meter records."""
    try:
        if normalize_mac(str(data.get("meter", ""))) != target_mac:
            return None
        timestamp = int(str(data["timestamp"]))
        pulses = int(str(data["pulses"]))
        cumulative = int(str(data["total_pulses"]))
        calibration = float(str(data["pulses_per_kwh"]))
        if not (0 <= pulses <= 65535 and cumulative >= pulses):
            return None
        if not (math.isfinite(calibration) and 1.0 <= calibration <= 1000000.0):
            return None
        if timestamp < now - MAX_BACKFILL_AGE or timestamp > now + FUTURE_TOLERANCE:
            return None
        return Record(timestamp, pulses, cumulative, calibration)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def complete_hour(records: Mapping[int, Record], start: int) -> CompletedHour | None:
    """Require 60 consecutive minute records with exact pulse/counter continuity.

    An incomplete hour is NOT imported. This protects against lost native API
    events or missing meter measurements being silently attributed to a later hour.
    """
    samples = sorted(
        (sample for timestamp, sample in records.items()
         if start <= timestamp < start + HOUR_SECONDS),
        key=lambda sample: sample.timestamp,
    )
    if len(samples) != RECORDS_PER_HOUR:
        return None
    phase = samples[0].timestamp % RECORD_SECONDS
    if samples[0].timestamp != start + phase:
        return None
    previous = None
    for index, item in enumerate(samples):
        if item.timestamp != start + phase + index * RECORD_SECONDS:
            return None
        if previous and item.total_pulses != previous.total_pulses + item.pulses:
            return None
        previous = item
    first, last = samples[0], samples[-1]
    return CompletedHour(
        hour_start=start,
        first_timestamp=first.timestamp,
        last_timestamp=last.timestamp,
        first_pulses=first.pulses,
        first_total=first.total_pulses,
        last_total=last.total_pulses,
    )


def hourly_statistic(hour: CompletedHour, baseline_pulses: int, calibration: float) -> tuple[float, float]:
    """Return (raw meter state, continuous sum since first complete hour)."""
    if hour.last_total < baseline_pulses:
        raise ValueError("Counter went backwards")
    return (hour.last_total / calibration, (hour.last_total - baseline_pulses) / calibration)
