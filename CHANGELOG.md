# Changelog

## Unreleased

## 0.1.0b12

### What's Changed

- Add a guided, native Home Assistant **Configure** menu with Overview,
  Electricity pricing, Free electricity hours, Previous cost history,
  Electricity usage history and Historical electricity costs.
- Use HA's time pickers and numeric controls for a simple shared daily free
  electricity window and allowance, preserving advanced weekday/weekend tariffs.
- Replace typed historical cost source IDs with separate pickers for existing
  cumulative statistics or daily-reset sensor entities.
- Provide read-only Preview, a second explicit confirmation for Import and
  Undo, native HA progress UI, and visible completion/error results.
- Keep the original migration coordinator, backup-first safety checks and
  existing buttons unchanged for compatibility.
- Group existing buttons, pricing entities and status sensors under one
  dedicated PalPulse Powerpal device without changing their unique IDs.
- Preserve beta 11 usage and cost statistics, tariff rates and import journals.

### Important

- This is an experimental GUI improvement. It does not start historical imports
  automatically, alter any Recorder statistics or bypass verified backups.
- A migration launched through the GUI may continue or be interrupted if its
  progress view is closed; consult the status sensors and use the guarded
  Resume tool rather than repeating an unchecked import.
- Real Home Assistant 2026.11 frontend testing, migration progress checks and
  a full import/Undo rehearsal on a restored test instance remain outstanding.
  Do not run a live historical import without reviewing Preview and verifying
  a complete Recorder-inclusive backup.


## 0.1.0b11

### What's Changed

- Expose an independent **Historical daily cost import** configuration menu
  alongside electricity pricing without resetting either setting.
- Always show dedicated Preview, Import, Resume and Undo electricity-cost
  migration controls on the PalPulse integration's Entities page.
- Accept previous cumulative monetary hourly sums or, explicitly, a verified
  daily-reset monetary sensor with actual hourly closing-state history.
- For daily-reset sources, import only complete local calendar days and verify
  observed midnight resets, including 23/25-hour daylight-saving days. Skip
  incomplete, inconsistent or missing days without estimating bills.
- Preserve immutable original usage/cost histories, and retain independent,
  backup-first cost migration and targeted Undo.
- Refuse changes to the old cost source while an import journal is active.
- Document the separate cost import workflow and simplify the README introduction.
- Correct the Marketplace catalogue integration name in default-branch metadata,
  keeping **PalPulse for Home Assistant** in the manifest and hacs.json.
- Preserve the legacy integration-title migration fix from beta 10; this does not
  change the saved Marketplace device name or existing update entity IDs.
- Bundle the historical energy migration, Red Energy tariff support, historical
  monetary cost import and daily-reset conversion into one reviewed release.

### Important

- Daily-reset migration needs Recorder **hourly closing states**, not hourly
  averages, current-price sensors or template values lacking historic data.
- No import is allowed without an existing live PalPulse cost baseline, an
  explicit Preview, and a verified Recorder-inclusive Home Assistant backup.
- Historical amounts are not recalculated using today's tariff. No migration
  is started automatically. This beta includes experimental features and requires
  live HA validation against Recorder data before using Import.


## 0.1.0b10 (experimental, opt-in)

### What's Changed

- Fix the persisted PalPulse integration title reverting to the former
  **PalPulse Historical Energy** label after an update or restart.
- Migrate only PalPulse's known automatically generated old entry titles
  to **PalPulse for Home Assistant** on integration setup.
- Preserve user-customized entry titles, BLE meter identity, existing
  historical energy/cost statistics, and migration journals.
- Keep the separate Home Assistant Marketplace repository/device name and
  existing update entity IDs untouched. Marketplace owns these saved entries,
  so users may need to rename the Marketplace device once in Home Assistant.
- Add name-migration regression tests and troubleshooting instructions.
- Retain beta 9's opt-in Australian tariffs, Red Energy capped free periods,
  cumulative cost statistics, and backup-first historical cost migration.

### Important

- This is an experimental pre-release, not billing-grade software.
- Restart Home Assistant after installing the new integration code, then
  verify the integration title on Settings > Devices & services.
- This update does not change existing cost/usage histories or automatically
  start either historical migration.
- If only the Marketplace repository device displays the old name, it may
  need a separate manual rename. PalPulse does not modify Marketplace data.


## 0.1.0b9 (experimental, opt-in, review only)

### What's Changed

- Add optional Australian flat and time-of-use electricity tariffs, retaining
  Home Assistant managed prices as the untouched default.
- Price each complete Powerpal minute at its actual Australian local timestamp,
  including daylight saving, peak, shoulder and off-peak boundaries.
- Support Red Energy Red Power5 free windows (11:00-16:00), daily 15 kWh
  allowance, and the paid rate for usage above the allowance. Other providers
  can configure their own weekday/weekend periods and free caps.
- Generate a separate external monetary cost statistic for Home Assistant's
  **Use an entity tracking total costs** option. Existing PalPulse kWh
  statistics and original Powerpal sensor remain unchanged.
- Add a separate opt-in, backup-first historical monetary migration. It copies
  old cumulative cost statistics before the live PalPulse cost cutover,
  preserving original hourly changes without repricing them at today's rates.
- Give the cost migration its own Preview, Import, Resume, Undo controls,
  durable journal, progress reporting and verification.
- Cover capped free use, paid overflow, DST, gaps and cost-history safeguards
  with pure unit tests.

### Important

- All rates and the daily supply charge must be verified against the actual
  electricity bill. The old Red Power5 16.412 c/kWh value and unverified
  A$1.25/day supply estimate are not guaranteed current.
- Daily-reset display sensors or price-per-kWh sensors cannot substitute for
  genuine cumulative historical cost statistics.
- Cost migration leaves missing historical periods missing, not estimated.
- Controlled-load, demand, block-pricing, season/holiday exceptions and feed-in
  credits remain out of scope pending separate validation.
- Experimental: review and test on a non-production instance before deployment.
  Neither migration nor code changes are merged into main by this PR.


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
