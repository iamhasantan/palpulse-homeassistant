"""Pure tests for checkpoint telemetry and safe replay eligibility."""

from __future__ import annotations

import importlib
import unittest

import test_importer  # set up narrow Home Assistant stubs

cp = importlib.import_module("custom_components.palpulse.checkpoint")
NOW = 1794447000
MAC = "d8a5d6d91964"


def event(**updates):
    data = {
        "meter": "D8:A5:D6:D9:19:64",
        "checkpoint_timestamp": str(NOW - 60),
        "total_pulses": "120000",
        "ack_timestamp": str(NOW - 360),
        "ack_total_pulses": "119600",
        "queue_size": "3",
        "reliable_history": "true",
        "history_events": "true",
        "nvs_ready": "true",
        "checkpoint_dirty": "false",
        "replay_fault": "false",
        "buffer_oldest": str(NOW - 7200),
        "buffer_newest": str(NOW - 60),
    }
    data.update(updates)
    return data


class ESPCheckpointTests(unittest.TestCase):
    def test_valid_meter_checkpoint_can_offer_non_destructive_replay(self):
        r = cp.parse_checkpoint_event(event(), MAC, now=NOW)
        self.assertIsNotNone(r)
        self.assertEqual(r["ack_timestamp"], NOW - 360)
        self.assertEqual(r["total_pulses"], 120000)
        self.assertEqual(cp.replay_eligible(r, NOW)[0], True)

    def test_foreign_or_corrupt_events_are_ignored(self):
        self.assertIsNone(cp.parse_checkpoint_event(event(meter="ff:ee:dd:cc:bb:aa"), MAC, now=NOW))
        self.assertIsNone(cp.parse_checkpoint_event(event(total_pulses="119500"), MAC, now=NOW))
        self.assertIsNone(cp.parse_checkpoint_event(event(reliable_history="yes"), MAC, now=NOW))
        self.assertIsNone(cp.parse_checkpoint_event(event(queue_size="513"), MAC, now=NOW))
        self.assertIsNone(cp.parse_checkpoint_event(event(ack_timestamp=str(NOW + 7200)), MAC, now=NOW))

    def test_replay_rejected_for_stale_faulted_unsaved_or_unacknowledged(self):
        valid = cp.parse_checkpoint_event(event(), MAC, now=NOW)
        self.assertFalse(cp.replay_eligible(valid, NOW + 301)[0])
        for key, val in [
            ("nvs_ready", "false"),
            ("checkpoint_dirty", "true"),
            ("replay_fault", "true"),
            ("reliable_history", "false"),
        ]:
            with self.subTest(field=key):
                data = event(**{key: val})
                if key == "reliable_history":
                    data["ack_timestamp"] = "0"
                    data["ack_total_pulses"] = "0"
                r = cp.parse_checkpoint_event(data, MAC, now=NOW)
                self.assertIsNotNone(r)
                self.assertFalse(cp.replay_eligible(r, NOW)[0])

    def test_missing_powerpal_buffer_is_not_recovered(self):
        older = cp.parse_checkpoint_event(
            event(buffer_oldest=str(NOW - 240)), MAC, now=NOW,
        )
        self.assertFalse(cp.replay_eligible(older, NOW)[0])
        self.assertIn("rolled", cp.replay_eligible(older, NOW)[1])


if __name__ == "__main__":
    unittest.main()
