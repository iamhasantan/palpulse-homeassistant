"""Deterministic tests for flat, TOU, validation, and Melbourne DST."""
from datetime import datetime
from zoneinfo import ZoneInfo
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "custom_components/palpulse/pricing.py"
spec = importlib.util.spec_from_file_location("palpulse_pricing_pure", path)
pricing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pricing)
p = pricing
TZ = ZoneInfo("Australia/Melbourne")


def tou():
    return {
        p.CONF_PRICING_MODE: p.MODE_TOU,
        p.CONF_PEAK_CENTS: 42.135,
        p.CONF_SHOULDER_CENTS: 30,
        p.CONF_OFFPEAK_CENTS: 22.6,
        p.CONF_SUPPLY_CENTS: 115.5,
        p.CONF_WEEKDAY_PEAK: "07:00-09:00, 17:00-20:00",
        p.CONF_WEEKDAY_SHOULDER: "09:00-17:00,20:00-22:00",
        p.CONF_WEEKEND_PEAK: "",
        p.CONF_WEEKEND_SHOULDER: "",
    }


class PricingTests(unittest.TestCase):
    def test_default_preserves_native_home_assistant(self):
        self.assertIsNone(p.price_for_datetime({}, datetime(2026, 11, 9, 17, tzinfo=TZ)))
        self.assertIsNone(p.daily_supply_dollars({}))

    def test_flat_and_supply_price_in_dollars(self):
        opts = {p.CONF_PRICING_MODE: p.MODE_FLAT, p.CONF_FLAT_CENTS: 28.55,
                p.CONF_SUPPLY_CENTS: 123.4}
        self.assertEqual(p.price_for_datetime(opts, datetime(2026, 11, 9, 12, tzinfo=TZ)), 0.2855)
        self.assertEqual(p.daily_supply_dollars(opts), 1.234)

    def test_weekdays_weekends_and_boundary(self):
        opts = tou()
        self.assertEqual(p.price_for_datetime(opts, datetime(2026, 11, 9, 7, tzinfo=TZ)), 0.42135)
        self.assertEqual(p.price_for_datetime(opts, datetime(2026, 11, 9, 9, tzinfo=TZ)), 0.3)
        self.assertEqual(p.price_for_datetime(opts, datetime(2026, 11, 9, 22, tzinfo=TZ)), 0.226)
        self.assertEqual(p.price_for_datetime(opts, datetime(2026, 11, 7, 18, tzinfo=TZ)), 0.226)
        self.assertEqual(p.daily_supply_dollars(opts), 1.155)

    def test_dst_uses_wall_clock_without_fixed_utc_offset(self):
        opts = tou()
        for day in (datetime(2026, 4, 6, 17, tzinfo=TZ),
                    datetime(2026, 11, 9, 17, tzinfo=TZ)):
            self.assertEqual(p.price_for_datetime(opts, day), 0.42135)

    def test_reject_overlaps_and_bad_intervals(self):
        opts = tou()
        for value in ("24:00-24:00", "20:00-06:00", "17:00-22:00",
                      "not a time", "00:00-00:00"):
            opts[p.CONF_WEEKDAY_SHOULDER] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.validate_pricing(opts)

    def test_reject_invalid_rates(self):
        for value in (-1, float("nan"), float("inf"), "nope"):
            opts = {p.CONF_PRICING_MODE: p.MODE_FLAT,
                    p.CONF_FLAT_CENTS: value, p.CONF_SUPPLY_CENTS: 0}
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.validate_pricing(opts)

    def test_allow_all_day_windows(self):
        opts = tou()
        opts[p.CONF_WEEKDAY_PEAK] = "00:00-24:00"
        opts[p.CONF_WEEKDAY_SHOULDER] = ""
        p.validate_pricing(opts)
        self.assertEqual(p.price_for_datetime(opts, datetime(2026, 11, 9, 0, tzinfo=TZ)), 0.42135)


if __name__ == "__main__":
    unittest.main()
