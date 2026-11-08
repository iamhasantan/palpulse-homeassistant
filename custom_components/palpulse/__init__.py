"""Import complete, timestamped Powerpal hourly statistics into Home Assistant.

Records are received over Home Assistant's ESPHome event bus. To avoid inventing
readings, the importer requires a full 60-sample native-minute hour and exact
counter continuity. An incomplete hour remains a gap. Later fully verified hours can resume
without attributing missed consumption to the recovery hour.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.const import Platform
from homeassistant.helpers.storage import Store
from homeassistant.helpers.recorder import get_instance

from .const import CONF_METER_MAC, DOMAIN, EVENT_TYPE, STAT_NAME, STORAGE_VERSION
from .history import (
    HOUR_SECONDS,
    MAX_BACKFILL_AGE,
    RECORD_SECONDS,
    Record,
    complete_hour,
    hourly_statistic,
    parse_event,
)

_LOGGER = logging.getLogger(__name__)
SAVE_DELAY_SECONDS = 5
# An entire next hour must be complete and at least 15 minutes old before
# giving up on an earlier incomplete hour. ESPHome polls every five minutes.
RECOVERY_GRACE_SECONDS = 15 * 60
EVENT_STALE_SECONDS = 20 * 60


async def async_setup_entry(hass: HomeAssistant, entry) -> bool:
    """Start a per-meter event listener and persistent import ledger."""
    meter = entry.data[CONF_METER_MAC]
    # Rename only our own former default entry title. Preserve any title the
    # user customised, and preserve the unique meter identity and statistics.
    if getattr(entry, "title", None) == f"PalPulse {meter.upper()}":
        hass.config_entries.async_update_entry(
            entry, title="PalPulse for Home Assistant"
        )
    importer = PalPulseImporter(hass, meter)
    await importer.async_load()
    # Cost tracking is opt-in. It cannot rewrite or interrupt energy history.
    from .cost import is_cost_enabled
    if is_cost_enabled(getattr(entry, "options", {})):
        from .cost_recorder import PalPulseCostRecorder
        cost_recorder = PalPulseCostRecorder(hass, entry, meter)
        await cost_recorder.async_load()
        importer.cost_recorder = cost_recorder
        hass.data.setdefault(f"{DOMAIN}_cost", {})[entry.entry_id] = cost_recorder

    @callback
    def _receive(event: Event) -> None:
        hass.async_create_task(importer.async_receive(event.data))

    unsub = hass.bus.async_listen(EVENT_TYPE, _receive)
    entry.async_on_unload(unsub)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = importer
    # Create one shared migration manager before either entity platform starts.
    # The progress sensors and buttons both observe the same live job.
    from .migration import LegacyMigration

    manager = LegacyMigration(hass, entry, importer.statistic_id)
    await manager.async_load()
    hass.data.setdefault(f"{DOMAIN}_migration", {})[entry.entry_id] = manager
    # Separate opt-in cost migration: historical kWh migration remains unchanged.
    if getattr(entry, "options", {}).get("legacy_cost_statistic_id"):
        from .cost_migration import LegacyCostMigration
        cost_migration = LegacyCostMigration(
            hass, entry, f"{DOMAIN}:powerpal_{meter}_cost"
        )
        await cost_migration.async_load()
        hass.data.setdefault(f"{DOMAIN}_cost_migration", {})[
            entry.entry_id
        ] = cost_migration
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.BUTTON, Platform.SENSOR]
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry) -> bool:
    """Avoid unloading a running migration or losing the active progress UI."""
    manager = hass.data.get(f"{DOMAIN}_migration", {}).get(entry.entry_id)
    cost_migration = hass.data.get(f"{DOMAIN}_cost_migration", {}).get(entry.entry_id)
    if cost_migration is not None and cost_migration.busy:
        _LOGGER.warning("PalPulse reload refused during an active cost migration")
        return False
    if manager is not None and manager.busy:
        _LOGGER.warning(
            "PalPulse integration reload refused while historical migration "
            "is active. Monitor migration progress before restarting Home Assistant"
        )
        return False
    if not await hass.config_entries.async_unload_platforms(
        entry, [Platform.BUTTON, Platform.SENSOR]
    ):
        return False
    hass.data.get(f"{DOMAIN}_migration", {}).pop(entry.entry_id, None)
    hass.data.get(f"{DOMAIN}_cost_migration", {}).pop(entry.entry_id, None)
    cost_recorder = hass.data.get(f"{DOMAIN}_cost", {}).pop(entry.entry_id, None)
    if cost_recorder:
        await cost_recorder.async_flush()
    importer = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if importer:
        await importer.async_flush()
    return True


class PalPulseImporter:
    """Per-meter durable ledger and conservative hourly statistics importer."""

    def __init__(self, hass: HomeAssistant, mac: str) -> None:
        self.hass = hass
        self.mac = mac
        self.statistic_id = f"{DOMAIN}:powerpal_{mac}_energy"
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{mac}")
        self._lock = asyncio.Lock()
        self._records: dict[int, Record] = {}
        self._calibration: float | None = None
        self._baseline_pulses: int | None = None
        self._last_hour: int | None = None
        self._last_timestamp: int | None = None
        self._last_total: int | None = None
        self._halted = False
        self._accounted_pulses: int | None = None
        self._gap_count = 0
        self._last_gap_hour: int | None = None
        self._blocked_hour: int | None = None
        self._last_event_at: int | None = None
        self._status_message = "Waiting for timestamped Powerpal records"
        self._last_import_error: str | None = None
        self._listeners: set = set()

    def _snapshot(self) -> dict:
        return {
            "calibration": self._calibration,
            "baseline_pulses": self._baseline_pulses,
            "last_hour": self._last_hour,
            "last_timestamp": self._last_timestamp,
            "last_total": self._last_total,
            "halted": self._halted,
            "accounted_pulses": self._accounted_pulses,
            "gap_count": self._gap_count,
            "last_gap_hour": self._last_gap_hour,
            "blocked_hour": self._blocked_hour,
            "last_event_at": self._last_event_at,
            "status_message": self._status_message,
            "last_import_error": self._last_import_error,
            "records": {
                str(t): [r.pulses, r.total_pulses]
                for t, r in self._records.items()
            },
        }

    async def async_load(self) -> None:
        state = await self._store.async_load() or {}
        self._calibration = state.get("calibration")
        self._baseline_pulses = state.get("baseline_pulses")
        self._last_hour = state.get("last_hour")
        self._last_timestamp = state.get("last_timestamp")
        self._last_total = state.get("last_total")
        self._halted = bool(state.get("halted", False))
        # Migration from the existing 0.1.0b7 live ledger. That version's
        # sum was (last_total - baseline) / calibration with no gaps skipped.
        if state.get("accounted_pulses") is not None:
            self._accounted_pulses = int(state["accounted_pulses"])
        elif self._last_total is not None and self._baseline_pulses is not None:
            self._accounted_pulses = int(self._last_total) - int(self._baseline_pulses)
        self._gap_count = int(state.get("gap_count", 0))
        self._last_gap_hour = state.get("last_gap_hour")
        self._blocked_hour = state.get("blocked_hour")
        self._last_event_at = state.get("last_event_at")
        self._status_message = state.get("status_message", self._status_message)
        self._last_import_error = state.get("last_import_error")
        if self._last_hour is not None and (
            self._accounted_pulses is None or self._accounted_pulses < 0
        ):
            self._halted = True
            self._status_message = "Invalid persisted energy cursor; manual review required"
        if self._calibration is not None:
            try:
                self._records = {
                    int(ts): Record(int(ts), int(vals[0]), int(vals[1]), float(self._calibration))
                    for ts, vals in state.get("records", {}).items()
                }
            except (ValueError, TypeError, IndexError):
                _LOGGER.error("Corrupt PalPulse pending measurement store; import paused")
                self._records = {}
                self._halted = True
        _LOGGER.info(
            "PalPulse importer for %s listening (statistics %s, last complete hour %s)",
            self.mac, self.statistic_id, self._last_hour,
        )

    @property
    def health(self) -> dict:
        """Read-only live importer health, not legacy migration progress."""
        age = (int(time.time()) - self._last_event_at
               if self._last_event_at is not None else None)
        if self._halted:
            status = "Halted"
        elif self._last_import_error:
            status = "Recorder Error"
        elif age is None or age > EVENT_STALE_SECONDS:
            status = "No Events"
        elif self._blocked_hour is not None:
            status = "Waiting For Complete Hour"
        elif self._gap_count:
            status = "Active With Gaps"
        elif self._last_hour is None:
            status = "Collecting"
        else:
            status = "Active"
        return {
            "status": status,
            "last_event_received": self._last_event_at,
            "last_completed_utc_hour": self._last_hour,
            "blocked_utc_hour": self._blocked_hour,
            "skipped_hours": self._gap_count,
            "last_gap_utc_hour": self._last_gap_hour,
            "pending_minute_records": len(self._records),
            "message": self._status_message,
            "last_import_error": self._last_import_error,
            "statistic_id": self.statistic_id,
        }

    def subscribe(self, callback):
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def _notify_health(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    async def async_receive(self, data: dict) -> None:
        now = int(time.time())
        record = parse_event(data, self.mac, now)
        if record is None:
            return
        async with self._lock:
            self._last_event_at = now
            self._notify_health()
            if self._halted:
                return
            if self._calibration is None:
                self._calibration = record.calibration
            elif self._calibration != record.calibration:
                self._halted = True
                self._status_message = "Calibration changed; live history halted"
                _LOGGER.error(
                    "PalPulse calibration changed for meter %s: %s to %s. "
                    "Importer halted to avoid invalid historical statistics",
                    self.mac, self._calibration, record.calibration,
                )
                await self._store.async_save(self._snapshot())
                return
            if record.timestamp in self._records:
                if not self._records[record.timestamp].same_measurement(record):
                    self._halted = True
                    self._status_message = "Conflicting record; live history halted"
                    _LOGGER.error(
                        "Conflicting Powerpal record at %s for meter %s; import halted",
                        record.timestamp, self.mac,
                    )
                    await self._store.async_save(self._snapshot())
                return
            if (self._last_timestamp is not None and
                    record.timestamp <= self._last_timestamp):
                return  # Already imported, including duplicate replay after restart.
            self._records[record.timestamp] = record
            try:
                await self._drain(now)
                self._last_import_error = None
            except Exception as err:
                # Never advance the imported hour on Recorder exceptions.
                # A later event can retry idempotently from the saved cursor.
                message = f"{type(err).__name__}: {err}"
                if self._last_import_error != message:
                    _LOGGER.exception(
                        "PalPulse live Recorder write failed; keeping cursor "
                        "unchanged and retrying when new records arrive"
                    )
                self._last_import_error = message
                self._status_message = (
                    f"Recorder error; last imported hour unchanged: {message}"
                )
            finally:
                self._prune(now)
                self._store.async_delay_save(self._snapshot, SAVE_DELAY_SECONDS)
                self._notify_health()

    async def _drain(self, now: int) -> None:
        """Import all complete hours, safely passing permanent missing hours.

        Never bridge a gap using an unadjusted meter total: its delta would
        misattribute missed energy to the first hour after the gap.
        """
        assert self._calibration is not None
        cutoff = ((now - 120) // HOUR_SECONDS) * HOUR_SECONDS
        candidates = sorted({(ts // HOUR_SECONDS) * HOUR_SECONDS
                             for ts in self._records})
        if self._last_hour is None:
            for start in candidates:
                if start + HOUR_SECONDS > cutoff:
                    continue
                hour = complete_hour(self._records, start)
                if hour is None:
                    continue
                base = hour.first_total - hour.first_pulses
                if base < 0:
                    continue
                # Preserve the original first-hour zero baseline semantics.
                await self._import([
                    {"start": datetime.fromtimestamp(start - HOUR_SECONDS, timezone.utc),
                     "state": base / self._calibration, "sum": 0.0},
                    self._statistic(hour, hour.consumption_pulses),
                ])
                if cost_recorder := getattr(self, "cost_recorder", None):
                    await cost_recorder.async_import_verified_hour(
                        hour, self._records, self._calibration
                    )
                self._baseline_pulses = base
                self._accounted_pulses = hour.consumption_pulses
                self._last_hour = start
                self._last_timestamp = hour.last_timestamp
                self._last_total = hour.last_total
                self._status_message = "First complete Powerpal hour imported"
                await self._store.async_save(self._snapshot())
                self._notify_health()
                _LOGGER.info("PalPulse first complete hour imported at %s UTC", start)
                break

        while self._last_hour is not None:
            start = self._last_hour + HOUR_SECONDS
            if start + HOUR_SECONDS > cutoff:
                return
            hour = complete_hour(self._records, start)
            skipped = 0
            if hour is None:
                # Wait for late five-minute BLE polls and buffered API events.
                # Only resume when a later complete hour is independently
                # verified and its end is at least 15 minutes in the past.
                for candidate in candidates:
                    if candidate <= start:
                        continue
                    if candidate + HOUR_SECONDS + RECOVERY_GRACE_SECONDS > now:
                        break
                    if candidate + HOUR_SECONDS > cutoff:
                        break
                    future = complete_hour(self._records, candidate)
                    if future is not None:
                        hour = future
                        skipped = (candidate - start) // HOUR_SECONDS
                        break
                if hour is None:
                    if self._blocked_hour != start and any(
                        candidate > start for candidate in candidates
                    ):
                        self._blocked_hour = start
                        self._status_message = (
                            f"Incomplete UTC hour {start}; waiting for missing "
                            "records or a later complete hour"
                        )
                        _LOGGER.warning(
                            "PalPulse incomplete UTC hour %s: waiting for late "
                            "records or a verified recovery hour", start,
                        )
                        self._notify_health()
                    return

            if self._last_timestamp is None or self._last_total is None or (
                self._accounted_pulses is None
            ):
                self._halted = True
                self._status_message = "Missing persisted energy cursor; importer halted"
                await self._store.async_save(self._snapshot())
                self._notify_health()
                return

            first_before = hour.first_total - hour.first_pulses
            if (
                first_before < self._last_total
                or (skipped == 0 and (
                    hour.first_timestamp != self._last_timestamp + RECORD_SECONDS
                    or hour.first_total != self._last_total + hour.first_pulses
                ))
            ):
                self._halted = True
                self._status_message = (
                    f"Meter counter or timestamps discontinuous at UTC hour "
                    f"{hour.hour_start}; review required"
                )
                _LOGGER.error(
                    "PalPulse live import halted: meter discontinuity at UTC %s. "
                    "No energy was estimated or inserted", hour.hour_start
                )
                await self._store.async_save(self._snapshot())
                self._notify_health()
                return

            # Only the 60 proven minutes of THIS hour contribute to the sum.
            # Energy accrued during skipped hours remains intentionally unknown.
            next_accounted = self._accounted_pulses + hour.consumption_pulses
            await self._import([self._statistic(hour, next_accounted)])
            if cost_recorder := getattr(self, "cost_recorder", None):
                await cost_recorder.async_import_verified_hour(
                    hour, self._records, self._calibration
                )
            self._accounted_pulses = next_accounted
            self._last_hour = hour.hour_start
            self._last_timestamp = hour.last_timestamp
            self._last_total = hour.last_total
            if skipped:
                self._gap_count += skipped
                self._last_gap_hour = start
                self._status_message = (
                    f"Recovered after skipping {skipped} incomplete UTC hour(s), "
                    f"starting at {start}. Missing consumption was not estimated"
                )
                _LOGGER.warning(
                    "PalPulse recovered at UTC hour %s after %d skipped "
                    "hour(s) beginning %s; skipped energy not counted",
                    hour.hour_start, skipped, start,
                )
            else:
                self._status_message = (
                    f"Imported complete UTC hour {hour.hour_start}"
                )
            self._blocked_hour = None
            await self._store.async_save(self._snapshot())
            self._notify_health()

    def _statistic(self, hour, accounted_pulses: int) -> dict:
        assert self._calibration is not None
        return {
            "start": datetime.fromtimestamp(hour.hour_start, timezone.utc),
            "state": hour.last_total / self._calibration,
            "sum": accounted_pulses / self._calibration,
        }

    async def _import(self, rows: list[dict]) -> None:
        metadata = {
            "statistic_id": self.statistic_id,
            "source": DOMAIN,
            "name": STAT_NAME,
            "unit_of_measurement": "kWh",
            "unit_class": "energy",
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
        }
        # This queues Recorder work. Wait for its writes before advancing the
        # Store cursor, so an HA restart can't leave a committed cursor with
        # a missing corresponding statistic.
        async_add_external_statistics(self.hass, metadata, rows)
        await get_instance(self.hass).async_block_till_done()

    def _prune(self, now: int) -> None:
        cutoff = now - MAX_BACKFILL_AGE
        if self._last_hour is not None:
            cutoff = max(cutoff, self._last_hour * 1 - HOUR_SECONDS)
        for stamp in list(self._records):
            if stamp < cutoff:
                del self._records[stamp]
        if len(self._records) > 12000:
            self._halted = True
            _LOGGER.error("PalPulse pending record ledger exceeded memory cap; halted")

    async def async_flush(self) -> None:
        async with self._lock:
            await self._store.async_save(self._snapshot())
