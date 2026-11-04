# Changelog

## Unreleased

## 0.1.0b5 (experimental, opt-in)

### What's Changed

- Fix the unhelpful "Legacy cumulative energy decreases" preview error.
- Preview now reports every reversal in the original Home Assistant hourly
  cumulative energy sum, including the first ten affected timestamps,
  previous and new sums, and raw meter states, in HA's local timezone.
- Send a persistent Home Assistant notification instead of simply rejecting
  the Preview button with a generic error.
- Keep Import deliberately blocked if any old cumulative sums decrease.
  No attempt is made to reinterpret resets, guess readings, or silently
  modify the original Powerpal history.
- Reset any earlier preview authorisation when another preview fails.
- Add regression tests confirming invalid history never creates a backup,
  never arms an import and never writes to Recorder.

### Important

- This is diagnostic only. It does not automatically repair anomalous
  legacy energy statistics.
- The original Powerpal entity, stored history, PalPulse statistic and
  Energy dashboard configuration are unchanged by Preview.
- The separate migration and Undo buttons still require a completed,
  independently verified Home Assistant backup containing Recorder first.

## 0.1.0b4 (experimental, opt-in)

### What's Changed

- Add disabled-by-default Home Assistant buttons to preview, migrate, and undo
  historical Powerpal totals in the new PalPulse statistic.
- Copy legacy hourly readings only up to (not including) the first existing
  PalPulse data point. Never overwrite the new statistic's existing hours.
- Shift old cumulative sums to join the new statistic's zero-sum baseline.
- Require and verify a completed Home Assistant backup including the Recorder
  database before the first import and again before Undo.
- Keep the original Powerpal sensor and all its historical data untouched.
- Store a durable undo journal. Undo clears only the rows this migration
  inserted, using Home Assistant's time-range Recorder API.
- Reject changed, overlapping or invalid data rather than guessing or deleting.

### Important

- This is an **experimental beta**. Do not install it on production without a
  verified independent backup and a review of the migration preview.
- The old history may contain existing catch-up spikes. Those are copied as
  recorded, not silently rewritten.
- If your original source has gaps, PalPulse does not invent missing readings.
- Reversing the migration is a button action, not a full HA restore.

## 0.1.0b3

### What's Changed

- Publish versioned updates through HACS / Home Assistant Marketplace instead
  of displaying Git commit hashes.
- Show clear release notes for each update.
- Require Home Assistant 2026.11.0b2 or newer.

### Notes

- Experimental beta, not billing-grade.
- No changes to the ESP32 firmware, Powerpal sensors, or energy accounting.

## 0.1.0b2

- Fix event-loop thread safety when receiving ESPHome Powerpal history events.
  Previously, Home Assistant 2026.11 rejected incoming events before PalPulse
  could import them, leaving the historical statistic missing.
- Add an event listener regression test.
- Publish a versioned beta release for Marketplace update testing.
- No firmware changes or energy source reconfiguration required.
- Existing dropped records are not automatically recovered. Start with a
  complete, valid new UTC hour before checking historical statistics.

## Initial beta development (0.1.0b1, unpublished)

- Build a HACS-installable PalPulse companion integration with UI configuration
  for a specific Powerpal BLE device.
- Add strict parsing of native-minute Powerpal events with meter/calibration checks.
- Require full UTC-hour measurement continuity before importing any energy
  statistics, and stop at missing or contradictory data.
- Persist processed times and a deduplicated measurement ledger.
- Import named external energy statistics through Home Assistant's supported
  Recorder API with a zero-sum initial baseline.
- Add standalone accounting tests and GitHub Actions validation.

**Not production ready:** firmware event delivery and end-to-end HA Recorder
behaviour require hardware and Home Assistant validation.

- Document compatibility with Home Assistant 2026.11's built-in Marketplace,
  and retain HACS instructions for older Home Assistant releases.

- Clarify Home Assistant 2026.11 Marketplace's first-install hot-loading
  behaviour: PalPulse has a config flow, so a restart prompt is not expected.
  Marketplace handles restart notifications after an update or removal.
- Remove outdated HACS/Marketplace GitHub-topic setup warning.

- Keep project-facing documentation independent of predecessor branding, while
  linking the ESPHome project's engineering provenance record.
- Explain that Marketplace's update entity is not an energy sensor, and where
  to inspect the external hourly energy statistics after configuring a meter.

- Add native HA diagnostic sensors showing migration stage and percentage, verified
  rows, total rows, last error, backup ID and resumability. Update persistent
  progress notifications at approximately 10% intervals.
- Resume interrupted imports using the existing Import action or a distinct
  Resume button, only after revalidating the original source, backup, cutover,
  and every already-written Recorder row.
- Reconcile the actual Recorder state after crashes, skip matching existing
  hours, and persist confirmed batch counts before continuing.
- Fix outdated historical-decrease test expectations and add restart/recovery,
  duplicate prevention, changed data, backup-loss and status tests.
