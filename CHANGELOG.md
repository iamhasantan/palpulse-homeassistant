# Changelog

## Unreleased

## 0.1.0b8 (experimental, opt-in)

### What's Changed

- Restore live Powerpal consumption after an incomplete hourly record, preventing
  a single missing minute from indefinitely stopping the Energy dashboard.
- Allow 15 minutes for late events after a later complete hour, then advance to
  that verified hour without inventing the missing hours' consumption.
- Track a durable accounted-energy sum that includes **only verified complete
  hours**, with compatibility for existing 0.1.0b7 state.
- Await Recorder's queued writes before advancing the live import cursor.
- Expose **PalPulse live history status** diagnostic entity with the last
  event, last imported hour, blocked hour, skipped-hour count, and error details.
- Log a stalled hour only when the blocking hour changes, rather than hundreds
  of duplicate warnings for a single missing record.
- Keep calibration changes, conflicting minute records and real meter-counter
  discontinuities as hard failures.
- Rename Marketplace/HACS, installation text and generated configuration
  entries consistently to **PalPulse for Home Assistant**. Preserve the
  existing `palpulse` domain, per-meter unique identity and statistic IDs.
- Add regression tests for interrupted imports, late events, restart/replay,
  cumulative total continuity, duplicate prevention and official naming.

### Important

- This remains experimental, not billing-grade. Review the Energy dashboard
  after updating. A genuinely missing hour is still unknown and its energy
  must **not** be estimated or attributed to the next hour.
- The old Powerpal source statistic and historical migration ledger are not
  changed by this update.
- The new live health sensor is separate from the migration progress sensors.
- Do not erase or reset the ESP32 or the HA Recorder to recover missing hours.


## 0.1.0b7 (experimental, opt-in)

### What's Changed

- Fix a Home Assistant OS/Supervised backup verification bug. HA's
  `backup_job_id` is a transient job identifier and may differ from the
  actual saved backup ID returned by Supervisor.
- Identify the completed, saved backup by a unique name and verify the
  actual ID, selected destination, Home Assistant contents and Recorder DB
  **before** making any historical statistics changes.
- Prevent a false import failure after a backup was successfully created.
- Expose the underlying failure reason in the migration status entity's
  `last_error` attribute even if failure occurs before a recovery journal
  can be created.
- Add regression tests with deliberately different job and backup IDs,
  missing/invalid backup results and local destination fallback.

### Important

- The original Powerpal and PalPulse statistics are never changed when
  backup creation or verification fails.
- A backup may exist even if an older PalPulse version reported a failed
  verification. Inspect existing backups before manually creating more.
- The actual HA backup failure, if any, must still be inspected through the
  `last_error` attribute or Core logs. No backup failure is bypassed.


## 0.1.0b6 (experimental, opt-in)

### What's Changed

- Add **live PalPulse migration status and progress** entities to the integration,
  showing current phase, verified rows, total hours, percentage and last error.
- Keep the migration persistent notification updated as historical hours are
  verified. Users can monitor long-running backups, imports and verification.
- Detect previous incomplete or failed migration journals on Home Assistant
  restart and show **Interrupted**. No writes resume automatically.
- Add a disabled-by-default **Resume interrupted historical migration** button,
  and make the existing Import button resume safely when an incomplete journal
  already exists.
- Confirm the original Recorder-inclusive backup still exists, validate the
  original source and unchanged PalPulse cutover, and reconcile all previously
  committed target hours before writing.
- Skip every already-present matching hour; only write missing periods, with a
  Recorder read-back after each 240-row batch. Persist confirmed progress.
- Fail closed for changed/unknown target rows, different source data, missing
  backups and conflicting five-minute statistics.
- Add restart and incomplete-commit regression tests, including a multi-batch
  import and the Home Assistant progress entity lifecycle.
- Refuse live integration reloads while a migration operation is running.

### Important

- This is an **opt-in experimental beta**, not a billing-grade migration.
- If a migration is currently running in an older version, **do not restart
  Home Assistant to install this beta**. Wait until its existing operation
  finishes or you have verified its journal and Recorder state.
- New progress entities become available only after installing this beta and
  restarting/reloading the integration.
- The original Powerpal statistics are read-only and existing PalPulse hours
  at and after the cutover are untouched.
- Previously committed hours are never blindly imported again. A mismatched
  data set blocks resume rather than overwriting anything.


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
