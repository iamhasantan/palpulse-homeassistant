"""Import complete, timestamped Powerpal hourly statistics into Home Assistant.

Records are received over Home Assistant's ESPHome event bus. To avoid inventing
readings, the importer requires a full 60-sample native-minute hour and exact
counter continuity. A missing event blocks that hour rather than converting
a historical gap into a later, incorrect spike.
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


async def async_setup_entry(hass: HomeAssistant, entry) -> bool:
    """Start a per-meter event listener and persistent import ledger."""
    meter = entry.data[CONF_METER_MAC]
    importer = PalPulseImporter(hass, meter)
    await importer.async_load()

    @callback
    def _receive(event: Event) -> None:
        hass.async_create_task(importer.async_receive(event.data))

    unsub = hass.bus.async_listen(EVENT_TYPE, _receive)
    entry.async_on_unload(unsub)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = importer
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.BUTTON])
    return True


async def async_unload_entry(hass: HomeAssistant, entry) -> bool:
    """Stop receiving data and store pending measurements."""
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.BUTTON]):
        return False
    hass.data.get(f"{DOMAIN}_migration", {}).pop(entry.entry_id, None)
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

    def _snapshot(self) -> dict:
        return {
            "calibration": self._calibration,
            "baseline_pulses": self._baseline_pulses,
            "last_hour": self._last_hour,
            "last_timestamp": self._last_timestamp,
            "last_total": self._last_total,
            "halted": self._halted,
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

    async def async_receive(self, data: dict) -> None:
        now = int(time.time())
        record = parse_event(data, self.mac, now)
        if record is None:
            return
        async with self._lock:
            if self._halted:
                return
            if self._calibration is None:
                self._calibration = record.calibration
            elif self._calibration != record.calibration:
                self._halted = True
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
            await self._drain(now)
            self._prune(now)
            self._store.async_delay_save(self._snapshot, SAVE_DELAY_SECONDS)

    async def _drain(self, now: int) -> None:
        assert self._calibration is not None
        # Import only fully elapsed hours. The two-minute lag tolerates late
        # arrivals while the device is online.
        cutoff = ((now - 120) // HOUR_SECONDS) * HOUR_SECONDS
        candidates = sorted({(ts // HOUR_SECONDS) * HOUR_SECONDS for ts in self._records})

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
                # Establish a zero-sum baseline in the preceding hour. This
                # prevents an initial 20+ kWh meter reading being treated as
                # 20+ kWh of consumption at installation.
                baseline = {
                    "start": datetime.fromtimestamp(start - HOUR_SECONDS, timezone.utc),
                    "state": base / self._calibration,
                    "sum": 0.0,
                }
                current = self._statistic(hour, base)
                self._import([baseline, current])
                self._baseline_pulses = base
                self._last_hour = start
                self._last_timestamp = hour.last_timestamp
                self._last_total = hour.last_total
                _LOGGER.info("PalPulse first complete hour imported at %s UTC", start)
                await self._store.async_save(self._snapshot())
                break

        # Contiguous, complete hours only. Missing hours are never bridged:
        # bridging would attribute unobserved past consumption to a later hour.
        while self._last_hour is not None:
            start = self._last_hour + HOUR_SECONDS
            if start + HOUR_SECONDS > cutoff:
                return
            hour = complete_hour(self._records, start)
            if hour is None:
                if any((ts // HOUR_SECONDS) * HOUR_SECONDS > start for ts in self._records):
                    _LOGGER.warning(
                        "PalPulse historical import paused at UTC hour %s: incomplete "
                        "Powerpal records; preserving gap rather than creating a spike",
                        start,
                    )
                return
            if (self._last_timestamp is None or self._last_total is None or
                    hour.first_timestamp != self._last_timestamp + RECORD_SECONDS or
                    hour.first_total != self._last_total + hour.first_pulses):
                self._halted = True
                _LOGGER.error(
                    "PalPulse discontinuity at hour %s; import halted. "
                    "Do not insert guessed energy data", start,
                )
                await self._store.async_save(self._snapshot())
                return
            assert self._baseline_pulses is not None
            self._import([self._statistic(hour, self._baseline_pulses)])
            self._last_hour = start
            self._last_timestamp = hour.last_timestamp
            self._last_total = hour.last_total
            await self._store.async_save(self._snapshot())

    def _statistic(self, hour, baseline_pulses: int) -> dict:
        assert self._calibration is not None
        state, summation = hourly_statistic(hour, baseline_pulses, self._calibration)
        return {
            "start": datetime.fromtimestamp(hour.hour_start, timezone.utc),
            "state": state,
            "sum": summation,
        }

    def _import(self, rows: list[dict]) -> None:
        metadata = {
            "statistic_id": self.statistic_id,
            "source": DOMAIN,
            "name": STAT_NAME,
            "unit_of_measurement": "kWh",
            "unit_class": "energy",
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
        }
        # Recorder upserts by statistic_id and UTC hour; duplicates won't
        # increase sums. It queues the write, not a synchronous database commit.
        async_add_external_statistics(self.hass, metadata, rows)

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
