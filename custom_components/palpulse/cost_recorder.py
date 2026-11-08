"""Persistent per-meter cost writer independent from PalPulse's energy cursor.

Experimental and not billing-grade. Existing energy statistics never change.
Only complete, timestamp-verified minutes are priced, with a conservative
daily-free-allowance guard after gaps. Recorder writes are verified before
durable checkpointing, and duplicate replay is reconciled.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import logging
import math
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics, statistics_during_period,
)
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .cost import is_cost_enabled, price_verified_hour, supply_charge_for_dates
from .pricing import CONF_FREE_CAP_KWH, free_window_active

_LOGGER = logging.getLogger(__name__)


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
        self._error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def status(self) -> dict:
        return {
            "statistic_id": self.statistic_id,
            "last_costed_hour": self._last_hour,
            "cumulative_cost": self._sum,
            "last_error": self._error,
            "enabled": is_cost_enabled(self.entry.options),
        }

    def _snapshot(self) -> dict:
        return {
            "last_hour": self._last_hour,
            "sum": self._sum,
            "last_supply_date": self._last_supply_date,
            "free_used": self._free_used,
        }

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._last_hour = data.get("last_hour")
        self._sum = float(data.get("sum", 0))
        self._last_supply_date = data.get("last_supply_date")
        self._free_used = {str(k): float(v) for k, v in data.get("free_used", {}).items()}
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
        """Write only pricing for the proven hour. Never modify energy cursor."""
        if not is_cost_enabled(self.entry.options):
            return
        async with self._lock:
            try:
                await self._write_hour(hour, records, calibration)
                self._error = None
            except Exception as err:
                self._error = f"{type(err).__name__}: {err}"
                _LOGGER.exception(
                    "PalPulse cost import paused for meter %s at UTC %s. "
                    "The energy importer and its history are unaffected",
                    self.mac, hour.hour_start,
                )

    async def _write_hour(self, hour, records: dict, calibration: float) -> None:
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
        options = self.entry.options
        local_zone = ZoneInfo(self.hass.config.time_zone)
        first_local = datetime.fromtimestamp(start, timezone.utc).astimezone(local_zone)
        first_date = first_local.date().isoformat()
        cap = float(options.get(CONF_FREE_CAP_KWH, 0))
        if cap > 0:
            # A gap or a mid-window activation may consume an unknown portion
            # of today's capped free allowance. Do not assume it was zero.
            gap = self._last_hour is not None and start > self._last_hour + 3600
            preceding = first_local - timedelta(minutes=1)
            started_mid_window = free_window_active(options, preceding)
            if ((gap and first_date == datetime.fromtimestamp(
                    self._last_hour, timezone.utc
                ).astimezone(local_zone).date().isoformat())
                or (self._last_hour is None and started_mid_window)):
                raise ValueError(
                    "Capped free energy allocation cannot be established after "
                    "a missing hour or mid-window activation. Wait for a new "
                    "local billing day; no cost was invented"
                )

        samples = [record for stamp, record in records.items()
                   if start <= stamp < start + 3600]
        usage, dates, free_used = price_verified_hour(
            samples, calibration, options, self.hass.config.time_zone,
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
        self._last_supply_date = charged_date
        # Only retain current and previous day. The full ledger is unnecessary.
        self._free_used = {
            key: value for key, value in free_used.items()
            if key >= first_date
        }
        await self._store.async_save(self._snapshot())
