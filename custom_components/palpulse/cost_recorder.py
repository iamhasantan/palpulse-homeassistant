"""Persistent per-meter cost writer independent from PalPulse's energy cursor.

Experimental and not billing-grade. Existing energy statistics never change.
Only complete, timestamp-verified minutes are priced, with a conservative
daily-free-allowance guard after gaps. Recorder writes are verified before
durable checkpointing, and duplicate replay is reconciled.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
import time

from .history import CompletedHour, Record
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics, statistics_during_period,
)
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .cost import (free_dates_between, is_cost_enabled, price_verified_hour, supply_charge_for_dates)
from .pricing import CONF_FREE_CAP_KWH

_LOGGER = logging.getLogger(__name__)


class UnpriceableHour(ValueError):
    """Capped-free electricity charge cannot be established from measurements."""


class PalPulseCostRecorder:
    """Own the cost statistic, with a ledger separate from the energy importer."""

    def __init__(self, hass, entry, mac: str):
        self.hass = hass
        self.entry = entry
        self.mac = mac
        self.statistic_id = f"{DOMAIN}:powerpal_{mac}_cost"
        self._store = Store(hass, 1, f"{DOMAIN}_cost_{mac}")
        self._last_hour: int | None = None
        self._sum = 0.0
        self._last_supply_date: str | None = None
        self._free_used: dict[str, float] = {}
        self._free_uncertain_dates: set[str] = set()
        self._error: str | None = None
        self._pending: list[dict] = []
        self._tariff_profiles: list[dict] = []
        self._transition_hours: set[int] = set()
        self._unpriced_hours: set[int] = set()
        self._lock = asyncio.Lock()

    @property
    def status(self) -> dict:
        return {
            "statistic_id": self.statistic_id,
            "last_costed_hour": self._last_hour,
            "cumulative_cost": self._sum,
            "last_error": self._error,
            "enabled": is_cost_enabled(self.entry.options),
            "free_cap_uncertain_dates": sorted(self._free_uncertain_dates),
            "pending_cost_hours": len(self._pending),
            "tariff_transition_hours": sorted(self._transition_hours),
            "unpriced_hours": sorted(self._unpriced_hours),
        }

    def _snapshot(self) -> dict:
        return {
            "last_hour": self._last_hour,
            "sum": self._sum,
            "last_supply_date": self._last_supply_date,
            "free_used": self._free_used,
            "free_cap_uncertain_dates": sorted(self._free_uncertain_dates),
            "pending": self._pending,
            "tariff_profiles": self._tariff_profiles,
            "tariff_transition_hours": sorted(self._transition_hours),
            "last_error": self._error,
        }

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._last_hour = data.get("last_hour")
        self._sum = float(data.get("sum", 0))
        self._last_supply_date = data.get("last_supply_date")
        self._free_used = {str(k): float(v) for k, v in data.get("free_used", {}).items()}
        self._free_uncertain_dates = set(data.get("free_cap_uncertain_dates", []))
        self._pending = list(data.get("pending", []))
        self._error = data.get("last_error")
        self._tariff_profiles = list(data.get("tariff_profiles", []))
        self._transition_hours = set(int(ts) for ts in data.get("tariff_transition_hours", []))
        self._unpriced_hours = set(int(ts) for ts in data.get("unpriced_hours", []))
        current = dict(self.entry.options)
        zone = self.hass.config.time_zone
        if not self._tariff_profiles:
            self._tariff_profiles = [{"from": 0, "options": current, "timezone": zone}]
        elif (
            self._tariff_profiles[-1]["options"] != current
            or self._tariff_profiles[-1]["timezone"] != zone
        ):
            now = int(time.time())
            hour = now // 3600 * 3600
            self._transition_hours.add(hour)
            self._tariff_profiles.append({
                "from": hour + 3600, "options": current, "timezone": zone,
            })
        await self._store.async_save(self._snapshot())
        if not math.isfinite(self._sum):
            raise ValueError("PalPulse cost ledger has an invalid cumulative sum")

    async def async_flush(self) -> None:
        async with self._lock:
            await self._store.async_save(self._snapshot())

    async def _read(self, start: int, end: int) -> list[dict]:
        def work():
            return statistics_during_period(
                self.hass,
                datetime.fromtimestamp(start, timezone.utc),
                datetime.fromtimestamp(end, timezone.utc),
                {self.statistic_id},
                "hour",
                {},
                {"sum", "state"},
            ).get(self.statistic_id, [])
        return await self.hass.async_add_executor_job(work)

    async def async_import_verified_hour(self, hour, records: dict, calibration: float) -> None:
        """Persist a complete priced-hour job before energy advances its cursor."""
        if not is_cost_enabled(self.entry.options):
            return
        async with self._lock:
            if hour.hour_start in self._transition_hours:
                self._error = (
                    "Tariff changed during this UTC hour. No charge was "
                    "invented for the ambiguous transition"
                )
                await self._store.async_save(self._snapshot())
                return
            if not any(int(item["start"]) == hour.hour_start for item in self._pending):
                if len(self._pending) >= 168:
                    self._error = "Pending cost journal full (168 hours); review required"
                    await self._store.async_save(self._snapshot())
                    return
                samples = sorted(
                    (r for ts, r in records.items()
                     if hour.hour_start <= ts < hour.hour_start + 3600),
                    key=lambda r: r.timestamp,
                )
                if len(samples) != 60:
                    self._error = "Incomplete cost hour cannot be queued"
                    await self._store.async_save(self._snapshot())
                    return
                profile = max(
                    (p for p in self._tariff_profiles if int(p["from"]) <= hour.hour_start),
                    key=lambda p: int(p["from"]),
                )
                self._pending.append({
                    "start": hour.hour_start,
                    "first_timestamp": hour.first_timestamp,
                    "last_timestamp": hour.last_timestamp,
                    "first_pulses": hour.first_pulses,
                    "first_total": hour.first_total,
                    "last_total": hour.last_total,
                    "calibration": calibration,
                    "samples": [[r.timestamp, r.pulses, r.total_pulses] for r in samples],
                    "options": dict(profile["options"]),
                    "timezone": str(profile["timezone"]),
                })
                self._pending.sort(key=lambda item: int(item["start"]))
                await self._store.async_save(self._snapshot())
            await self._drain_pending()

    async def _drain_pending(self) -> None:
        for item in tuple(self._pending):
            try:
                hour = CompletedHour(
                    int(item["start"]), int(item["first_timestamp"]),
                    int(item["last_timestamp"]), int(item["first_pulses"]),
                    int(item["first_total"]), int(item["last_total"]),
                )
                cal = float(item["calibration"])
                records = {
                    int(row[0]): Record(int(row[0]), int(row[1]), int(row[2]), cal)
                    for row in item["samples"]
                }
                await self._write_hour(
                    hour, records, cal,
                    options=item["options"], time_zone=item["timezone"],
                )
                self._pending.remove(item)
                self._error = None
                await self._store.async_save(self._snapshot())
            except UnpriceableHour as err:
                self._pending.remove(item)
                self._unpriced_hours.add(int(item["start"]))
                self._error = str(err)
                await self._store.async_save(self._snapshot())
                _LOGGER.warning("PalPulse cost hour %s omitted: %s", item["start"], err)
                continue
            except Exception as err:
                self._error = f"{type(err).__name__}: {err}"
                await self._store.async_save(self._snapshot())
                _LOGGER.exception(
                    "PalPulse cost hour %s remains queued for meter %s",
                    item.get("start"), self.mac,
                )
                return

    async def _write_hour(
        self, hour, records: dict, calibration: float, *,
        options=None, time_zone: str | None = None,
    ) -> None:
        start = hour.hour_start
        if self._last_hour is not None and start <= self._last_hour:
            return
        # A previous Recorder write may have succeeded just before HA crashed
        # while its Store checkpoint failed. Never skip over those committed
        # but unjournalled cost hours and silently change the cumulative sum.
        if self._last_hour is not None and start > self._last_hour + 3600:
            pending = await self._read(self._last_hour + 3600, start)
            if pending:
                raise ValueError(
                    "Recorder has unjournalled cost hours after the last "
                    "durable checkpoint. Review before advancing cumulative cost"
                )
        options = self.entry.options if options is None else options
        time_zone = time_zone or self.hass.config.time_zone
        local_zone = ZoneInfo(time_zone)
        first_local = datetime.fromtimestamp(start, timezone.utc).astimezone(local_zone)
        first_date = first_local.date().isoformat()
        cap = float(options.get(CONF_FREE_CAP_KWH, 0))
        uncertain = set(self._free_uncertain_dates)
        if cap > 0:
            # Only gaps that overlap an actual capped-free window make its
            # allowance uncertain. A missing paid/off-peak hour must NOT stop
            # unrelated paid cost hours for the rest of the day.
            if self._last_hour is not None and start > self._last_hour + 3600:
                uncertain.update(free_dates_between(
                    options, time_zone,
                    self._last_hour + 3600, start,
                ))
            elif self._last_hour is None:
                # Enabling pricing mid-day may follow a free window already
                # consumed before this ledger existed. Treat later free
                # intervals on that date as uncertain, even after paid hours.
                midnight = first_local.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                uncertain.update(free_dates_between(
                    options, time_zone,
                    int(midnight.timestamp()), start,
                ))
            current_free = free_dates_between(
                options, time_zone, start, start + 3600,
            )
            if current_free & uncertain:
                self._free_uncertain_dates = uncertain
                await self._store.async_save(self._snapshot())
                raise UnpriceableHour(
                    "Capped free allocation is unknown for this free-window "
                    "hour after missing measurements. Paid hours outside the "
                    "free window may still be priced safely"
                )

        samples = [record for stamp, record in records.items()
                   if start <= stamp < start + 3600]
        usage, dates, free_used = price_verified_hour(
            samples, calibration, options, time_zone,
            self._free_used,
        )
        supply, charged_date = supply_charge_for_dates(
            dates, self._last_supply_date, options
        )
        next_sum = round(self._sum + usage + supply, 9)

        # A pre-existing target row MUST agree, never silently overwrite.
        present = await self._read(start, start + 3600)
        if present:
            if len(present) != 1 or int(present[0]["start"]) != start or (
                abs(float(present[0]["sum"]) - next_sum) > 1e-5
            ):
                raise ValueError(
                    "Existing cost statistic differs from journal; refusing overwrite"
                )
        else:
            rows = []
            if self._last_hour is None:
                previous = await self._read(start - 3600, start)
                if previous:
                    raise ValueError("Existing unjournalled cost baseline; review first")
                rows.append({
                    "start": datetime.fromtimestamp(start - 3600, timezone.utc),
                    "state": 0.0, "sum": 0.0,
                })
            rows.append({
                "start": datetime.fromtimestamp(start, timezone.utc),
                "state": next_sum, "sum": next_sum,
            })
            metadata = {
                "statistic_id": self.statistic_id,
                "source": DOMAIN,
                "name": "PalPulse electricity cost",
                "unit_of_measurement": None,
                "unit_class": None,
                "mean_type": StatisticMeanType.NONE,
                "has_sum": True,
            }
            async_add_external_statistics(self.hass, metadata, rows)
            await asyncio.wait_for(
                get_instance(self.hass).async_block_till_done(), timeout=180
            )
            saved = await self._read(start, start + 3600)
            if len(saved) != 1 or abs(float(saved[0]["sum"]) - next_sum) > 1e-5:
                raise ValueError("Recorder did not confirm the cost write")
        self._last_hour = start
        self._sum = next_sum
        self._free_uncertain_dates = {
            day for day in uncertain if day >= first_date
        }
        self._last_supply_date = charged_date
        # Only retain current and previous day. The full ledger is unnecessary.
        self._free_used = {
            key: value for key, value in free_used.items()
            if key >= first_date
        }
        await self._store.async_save(self._snapshot())
