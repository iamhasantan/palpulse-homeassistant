"""Tariff cost tests, especially free hours and local billing dates."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from pathlib import Path
import importlib
import sys
import types
import unittest

# Load the pure helpers without importing the integration's Home Assistant
# runtime module. Do not affect other tests that import custom_components.
folder = Path(__file__).resolve().parents[1] / "custom_components" / "palpulse"
pkg = types.ModuleType("isolated_palpulse_cost")
pkg.__path__ = [str(folder)]
sys.modules[pkg.__name__] = pkg
cost = importlib.import_module("isolated_palpulse_cost.cost")
pricing = importlib.import_module("isolated_palpulse_cost.pricing")
cost_for_verified_hour = cost.cost_for_verified_hour
supply_charge_for_dates = cost.supply_charge_for_dates
price_verified_hour = cost.price_verified_hour
from isolated_palpulse_cost.pricing import (
    CONF_PRICING_MODE, MODE_TOU, CONF_PEAK_CENTS, CONF_OFFPEAK_CENTS,
    CONF_SHOULDER_CENTS, CONF_SUPPLY_CENTS,
    CONF_WEEKDAY_FREE, CONF_WEEKEND_FREE,
    CONF_WEEKDAY_PEAK, CONF_WEEKDAY_SHOULDER,
    CONF_FREE_CAP_KWH, CONF_FREE_EXCESS_CENTS,
    CONF_WEEKEND_PEAK, CONF_WEEKEND_SHOULDER,
)


def options():
    return {
        CONF_PRICING_MODE: MODE_TOU,
        CONF_PEAK_CENTS: 50.0,
        CONF_SHOULDER_CENTS: 30.0,
        CONF_OFFPEAK_CENTS: 20.0,
        CONF_SUPPLY_CENTS: 120.0,
        CONF_WEEKDAY_FREE: "11:00-14:00",
        CONF_WEEKEND_FREE: "11:00-14:00",
        CONF_WEEKDAY_PEAK: "07:00-10:00,17:00-21:00",
        CONF_WEEKDAY_SHOULDER: "10:00-17:00",
        CONF_WEEKEND_PEAK: "",
        CONF_WEEKEND_SHOULDER: "",
    }


def hour_at(local_hour: datetime, pulses=60):
    utc_hour = local_hour.astimezone(timezone.utc)
    return [
        SimpleNamespace(
            timestamp=int((utc_hour + timedelta(minutes=i)).timestamp()),
            pulses=pulses,
        ) for i in range(60)
    ]


class CostTests(unittest.TestCase):
    def test_weekday_peak_free_offpeak(self):
        tz = ZoneInfo("Australia/Melbourne")
        o = options()
        # 60 pulses/minute, 3600 pulses = 1 kWh for the hour.
        self.assertAlmostEqual(cost_for_verified_hour(
            hour_at(datetime(2026, 11, 9, 8, tzinfo=tz)), 3600, o, "Australia/Melbourne"
        )[0], .5)
        self.assertAlmostEqual(cost_for_verified_hour(
            hour_at(datetime(2026, 11, 9, 12, tzinfo=tz)), 3600, o, "Australia/Melbourne"
        )[0], 0.)
        self.assertAlmostEqual(cost_for_verified_hour(
            hour_at(datetime(2026, 11, 9, 22, tzinfo=tz)), 3600, o, "Australia/Melbourne"
        )[0], .2)

    def test_free_overrides_peak_and_shoulder(self):
        o = options()
        o[CONF_WEEKDAY_PEAK] = "11:00-13:00"
        o[CONF_WEEKDAY_SHOULDER] = "13:00-16:00"
        tz = ZoneInfo("Australia/Melbourne")
        self.assertEqual(cost_for_verified_hour(
            hour_at(datetime(2026, 11, 9, 11, tzinfo=tz)),
            3600, o, "Australia/Melbourne"
        )[0], 0.)

    def test_half_hour_transition_is_precise(self):
        o = options()
        o[CONF_WEEKDAY_FREE] = "11:30-14:00"
        tz = ZoneInfo("Australia/Melbourne")
        total, _ = cost_for_verified_hour(
            hour_at(datetime(2026, 11, 9, 11, tzinfo=tz)),
            3600, o, "Australia/Melbourne"
        )
        # Thirty shoulder minutes and thirty free minutes, each 1/60 kWh.
        self.assertAlmostEqual(total, .15)

    def test_supply_daily_and_dst(self):
        tz = ZoneInfo("Australia/Melbourne")
        o = options()
        # DST spring forward: no extra or missing daily fee.
        _, dates = cost_for_verified_hour(
            hour_at(datetime(2026, 10, 4, 3, tzinfo=tz)),
            3600, o, "Australia/Melbourne"
        )
        charge, date = supply_charge_for_dates(dates, None, o)
        self.assertEqual(charge, 1.2)
        self.assertEqual(date, "2026-10-04")
        self.assertEqual(supply_charge_for_dates(dates, date, o), (0, date))

    def test_red_power5_cap_and_paid_excess(self):
        o = options()
        o[CONF_WEEKDAY_FREE] = "11:00-16:00"
        o[CONF_WEEKEND_FREE] = "11:00-16:00"
        o[CONF_FREE_CAP_KWH] = 15
        o[CONF_FREE_EXCESS_CENTS] = 16.412
        tz = ZoneInfo("Australia/Melbourne")
        amount, dates, used = price_verified_hour(
            hour_at(datetime(2026, 11, 9, 13, tzinfo=tz), pulses=240),
            3600, o, "Australia/Melbourne",
            {"2026-11-09": 14.0},
        )
        # Four kWh consumed. The first kWh is free; the other three cost $0.16412.
        self.assertAlmostEqual(amount, 3 * .16412)
        self.assertAlmostEqual(used["2026-11-09"], 18)
        self.assertEqual(dates, frozenset({"2026-11-09"}))

    def test_free_cap_resets_on_next_local_day(self):
        o = options()
        o[CONF_WEEKEND_FREE] = "11:00-16:00"
        o[CONF_FREE_CAP_KWH] = 15
        o[CONF_FREE_EXCESS_CENTS] = 16.412
        tz = ZoneInfo("Australia/Melbourne")
        amount, _, used = price_verified_hour(
            hour_at(datetime(2026, 11, 8, 12, tzinfo=tz)),
            3600, o, "Australia/Melbourne",
            {"2026-11-07": 20.0},
        )
        self.assertEqual(amount, 0)
        self.assertEqual(used["2026-11-08"], 1.0)

    def test_partial_hour_is_rejected(self):
        tz = ZoneInfo("Australia/Melbourne")
        with self.assertRaises(ValueError):
            cost_for_verified_hour(
                hour_at(datetime(2026, 11, 9, 9, tzinfo=tz))[:-1],
                3600, options(), "Australia/Melbourne",
            )


if __name__ == "__main__":
    unittest.main()
