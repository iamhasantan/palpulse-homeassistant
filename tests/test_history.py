"""Pure accounting tests; no Home Assistant installation required."""

from __future__ import annotations

from datetime import datetime, timezone
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "custom_components" / "palpulse"))

from history import Record, complete_hour, hourly_statistic, normalize_mac, parse_event  # noqa: E402


def hour_records(hour: int, *, phase: int = 0, base: int = 40000,
                 pulses: int = 10) -> dict[int, Record]:
    return {
        hour + phase + minute * 60:
            Record(hour + phase + minute * 60, pulses, base + (minute + 1) * pulses, 3200)
        for minute in range(60)
    }


class HistoryTests(unittest.TestCase):
    def test_mac_normalisation(self):
        self.assertEqual(normalize_mac("D8:A5:D6:D9:19:64"), "d8a5d6d91964")
        self.assertEqual(normalize_mac("d8-a5-d6-d9-19-64"), "d8a5d6d91964")
        for invalid in ("hello", "aa:bb:cc", ""):
            with self.assertRaises(ValueError):
                normalize_mac(invalid)

    def test_event_validation(self):
        now = 1793744000
        data = {
            "meter": "D8:A5:D6:D9:19:64",
            "timestamp": str(now - 60),
            "pulses": "10",
            "total_pulses": "32000",
            "pulses_per_kwh": "3200",
        }
        self.assertEqual(parse_event(data, "d8a5d6d91964", now).total_pulses, 32000)
        self.assertIsNone(parse_event(data, "ffffffffffff", now))
        self.assertIsNone(parse_event({**data, "timestamp": "0"}, "d8a5d6d91964", now))
        self.assertIsNone(parse_event({**data, "pulses": "99999"}, "d8a5d6d91964", now))
        self.assertIsNone(parse_event({**data, "total_pulses": "1"}, "d8a5d6d91964", now))
        self.assertIsNone(parse_event({**data, "pulses_per_kwh": "nan"}, "d8a5d6d91964", now))

    def test_full_60_minute_hour(self):
        start = 1793728800
        records = hour_records(start)
        h = complete_hour(records, start)
        self.assertIsNotNone(h)
        self.assertEqual(h.consumption_pulses, 600)
        self.assertEqual(h.last_total, 40600)
        state, summation = hourly_statistic(h, 40000, 3200)
        self.assertAlmostEqual(state, 12.6875)
        self.assertAlmostEqual(summation, 0.1875)

    def test_one_missing_record_blocks_import(self):
        start = 1793728800
        records = hour_records(start)
        del records[start + 12 * 60]
        self.assertIsNone(complete_hour(records, start))

    def test_duplicate_or_out_of_order_accounting(self):
        start = 1793728800
        records = hour_records(start)
        corrupted = dict(records)
        ts = start + 300
        corrupted[ts] = Record(ts, 10, 40000 + 6 * 10 + 20, 3200)
        self.assertIsNone(complete_hour(corrupted, start))

    def test_native_phase_and_utc(self):
        start = int(datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc).timestamp())
        self.assertIsNotNone(complete_hour(hour_records(start, phase=37), start))

    def test_counter_wrap_or_reset_refused(self):
        start = 1793728800
        r = hour_records(start)
        h = complete_hour(r, start)
        with self.assertRaises(ValueError):
            hourly_statistic(h, h.last_total + 1, 3200)


if __name__ == "__main__":
    unittest.main()
