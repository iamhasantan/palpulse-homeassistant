"""Persistent per-meter cost writer independent from PalPulse's energy cursor.

Experimental and not billing-grade. Existing energy statistics never change.
Only complete, timestamp-verified minutes are priced, with a conservative
daily-free-allowance guard after gaps. Recorder writes are verified before
durable checkpointing, and duplicate replay is reconciled.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import math
import time
from types import SimpleNamespace

from .history import CompletedHour, Record
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics, statistics_during_period,
)
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .cost import (
    SparseTariffBoundaryError, free_dates_between, is_cost_enabled,
    price_counter_verified_hour, price_verified_hour, supply_charge_for_dates,
)
from .pricing import (
    CONF_PRICING_MODE, CONF_FLAT_CENTS, CONF_PEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
    CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS, WINDOW_FIELDS,
    price_for_datetime, free_window_active,
)

_LOGGER = logging.getLogger(__name__)

_TARIFF_FIELDS = frozenset({
    CONF_PRICING_MODE, CONF_FLAT_CENTS, CONF_PEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_OFFPEAK_CENTS, CONF_SUPPLY_CENTS,
    CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS,
    *WINDOW_FIELDS,
})


def _tariff_options(options) -> dict:
    """A non-tariff setting must not falsely create a pricing transition."""
    return {k: options[k] for k in _TARIFF_FIELDS if k in options}


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
        self._unsubscribe_retry = None
        self._last_retry_utc: int | None = None
        self._retry_count = 0
        self._energy_cost_link = "unknown"
        self._energy_cost_guidance = "Checking Home Assistant Energy settings"
        self.energy_importer = None
        self._recorder_conflict: dict | None = None
        self._reconciled_cost_hours = 0
        self._last_reconciled_utc_hour: int | None = None
        self._repaired_pending_tariffs = 0
        self._energy_backfill_hours = 0

    @property
    def status(self) -> dict:
        last_energy = (
            self.energy_importer.health.get("last_completed_utc_hour")
            if self.energy_importer is not None else None
        )
        lag_hours = (
            max(0, int((last_energy - self._last_hour) // 3600))
            if last_energy is not None and self._last_hour is not None
            else None
        )
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
            "last_retry_utc": self._last_retry_utc,
            "recovery_attempts": self._retry_count,
            "energy_dashboard_cost_link": self._energy_cost_link,
            "energy_dashboard_cost_guidance": self._energy_cost_guidance,
            "energy_to_cost_lag_hours": lag_hours,
            "recorder_conflict": self._recorder_conflict,
            "reconciled_cost_hours": self._reconciled_cost_hours,
            "last_reconciled_utc_hour": self._last_reconciled_utc_hour,
            "repaired_pending_tariffs": self._repaired_pending_tariffs,
            "recovered_cost_from_energy_hours": self._energy_backfill_hours,
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
            "unpriced_hours": sorted(self._unpriced_hours),
            "last_retry_utc": self._last_retry_utc,
            "recovery_attempts": self._retry_count,
            "recorder_conflict": self._recorder_conflict,
            "reconciled_cost_hours": self._reconciled_cost_hours,
            "last_reconciled_utc_hour": self._last_reconciled_utc_hour,
            "repaired_pending_tariffs": self._repaired_pending_tariffs,
            "recovered_cost_from_energy_hours": self._energy_backfill_hours,
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
        self._last_retry_utc = data.get("last_retry_utc")
        self._retry_count = int(data.get("recovery_attempts", 0))
        self._recorder_conflict = data.get("recorder_conflict")
        self._reconciled_cost_hours = int(data.get("reconciled_cost_hours", 0))
        self._last_reconciled_utc_hour = data.get("last_reconciled_utc_hour")
        self._repaired_pending_tariffs = int(data.get("repaired_pending_tariffs", 0))
        self._energy_backfill_hours = int(data.get("recovered_cost_from_energy_hours", 0))
        current = _tariff_options(self.entry.options)
        zone = self.hass.config.time_zone
        if not self._tariff_profiles:
            self._tariff_profiles = [{"from": 0, "options": current, "timezone": zone}]
        elif (
            _tariff_options(self._tariff_profiles[-1]["options"]) != current
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

    async def async_start(self) -> None:
        """Recover committed pending hours without waiting for a new meter event."""
        from homeassistant.helpers.event import async_track_time_interval

        if self._unsubscribe_retry is None:
            self._unsubscribe_retry = async_track_time_interval(
                self.hass, self._periodic_retry, timedelta(minutes=5),
            )
        await self.async_recover_pending()
        await self.async_refresh_cost_link()

    async def async_refresh_cost_link(self) -> None:
        """Diagnose a missing Energy dashboard cost mapping, never change it."""
        try:
            from .dashboard_cost import async_check_cost_link, async_link_verified_cost
            from .const import CONF_AUTO_LINK_ENERGY_COST

            energy_id = f"{DOMAIN}:powerpal_{self.mac}_energy"
            result = await async_check_cost_link(
                self.hass, energy_id, self.statistic_id,
            )
            if (
                result.status == "unlinked"
                and self.entry.options.get(CONF_AUTO_LINK_ENERGY_COST, False)
            ):
                result = await async_link_verified_cost(
                    self.hass, energy_id, self.statistic_id, self,
                )
            self._energy_cost_link = result.status
            self._energy_cost_guidance = result.guidance
        except Exception as err:
            self._energy_cost_link = "unavailable"
            self._energy_cost_guidance = (
                "Cannot read Energy dashboard configuration: "
                f"{type(err).__name__}"
            )

    async def _periodic_retry(self, _now) -> None:
        """A failed Recorder write is retried even after an ESP32/HA outage."""
        await self.async_recover_pending()
        await self.async_refresh_cost_link()

    async def async_recover_pending(self) -> None:
        """Retry the journal, then safely reconstruct absent fixed-band costs."""
        if not is_cost_enabled(self.entry.options):
            return
        async with self._lock:
            self._last_retry_utc = int(time.time())
            self._retry_count += 1
            if self._pending:
                await self._store.async_save(self._snapshot())
                await self._drain_pending()
            # Never bypass a still-blocked durable minute-level cost job.
            if not self._pending:
                await self._recover_from_energy_statistics()
            await self._store.async_save(self._snapshot())

    async def _recover_from_energy_statistics(self) -> None:
        """Use exact adjacent Recorder energy deltas when minute jobs are gone.

        Only confirmed state+sum deltas from consecutive hourly energy rows
        can be costed. A mixed tariff, missing energy, uncertain capped-free
        allocation, or existing different Recorder cost is not rewritten.
        This cannot repair older unpriced rows after a newer cost checkpoint.
        """
        if self._last_hour is None or self.energy_importer is None:
            return
        data = getattr(self.hass, "data", {})
        for key in (f"{DOMAIN}_migration", f"{DOMAIN}_cost_migration"):
            manager = data.get(key, {}).get(getattr(self.entry, "entry_id", None))
            if manager is not None and manager.busy:
                return
        cal = getattr(self.energy_importer, "_calibration", None)
        latest = self.energy_importer.health.get("last_completed_utc_hour")
        if cal is None or not (0 < float(cal) <= 1000000) or latest is None:
            return
        if latest <= self._last_hour:
            return
        energy_id = f"{DOMAIN}:powerpal_{self.mac}_energy"
        # Bound each retry; subsequent five-minute retries continue from
        # whatever verified cost hour was last committed.
        # Walk forward from the durable checkpoint in bounded chunks.
        # Starting at latest - 168h silently stranded older provable costs
        # whenever an outage or recorder backlog exceeded one week.
        begin = self._last_hour
        finish = min(latest, self._last_hour + 168 * 3600) + 3600
        recorded = await self._read(begin, finish, statistic_id=energy_id)
        by_hour = {int(row["start"]): row for row in recorded}
        for stamp in sorted(by_hour):
            if stamp <= self._last_hour or stamp in self._unpriced_hours:
                continue
            previous = by_hour.get(stamp - 3600)
            current = by_hour[stamp]
            if previous is None:
                continue
            try:
                prev_state = float(previous["state"])
                curr_state = float(current["state"])
                delta_sum = float(current["sum"]) - float(previous["sum"])
                delta_state = curr_state - prev_state
                start_pulses = round(prev_state * cal)
                end_pulses = round(curr_state * cal)
                delta_pulses = end_pulses - start_pulses
                if (
                    delta_pulses < 0
                    or not all(math.isfinite(v) for v in (
                        prev_state, curr_state, delta_sum, delta_state,
                    ))
                    or not math.isclose(
                        delta_sum, delta_state, rel_tol=0, abs_tol=1e-5,
                    )
                    or not math.isclose(
                        delta_state, delta_pulses / cal, rel_tol=0,
                        abs_tol=1e-5,
                    )
                ):
                    continue

                if stamp in self._transition_hours:
                    continue
                profile = max(
                    (p for p in self._tariff_profiles
                     if int(p["from"]) <= stamp),
                    key=lambda p: int(p["from"]),
                    default=None,
                )
                if profile is None or any(
                    stamp < int(p["from"]) < stamp + 3600
                    for p in self._tariff_profiles
                ):
                    continue
                job = {
                    "start": stamp, "options": dict(profile["options"]),
                    "timezone": str(profile["timezone"]),
                }
                await self._repair_pending_tariff(job)
                options = job["options"]
                # A configured peak without peak periods is not a complete
                # TOU schedule. Do not reconstruct wrong off-peak costs.
                if (
                    options.get(CONF_PRICING_MODE) == "custom_time_of_use"
                    and float(options.get(CONF_PEAK_CENTS, 0)) > 0
                    and (
                        not options.get("weekday_peak")
                        or not options.get("weekend_peak")
                    )
                ):
                    self._error = "Peak rate has no peak window; complete the tariff to recover costs"
                    return

                hour = SimpleNamespace(
                    hour_start=stamp, missing_minutes=59,
                    consumption_pulses=delta_pulses,
                )
                await self._write_hour(
                    hour, {}, cal,
                    options=options, time_zone=job["timezone"],
                )
                self._energy_backfill_hours += 1
                await self._store.async_save(self._snapshot())
                _LOGGER.info(
                    "PalPulse reconstructed cost for UTC hour %s from "
                    "matching committed Recorder energy counters", stamp,
                )
            except UnpriceableHour as err:
                self._unpriced_hours.add(stamp)
                self._error = str(err)
                await self._store.async_save(self._snapshot())
                continue
            except Exception as err:
                self._error = f"{type(err).__name__}: {err}"
                _LOGGER.warning(
                    "PalPulse Recorder energy cost backfill paused at %s: %s",
                    stamp, self._error,
                )
                return

    async def async_flush(self) -> None:
        if self._unsubscribe_retry is not None:
            self._unsubscribe_retry()
            self._unsubscribe_retry = None
        async with self._lock:
            await self._store.async_save(self._snapshot())

    async def _read(self, start: int, end: int, *, statistic_id: str | None = None) -> list[dict]:
        """Read the real committed statistic, not an estimated Energy chart value."""
        source_id = self.statistic_id if statistic_id is None else statistic_id

        def work():
            return statistics_during_period(
                self.hass,
                datetime.fromtimestamp(start, timezone.utc),
                datetime.fromtimestamp(end, timezone.utc),
                {source_id},
                "hour",
                {},
                {"sum", "state"},
            ).get(source_id, [])
        return await get_instance(self.hass).async_add_executor_job(work)

    async def _recover_verified_unjournalled_hours(
        self, missing: list[dict], next_hour: int,
    ) -> None:
        """Adopt only cost rows mathematically proven from independent energy.

        The legacy writer sometimes commits a cost hour but loses its storage
        checkpoint. A subsequent cost job cannot be written across that hour.
        Reconcile only contiguous same-day, non-free, constant-rate hours with
        a matching verified energy state and sum. Unknown free allocations,
        supply days, changes of rate and mismatched sums remain blocked.
        """
        if self._last_hour is None or self._last_supply_date is None:
            raise ValueError("No durable cost and supply checkpoint to reconcile")

        stored = await self._read(self._last_hour, self._last_hour + 3600)
        if (
            len(stored) != 1
            or int(stored[0]["start"]) != self._last_hour
            or abs(float(stored[0]["sum"]) - self._sum) > 1e-6
        ):
            raise ValueError("Durable cost checkpoint differs from Recorder; no repair attempted")
        cursor = self._last_hour
        running_sum = self._sum
        energy_id = f"{DOMAIN}:powerpal_{self.mac}_energy"
        confirmed = []

        for row in sorted(missing, key=lambda item: int(item["start"])):
            stamp = int(row["start"])
            if stamp != cursor + 3600 or stamp >= next_hour:
                raise ValueError("Unjournalled cost rows are noncontiguous or overlap queued hours")
            if stamp in self._transition_hours:
                raise ValueError("Unjournalled cost hour overlaps a tariff transition")
            profile = max(
                (p for p in self._tariff_profiles if int(p["from"]) <= stamp),
                key=lambda item: int(item["from"]),
                default=None,
            )
            if profile is None or any(
                stamp < int(p["from"]) < stamp + 3600 for p in self._tariff_profiles
            ):
                raise ValueError("Tariff provenance cannot be verified for the cost hour")

            options = profile["options"]
            zone = ZoneInfo(str(profile["timezone"]))
            local_start = datetime.fromtimestamp(stamp, timezone.utc).astimezone(zone)
            # No daily supply fee, free cap or local-date transition may
            # change state when adopting a row without the original minutes.
            if local_start.date().isoformat() != self._last_supply_date:
                raise ValueError("Unjournalled hour may require a daily supply charge")
            rate = None
            for minute in range(60):
                local = datetime.fromtimestamp(
                    stamp + minute * 60, timezone.utc
                ).astimezone(zone)
                if (
                    local.date() != local_start.date()
                    or free_window_active(options, local)
                ):
                    raise ValueError("Unjournalled hour includes free or mixed billing dates")
                price = price_for_datetime(
                    options, local, require_excess_rate=False,
                )
                if price is None or not math.isfinite(price) or price <= 0:
                    raise ValueError("Unjournalled hour has no verifiable paid rate")
                if rate is not None and abs(rate - price) > 1e-12:
                    raise ValueError("Unjournalled hour crosses electricity price bands")
                rate = price
            # Require *both* physical counter states and imported cumulative
            # energy sums to agree. This rejects missing-hour catch-up spikes.
            energy = await self._read(
                stamp - 3600, stamp + 3600, statistic_id=energy_id,
            )
            by_start = {int(item["start"]): item for item in energy}
            if set(by_start) != {stamp - 3600, stamp} or len(energy) != 2:
                raise ValueError("Adjacent verified energy hours are missing")
            previous, current = by_start[stamp - 3600], by_start[stamp]
            delta = float(current["sum"]) - float(previous["sum"])
            physical_delta = float(current["state"]) - float(previous["state"])
            if (
                not math.isfinite(delta)
                or not math.isfinite(physical_delta)
                or delta < 0
                or abs(delta - physical_delta) > 1e-6
            ):
                raise ValueError("Energy counter and cumulative sum do not reconcile")
            expected = round(running_sum + round(delta * rate, 9), 9)
            recorded = float(row["sum"])
            state = float(row["state"])
            if (
                not math.isfinite(recorded)
                or not math.isfinite(state)
                or abs(recorded - expected) > 1e-6
                or abs(state - recorded) > 1e-6
            ):
                raise ValueError("Unjournalled cost does not match verified energy and paid tariff")
            confirmed.append((stamp, recorded))
            running_sum = recorded
            cursor = stamp

        for stamp, recorded in confirmed:
            # Save each verified checkpoint separately. On an interrupted
            # write, retry remains idempotent and never changes Recorder.
            self._last_hour = stamp
            self._sum = recorded
            self._reconciled_cost_hours += 1
            self._last_reconciled_utc_hour = stamp
            await self._store.async_save(self._snapshot())
            _LOGGER.warning(
                "PalPulse adopted existing verified paid cost hour %s for %s "
                "without overwriting Recorder", stamp, self.mac,
            )
        self._recorder_conflict = None
        await self._store.async_save(self._snapshot())

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
                if (
                    len(samples) != 60 - hour.missing_minutes
                    or hour.missing_minutes < 0
                    or hour.missing_minutes > 59
                ):
                    self._error = "Cost hour does not match verified energy samples"
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
                    "missing_minutes": hour.missing_minutes,
                    "samples": [[r.timestamp, r.pulses, r.total_pulses] for r in samples],
                    "options": dict(profile["options"]),
                    "timezone": str(profile["timezone"]),
                })
                self._pending.sort(key=lambda item: int(item["start"]))
                await self._store.async_save(self._snapshot())
            await self._drain_pending()

    async def _repair_pending_tariff(self, item: dict) -> None:
        """Repair only an unambiguous previously unset capped-free excess rate.

        A 0-cent legacy placeholder was never a valid saved tariff. Once the
        owner enters a positive rate, reuse it for queued hours only when
        every other tariff field is identical. Already recorded costs are
        never repriced, and no supply/peak/free window is changed.
        """
        old = dict(item["options"])
        current = _tariff_options(self.entry.options)
        if (
            old.get(CONF_PRICING_MODE) != "custom_time_of_use"
            or float(old.get(CONF_FREE_CAP_KWH, 0)) <= 0
            or float(current.get(CONF_FREE_EXCESS_CENTS, 0)) <= 0
            or (
                float(old.get(CONF_FREE_EXCESS_CENTS, 0)) > 0
                and float(old[CONF_FREE_EXCESS_CENTS])
                    != float(current[CONF_FREE_EXCESS_CENTS])
            )
            or item["timezone"] != self.hass.config.time_zone
        ):
            return

        repairable = set()
        if float(old.get(CONF_FREE_EXCESS_CENTS, 0)) <= 0:
            repairable.add(CONF_FREE_EXCESS_CENTS)
        # A known Power5 setup omission left a peak rate populated without
        # its 4-9pm window. Only accept the explicit corrected Power5
        # schedule, never a generic tariff change.
        if (
            old.get("weekday_free") == "11:00-16:00"
            and old.get("weekend_free") == "11:00-16:00"
            and float(old.get(CONF_FREE_CAP_KWH, 0)) == 15
            and not old.get("weekday_peak")
            and not old.get("weekend_peak")
            and not old.get("weekday_shoulder")
            and not old.get("weekend_shoulder")
            and current.get("weekday_peak") == "16:00-21:00"
            and current.get("weekend_peak") == "16:00-21:00"
            and not current.get("weekday_shoulder")
            and not current.get("weekend_shoulder")
        ):
            repairable.update(("weekday_peak", "weekend_peak"))

        if not repairable or any(
            (old.get(key) or "") != (current.get(key) or "")
            for key in _TARIFF_FIELDS if key not in repairable
        ):
            return
        repaired = dict(old)
        for key in repairable:
            if key in current:
                repaired[key] = current[key]
        from .pricing import validate_pricing
        validate_pricing(repaired)
        item["options"] = repaired
        self._repaired_pending_tariffs += 1
        await self._store.async_save(self._snapshot())
        _LOGGER.warning(
            "PalPulse repaired unset free-excess rate on uncommitted cost job "
            "%s using the owner's verified current rate; no Recorder rows changed",
            item["start"],
        )

    async def _drain_pending(self) -> None:
        for item in tuple(self._pending):
            try:
                await self._repair_pending_tariff(item)
                hour = CompletedHour(
                    int(item["start"]), int(item["first_timestamp"]),
                    int(item["last_timestamp"]), int(item["first_pulses"]),
                    int(item["first_total"]), int(item["last_total"]),
                    missing_minutes=int(item.get("missing_minutes", 0)),
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
            # An old pending entry can survive a crash after a Store save.
            # Remove it only if its corresponding Recorder hour still exists.
            # Otherwise stop and retain the durable recovery job.
            present = await self._read(start, start + 3600)
            if len(present) != 1 or int(present[0]["start"]) != start:
                raise ValueError(
                    "Previously costed Recorder hour is missing; pending job "
                    "retained for review rather than silently discarded"
                )
            return
        # A previous Recorder write may have succeeded just before HA crashed
        # while its Store checkpoint failed. Never skip over those committed
        # but unjournalled cost hours and silently change the cumulative sum.
        if self._last_hour is not None and start > self._last_hour + 3600:
            pending = await self._read(self._last_hour + 3600, start)
            if pending:
                # There may be legitimate rows left by a prior version or
                # migration, but without their original verified minute
                # records we cannot reconstruct tariff/free-cap state.
                # Expose exact boundaries for a backup-first review. Never
                # automatically skip, rebase, overwrite or double-charge.
                first = min(int(row["start"]) for row in pending)
                last = max(int(row["start"]) for row in pending)
                self._recorder_conflict = {
                    "checkpoint_utc_hour": int(self._last_hour),
                    "first_unjournalled_utc_hour": first,
                    "last_unjournalled_utc_hour": last,
                    "existing_hour_count": len(pending),
                    "queued_utc_hour": int(start),
                }
                await self._store.async_save(self._snapshot())
                try:
                    await self._recover_verified_unjournalled_hours(
                        pending, start,
                    )
                except (ValueError, TypeError, KeyError) as err:
                    raise ValueError(
                        f"Recorder has {len(pending)} existing cost hour(s) "
                        "beyond the saved checkpoint. Automatic recovery "
                        f"refused: {err}. Recorder rows were not changed"
                    ) from err
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
        if hour.missing_minutes:
            try:
                usage, dates, free_used = price_counter_verified_hour(
                    hour, calibration, options, time_zone, self._free_used,
                )
            except SparseTariffBoundaryError as err:
                raise UnpriceableHour(str(err)) from err
        else:
            usage, dates, free_used = price_verified_hour(
                samples, calibration, options, time_zone,
                self._free_used,
            )
        supply, charged_date = supply_charge_for_dates(
            dates, self._last_supply_date, options,
            require_excess_rate=False,
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
