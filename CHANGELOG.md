# Changelog

## Unreleased

## 0.2b3

### What's Changed

- **More reliable electricity costs.** Pending cost records are retried after Home Assistant restarts and every five minutes, even when the ESP32 has not sent a new measurement.
- **Help for a $0 Energy dashboard.** PalPulse can check whether Home Assistant is using the recorded cost statistic. You can choose to link a verified PalPulse cost source and optionally keep that specific link working. Existing prices or other cost sources are never replaced automatically.
- **Clearer status.** See delayed or missing cost hours, outstanding retries and whether the Energy dashboard is connected to PalPulse's cost statistic.
- **Fewer false gaps.** Changing a dashboard-link or migration setting no longer gets mistaken for a change to your electricity tariff.

### Experimental preview

This build is **experimental**. **Back up Home Assistant, including Recorder/history, before upgrading.** Test live readings and costs against your electricity plan and retailer bill. Known missing hours without the original minute-level evidence remain unpriced rather than guessed. Your existing energy statistics and historical migration records are not rewritten.

## 0.2b2

### What's Changed

- **A cleaner PalPulse device page.** Keep live recording and cost health visible, but hide duplicated migration buttons, finished progress indicators and the optional daily supply charge sensor. Existing entity IDs, automations and recorded data are left intact.
- **Simpler Configure menu.** Everyday options now appear as Status, Set up PalPulse, Electricity prices, History and migration, and Advanced. The full migration, backup-first Import/Undo, connection checks and ESP32 recovery tools are still available in their sections.
- **Respect existing customisations.** A one-time tidy-up hides only unmodified PalPulse auxiliary entities from older releases. Users who renamed or previously hid their entities are left alone. Manually showing them again persists through restarts.
- **Less dashboard clutter.** The sample dashboard no longer depends on completed-migration progress entities or the optional daily supply sensor. Existing user-created dashboards are not changed.

### Before updating

This is an **experimental preview**. Back up Home Assistant, including Recorder/history, before upgrading or using migration tools. No historical data, pricing, ESPHome firmware, or migration journals are modified by this interface cleanup. If an automation needs a hidden action entity, it remains available and can be shown in Home Assistant's entity settings.

## 0.2b1

### What's Changed

- **New-user migration:** Select your previous energy statistic instead of relying on the original PalPulse author's sensor name. Preview still validates the meter's counter at cutover and requires a Recorder-inclusive backup before Import.
- **ESP32 replacement:** A guided, one-time, 24-hour counter transition preserves existing recorded energy sums when replacing the ESP32 for the same Powerpal meter. It does not invent energy from missing time.
- **Optional confirmed history delivery:** With the matching 0.2b1 ESPHome component and the configured native ACK action, incoming minutes are acknowledged only after Home Assistant has durably stored them. Missing data can be replayed only while it remains in the meter buffer.
- **Cost resilience:** Complete cost hours are journalled for retry, tariff settings are saved with queued hours, and unknown capped-free periods no longer prevent unrelated paid hours from recording.
- **Safer tariff setup:** The generic free-power form no longer assumes Red Energy's default hours or allowance. Capped offers require a positive excess rate.
- **Better diagnostics and onboarding:** Added connection checks, delivery acknowledgments, ESP32 replacement guidance and a much shorter public README.

### Preview precautions

- **Back up Home Assistant including Recorder/history** before upgrading, importing history or accepting an ESP32 counter transition. Keep your current ESPHome firmware and NVS checkpoint before reflashing.
- The acknowledged transport and ESP32 replacement features are **experimental** and need real-device testing. Existing installations continue using their previous event path until acknowledged transport is explicitly configured.
- No existing historical energy or cost gaps are automatically repaired by this release. Never re-run old history imports just to fix a gap.
- Cost estimates are not billing-grade. Changes in tariff within an hour are conservatively left unpriced rather than estimated; verify actual plans and charges with the retailer.

## 0.1.0b15

### What's Changed

- Recover **exact energy consumption** for a completed UTC hour when at most
  four *interior* Powerpal native-minute records are missing, using verified
  cumulative meter-counter readings at both hourly boundaries.
- Require monotonic counter continuity, matching observed one-minute pulses,
  a stable sample phase, and a full following hour plus the existing late-event
  grace period before accepting a sparse hour. Missing endpoints, uncertain
  meter data, whole missing hours and counter resets remain unfilled.
- Retain beta 14 Recorder sum reconciliation, read-back verification and
  no-overwrite rules. Existing energy migration journals, historical statistics
  and original Powerpal entities are not altered.
- Keep tariff costing strict: **no charge is inferred for missing individual
  minutes**. Sparse energy-only hours contribute exact kWh, but do not create
  invented monetary statistics for that hour.
- Fix capped-free-tariff accounting so a missing *paid* interval does not
  suppress subsequent paid electricity costs for the rest of the day. Only
  overlapping missing free-window minutes make that day's capped-free
  allowance uncertain. Paid hours outside the uncertain free window continue
  to be priced from verified minute readings.
- Persist uncertain capped-free dates across restarts and handle local date
  boundaries and daylight-saving transitions using UTC-minute timestamps.
- Add the `sparse_recovered_hours` and `last_sparse_utc_hour` diagnostic
  attributes, plus `free_cap_uncertain_dates` on cost recorder status.
- Extend counter continuity, missing-boundary, sparse recovery and
  capped-free-window accounting regression tests.

### Important

