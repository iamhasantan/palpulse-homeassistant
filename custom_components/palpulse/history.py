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
MAX_SPARSE_MISSING_MINUTES = 58  # Exact counter deltas with both boundary minutes.
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
    missing_minutes: int = 0

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


def counter_verified_sparse_hour(
    records: Mapping[int, Record], start: int
) -> CompletedHour | None:
    """Recover exact hourly energy from cumulative endpoints, not estimated minutes.

    Both boundary readings are mandatory. Counter values must be monotonic,
    adjacent native minutes exact, and gaps bounded. The end-to-end pulse
    delta proves full-hour kWh even with 58 missing interior minutes, but
    never proves minute-level allocation across a changing tariff.
    """
    samples = sorted(
        (record for stamp, record in records.items()
         if start <= stamp < start + HOUR_SECONDS),
        key=lambda record: record.timestamp,
    )
    missing = RECORDS_PER_HOUR - len(samples)
    if not 1 <= missing <= MAX_SPARSE_MISSING_MINUTES:
        return None
    phase = samples[0].timestamp % RECORD_SECONDS
    if (
        samples[0].timestamp != start + phase
        or samples[-1].timestamp != start + phase + (RECORDS_PER_HOUR - 1) * RECORD_SECONDS
    ):
        return None
    previous = None
    previous_index = None
    for item in samples:
        offset = item.timestamp - start - phase
        if offset < 0 or offset % RECORD_SECONDS:
            return None
        index = offset // RECORD_SECONDS
        if index >= RECORDS_PER_HOUR or not 0 <= item.pulses <= 65535:
            return None
        if previous is not None:
            interval = index - previous_index
            counter_change = item.total_pulses - previous.total_pulses
            if (
                interval < 1
                or counter_change < item.pulses
                or counter_change > interval * 65535
                or (interval == 1 and counter_change != item.pulses)
            ):
                return None
        previous, previous_index = item, index
    first, last = samples[0], samples[-1]
    if last.total_pulses < first.total_pulses - first.pulses:
        return None
    return CompletedHour(
        hour_start=start,
        first_timestamp=first.timestamp,
        last_timestamp=last.timestamp,
        first_pulses=first.pulses,
        first_total=first.total_pulses,
        last_total=last.total_pulses,
        missing_minutes=missing,
    )


def counter_verified_from_previous(
    records: Mapping[int, Record],
    start: int,
    previous_timestamp: int,
    previous_total: int,
) -> CompletedHour | None:
    """Prove full-hour kWh using the preceding saved native minute as anchor.

    The current hour's first native record may be missing, but its last
    record is required. The previous hour's final durable counter plus the
    current final counter prove every pulse across this UTC hour without
    inventing a missing observation. Returned first boundary is synthetic
    and is never treated as a genuine minute sample for tariff allocation.
    """
    samples = sorted(
        (record for stamp, record in records.items()
         if start <= stamp < start + HOUR_SECONDS),
        key=lambda item: item.timestamp,
    )
    if not 1 <= len(samples) < RECORDS_PER_HOUR:
        return None
    phase = samples[-1].timestamp % RECORD_SECONDS
    if (
        previous_timestamp != start + phase - RECORD_SECONDS
        or previous_total < 0
        or samples[-1].timestamp != start + phase + (RECORDS_PER_HOUR - 1) * RECORD_SECONDS
    ):
        return None
    last_stamp = previous_timestamp
    last_total = previous_total
    for item in samples:
        interval_seconds = item.timestamp - last_stamp
        if interval_seconds <= 0 or interval_seconds % RECORD_SECONDS:
            return None
        count = interval_seconds // RECORD_SECONDS
        change = item.total_pulses - last_total
        if (
            change < item.pulses
            or change > count * 65535
            or (count == 1 and change != item.pulses)
        ):
            return None
        last_stamp = item.timestamp
        last_total = item.total_pulses
    return CompletedHour(
        hour_start=start,
        first_timestamp=start + phase,
        last_timestamp=samples[-1].timestamp,
        first_pulses=0,
        first_total=previous_total,
        last_total=last_total,
        missing_minutes=RECORDS_PER_HOUR - len(samples),
    )


def hourly_statistic(hour: CompletedHour, baseline_pulses: int, calibration: float) -> tuple[float, float]:
    """Return (raw meter state, continuous sum since first complete hour)."""
    if hour.last_total < baseline_pulses:
        raise ValueError("Counter went backwards")
    return (hour.last_total / calibration, (hour.last_total - baseline_pulses) / calibration)
