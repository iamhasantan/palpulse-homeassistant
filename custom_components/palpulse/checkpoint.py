"""Validate read-only ESPHome checkpoint status before offering history replay.

ESP32 cumulative counters and ACK cursors are not interchangeable with Home
Assistant Recorder statistics. Never infer or write missing hourly energy here.
"""

from __future__ import annotations

import re
import time

from .history import normalize_mac

MAX_QUEUE_SIZE = 512
MAX_AGE_SECONDS = 300
MAX_FUTURE_SECONDS = 120


def _flag(value) -> bool:
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    raise ValueError("ESPHome checkpoint flag is invalid")


def parse_checkpoint_event(data: dict, meter: str, *, now: int | None = None) -> dict | None:
    """Accept only a real, internally consistent report for this meter."""
    if now is None:
        now = int(time.time())
    try:
        if normalize_mac(str(data.get("meter", ""))) != normalize_mac(meter):
            return None
        checkpoint = int(data["checkpoint_timestamp"])
        counter = int(data["total_pulses"])
        ack = int(data["ack_timestamp"])
        ack_pulses = int(data["ack_total_pulses"])
        queued = int(data["queue_size"])
        oldest = int(data.get("buffer_oldest", 0))
        newest = int(data.get("buffer_newest", 0))
        flags = {
            key: _flag(data[key]) for key in (
                "reliable_history", "history_events", "nvs_ready",
                "checkpoint_dirty", "replay_fault",
            )
        }
        if not (
            0 <= ack <= checkpoint <= now + MAX_FUTURE_SECONDS
            and 0 <= ack_pulses <= counter < 2**64
            and 0 <= queued <= MAX_QUEUE_SIZE
            and 0 <= oldest <= newest <= now + MAX_FUTURE_SECONDS
        ):
            return None
        if not flags["reliable_history"] and (ack or ack_pulses):
            return None
        return {
            "checkpoint_timestamp": checkpoint,
            "total_pulses": counter,
            "ack_timestamp": ack,
            "ack_total_pulses": ack_pulses,
            "queue_size": queued,
            "buffer_oldest": oldest,
            "buffer_newest": newest,
            **flags,
            "received_at": now,
        }
    except (ValueError, KeyError, TypeError, OverflowError):
        return None


def replay_eligible(report: dict | None, now: int) -> tuple[bool, str]:
    """Only permit a re-read of Powerpal's still-buffered ACK cursor."""
    if report is None:
        return False, "No ESP32 checkpoint report. Install matching ESPHome firmware and check connection."
    if now - int(report["received_at"]) > MAX_AGE_SECONDS:
        return False, "ESP32 checkpoint report is stale."
    if not report["history_events"] or not report["reliable_history"]:
        return False, "Enable acknowledged history events in ESPHome first."
    if not report["nvs_ready"] or report["checkpoint_dirty"]:
        return False, "ESP32 checkpoint is not confirmed saved to NVS."
    if report["replay_fault"]:
        return False, "ESP32 reported a replay fault. Check BLE logs; no automatic cursor reset."
    if report["ack_timestamp"] > report["checkpoint_timestamp"]:
        return False, "ESP32 ACK is newer than its saved meter checkpoint."
    if report["buffer_oldest"] and report["ack_timestamp"] and (
        report["buffer_oldest"] > report["ack_timestamp"] + 60
    ):
        return False, "Powerpal buffer has rolled past the acknowledged cursor."
    return True, "Ready to ask ESPHome to re-read buffered measurements."


def derive_replay_service(ack_service: str) -> str | None:
    """Allow only a sibling ESPHome per-meter action, never an arbitrary call."""
    if not re.fullmatch(
        r"esphome\.[a-z][a-z0-9_]*_palpulse_ack_[0-9a-f]{8}",
        ack_service,
    ):
        return None
    return ack_service.replace("_palpulse_ack_", "_palpulse_replay_")