- This release **prevents avoidable future gaps**. It does **not** fill existing
  missing Home Assistant Recorder hours or reprice earlier billing periods.
  Older 9-11 November 2026 energy and cost gaps still need separate verification
  against source readings and a Recorder-inclusive backup before any repair.
- An exact sparse-hour kWh amount does not imply exact minute-by-minute tariff
  cost. This beta deliberately favours incomplete cost history over an
  unverified monetary estimate.
- Home Assistant/Recorder, ESPHome transport and source event health must
  still be checked if complete hours remain absent. Existing counter errors
  continue to halt unsafe imports.
- Features remain experimental. Validate a real HA 2026.11 instance and
  compare charges against actual retailer bills before treating the cost
  statistics as billing-grade.

## 0.1.0b14

### What's Changed

- Fix a major PalPulse live-energy continuity bug that could cause an
  impossible, multi-thousand-kWh negative hourly change after an earlier
  historical Powerpal migration.
- Reconcile every new live energy hour against Recorder's previous committed
  cumulative sum, **only if the previous raw meter state agrees** with the
  durable Powerpal cursor. A stored pulse-derived baseline is no longer
  allowed to silently replace a rebaselined Recorder sum.
- Persist the verified Recorder sum offset, preserve complete-hour and gap
  checks, prevent overwriting conflicting existing hours, and verify each
  committed Recorder row before advancing the importer cursor.
- Add a read-only **Energy history continuity check** under Configure, with
  detected anomalous intervals, measured electricity usage, recorded sum
  change and candidate correction. It never repairs history automatically.
- Bundle the cost-migration prerequisites wizard from PR #21, with separate
  old-source and live-cost checks, Check Recorder again, pricing shortcuts
  and recoverable Preview screens instead of misleading error popups.
- Add regression tests for the exact 9 November 2026 +11,179.097525 kWh
  discontinuity, restart continuity, invalid Recorder states and no-overwrite
  protection.

### Important

- This version fixes **future live writes**. It does **not** automatically
  repair historical negative sum rows created by older betas. Before manually
  adjusting the affected hour, verify a complete Recorder-inclusive backup
  and inspect adjacent hourly Recorder statistics.
- A single large negative anomaly may be repairable using Home Assistant's
  existing Adjust sum interface with the **correct hourly consumption**,
  not a cumulative total or lifetime offset. Do not adjust if more than one
  anomaly or a meter-state conflict exists.
- The original historical import, cost import and recovery journals stay
  unchanged. No imports or Undo operations are started automatically.
- All features remain experimental, and real Home Assistant validation is
  still required before enabling historical cost Import/Undo.

### Historical cost migration readiness wizard

- Replace the missing PalPulse cost statistic dead-end with a native,
  read-only prerequisites screen showing the old source, live cost statistic,
  cost writer state and clear next actions.
- Distinguish an invalid/missing historic source from custom pricing disabled,
  missing verified live cost rows, and cost recorder errors.
- Add **Check Recorder again**, **Configure electricity pricing**,
  **Change previous cost source**, and **Review PalPulse status** actions.
- Route the Import shortcut through the same preflight and Preview requirements.
- Suppress duplicate persistent notifications when Preview runs through
  the native GUI. Legacy entity Preview buttons remain unchanged.
- Never generate synthetic cost baselines, create historical statistics or
  run import/backup actions during readiness checks.
- Add regression tests for pending/ready/bad cost states and GUI retry.

## 0.1.0b13

### What's Changed

- Guide first-time users to choose a historical cost source before Preview,
  instead of showing a confusing persistent-notification error.
- Add a native Home Assistant **Guided setup wizard** for Red Energy Power5,
  Australian flat rates, custom time-of-use or HA native pricing.
- Suggest the known Red Power5 daily free-power window and cap, while requiring
  paid rates and supply charge from the user's current bill. All values are
  reviewed before saving.
- Offer a separate optional old-cost-source selection with a visual
  cumulative-statistics or daily-reset sensor picker, without starting Import.
- Add a **PalPulse dashboard setup** options page with personalised statistic
  IDs and a linked Lovelace YAML template.
- Include an optional three-view **PalPulse Control Centre** dashboard using
  built-in HA statistic, graph, entity and gauge cards. No extra HACS frontend
  component or unstable programmatic dashboard writes.
- Keep Import, Resume and Undo off the dashboard and preserve the existing
  preview, confirmation, Recorder backup, cutover and recovery safeguards.
- Retain all beta 12 entity IDs, energy/cost Recorder statistics and historic
  migration journals.

### Notes and precautions

- This is still experimental software. Historical monetary values must be
  reviewed against actual Red Energy bills, not assumed correct.
- The Lovelace dashboard is an **optional one-time manual import**. Follow
  Configure > PalPulse dashboard setup; no existing dashboards are overwritten.
- The configuration wizard does not import any historical rows. Use the
  separate cost migration Preview before considering any Import.
- Live HA 2026.11 GUI, dashboard and cost import/recovery verification are
  still required on a test instance before using mutation actions in production.

### GUI cost-source onboarding

- Show the selected historical cost source and migration status at the top of
  the **Historical electricity costs** menu.
- Put **Choose your previous cost source** first, ahead of Preview and Import.
- Redirect first-run cost Preview into Home Assistant's visual source-selection
  wizard when no old cost source has been configured, rather than producing a
  persistent notification.
- Correct the legacy button's message with the actual Home Assistant
  **Devices & services > Integrations > PalPulse > Configure** navigation.
- Keep import/undo confirmation, backup checks and recorder history unchanged.

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
