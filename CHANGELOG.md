# Changelog

## Unreleased

## 1.0.1b3

### What's Changed

#### Private-beta interrupted cost journal recovery

- **Permanent fix:** New repair journals pin only the dated tariff versions, current contract rates and billing timezone. Unrelated ESPHome, migration and dashboard settings no longer interrupt a valid cost correction, while real tariff changes remain protected.
- **Private-beta compatibility:** Automatically revalidate a protected 1.0.1b2 cost-repair journal when a settings revision changes, rather than interpreting every integration option edit as an electricity tariff change.
- Independently recalculate **each originally approved hourly charge** from the currently saved dated tariff, original source energy and the journal's immutable pre-repair monetary records. Only an exact match is eligible for continuation, including TOU, free-energy cap, and daily supply fee.
- Verify the original and any later live-tail Recorder-inclusive backup, detect source-energy drift and external Recorder edits, and classify original, already corrected, or originally missing rows before rebinding the stale revision.
- Preserve partially written journals and their original backups when rate evidence changes, a backup is missing, an unrelated Recorder write appears, or any hourly amount differs. Never silently restart, double-charge, or reset a meter checkpoint.
- If a stalled journal has **no modified cost rows**, existing safe supersession can retire it and allow a fresh user preview without discarding historical Recorder changes.
- Keep this compatibility path in the dedicated `private_beta_journal_recovery.py` module. It is intended for cleanup before public release, not for relaxing normal HA/Recorder integrity safeguards.
- Add regression tests for partial historical replays, tariff changes, energy drift, concurrent monetary edits, live-tail preservation and missing backups.

Only the private-beta legacy recovery path changes. Standard fresh-user tariff calculation, cost reconciliation safeguards, energy statistics and ESPHome firmware remain unchanged.

## 1.0.1b2

### What's Changed

#### Full-day cost recalculation

- Add a native **Recalculate an entire billing day** workflow in the Hourly history panel. It previews each hour's verified energy, original charge (when independently verifiable), and date-effective recalculated price.
- Require a completely recorded 23-, 24-, or 25-hour local billing day, a verified monetary anchor and end-of-day checkpoint. Apply one daily supply fee, the actual free-energy cap and historical TOU bands. Do not guess any missing energy or tariff.
- Use the existing administrator-confirmed, Recorder-inclusive backup, digest, journal, atomic batch verification and safe resume workflow. Carry the exact end-of-day correction into newer recorded cost totals without changing their original hourly charges.
- Keep unrelated historical repairs and ESP32 firmware unchanged. Incomplete monetary evidence refuses the repair rather than inventing cost.

## 1.0.1b1

### What's Changed

#### Cost recovery and Recorder baseline integrity

- Recognise migrated Recorder cost histories where cumulative `state` and `sum` use different but independently verified baselines. Confirm the offset against the adjacent earlier cost hour and its recorded increment, without assuming that `state == sum` for all valid historical data.
- Preserve both verified cumulative monetary counters during backup-first repair. Existing original hourly charges and the anchor's values remain untouched. Refuse unexplained counter-offset changes, resets or missing original evidence.
- Show the exact blocked/verified cost-anchor timestamp, state, sum, offset, prior-hour values, recorded change and refusal reason in the **Self-heal missing electricity costs** read-only assessment. When assessment is blocked, show **Not assessed**, not a misleading count of zero recoverable hours.
- Recheck both the anchor `state` and `sum` before any repair or interrupted-journal resume. Retain existing digest, Recorder-inclusive backup, exact-target verification, crash-resumable journalling and no-double-charge safeguards.
- Extend tests for consistent migrated offsets, inconsistent or missing evidence, changing monetary counters, protected backup, original-row preservation and crash recovery.

This beta does **not** rewrite arbitrary cost baselines, backfill unavailable energy or bypass billing safety checks. If a cost anchor is still blocked after upgrading, read the detailed evidence first and do not force a reset. Home Assistant Core 2026.11.1+ is required.

## 0.9

### Feature preview

PalPulse 0.9 consolidates the existing integration into a feature-focused preview release. There are no changes to the underlying energy, cost, meter-history or recovery algorithms in this version update.

- **Energy monitoring:** Local Powerpal BLE readings, live power, verified hourly and cumulative energy statistics for Home Assistant.
- **Electricity costs:** Flat and time-of-use tariffs, configurable free-power allowances, dated tariff profiles and Home Assistant cost statistics.
- **Data continuity:** Acknowledged ESPHome delivery, buffered replay, durable checkpoints, missing-hour detection and guarded recovery from independently verified meter evidence.
- **Historical tools:** Backup-first import, preview, migration, reconciliation and journalled recovery for provable energy and cost gaps.
- **Dashboard:** Integrated health, tariff configuration, usage history, diagnostics, ESP32 recovery and CSV export.
- **Privacy:** Local integration without requiring a cloud account.

**Requirements:** Home Assistant Core 2026.11.1 or newer and compatible PalPulse ESPHome firmware for reliable-history transport. This remains an experimental preview. Back up Home Assistant including Recorder before upgrading. Earlier release notes and historical recovery records are preserved below.

## 0.4b3

### Default-on acknowledged ESPHome history

- Automatic per-meter ACK action discovery using the same MAC identity hash as the ESP32 firmware. Does not require users to copy the action name. Detects actions registered after Home Assistant starts and refuses ambiguous matches.
- Settings shows a **Disable automatic history acknowledgements** checkbox (unchecked by default), discovered service status and actionable warnings when firmware history cannot be acknowledged. Existing explicit overrides remain available under advanced options.
- Both automatic and manual buffered-history replay use the actual discovered service. Existing NVS checkpoints, ACK cursors, energy and monetary Recorder statistics, and history migration or cost-repair journals are preserved.
- Added automated coverage for the firmware hash, delayed ESPHome registrations, duplicate devices, opt-out controls, native options settings and UI interactions. Home Assistant Core 2026.11.1+ and compatible ESPHome reliable-history firmware remain required.

## 0.4b2

### Missing cost recovery

- Separate missing-cost-only repair from full contract reconciliation. Independently price absent cost hours using Recorder-verified kWh, stored historical tariff versions, and proven capped free-power consumption. Never infer missing consumption.
- Preserve existing original hourly monetary charges. Rebase later cumulative state and sum after comparing each frozen original Recorder row, and verify writes in 12-hour batches with a crash-resumable Store journal.
- Require a verified Recorder-inclusive backup and refuse stale previews, unverified source energy, conflicting monetary rows, overlapping migration or incomplete automatic legacy repair.
- Provide admin-only missing-cost preview and backup-first confirmation directly inside **Hourly history**. Small fully supported gaps may recover automatically after normal cost retries; incomplete legacy jobs and larger repairs require explicit review.
- Keep legacy cost-reconciliation source and previous backups. A successfully replaced earlier journal is marked superseded without deleting its original evidence.
- Run regression checks for midnight supply allocation, tariffed free hours, invalid source evidence, and dashboard confirmation.

**Requires HA Core 2026.11.1+.** Experimental beta. Take a completed backup including Recorder before upgrading and check actual Recorder results after restart.

## 0.4b1

### What's Changed

- **Forward integrity audit:** Overview and Hourly history distinguish mature missing Recorder energy, missing cost, pending cost and unpriceable hours. A 2h15m settlement grace avoids false alarms while ESPHome catches up; no missing kWh are guessed.
- **Backlog-safe cost self-healing:** Process up to 168 hourly cost candidates at a time starting from the saved cost checkpoint rather than skipping straight to the latest week after an extended outage. Existing Recorder conflict checks remain intact.
- **Legacy history controls:** Historical cost reconciliation is hidden under Settings > Advanced historical tools by default. Existing journals, Recorder rows, backups, tariffs and repair code remain unchanged.
- **Regression coverage:** Recent hourly cutover/missing/queued/unpriceable cases, long cost backlogs and advanced sidebar visibility.

### Safety and acceptance

Requires Home Assistant Core 2026.11.1 or newer. This remains experimental beta software. Back up Home Assistant including Recorder before updating. This release does not rewrite historic Recorder values, delete old repair journals or backups, reset ESPHome NVS, or invent missing data. Historical cost reconciliation is still available through Advanced historical tools. GitHub CI verifies code and simulated recovery paths; a live Home Assistant restart and evidence check remain recommended.


## 0.3b8

### What's Changed

- **Home Assistant 2026.11.1 bootstrap timeout fixed.** PalPulse no longer awaits pending energy import inside its config-entry setup. Home Assistant's Recorder could be waiting on startup while PalPulse was itself waiting for Recorder, leading to a stage-2 bootstrap cancellation and a red **Failed to set up** integration card. Metering and cost recovery now run in entry-owned background jobs only after Home Assistant reports it has started, or immediately on reload if it is already running.
- **Incoming ESPHome minutes preserved and ordered during startup.** A single config-entry lifetime event worker queues original Powerpal measurement payloads FIFO during HA bootstrap. The worker starts after HA is running and invokes the existing guarded durable importer. No event is acknowledged before processing; meter and cost cursors, pending Store records, and historical repair journals are unchanged by this startup fix. Config-entry background tasks are cancelled on unload.
- **No startup-blocking event task pile-up.** Each incoming ESPHome event is added to the in-memory FIFO instead of launching a normal Home Assistant startup-tracked task that waits behind a Recorder lock.
- **Independent cost recovery.** The persistent electricity-cost writer resumes in its own entry-lifetime task, so a long energy backlog does not delay cost retry scheduling.
- **Use Recorder's database executor.** The exact reads flagged by Home Assistant at `__init__.py:539` and `cost_recorder.py:368`, plus the energy integrity scan, now use `get_instance(hass).async_add_executor_job`. Neither the default executor nor the event loop is appropriate for synchronous Recorder statistics.
- **Bounded Recorder wait.** Import confirmation now fails after 180 seconds rather than awaiting Recorder forever; the durable energy cursor is not advanced without read-after-write confirmation. Further retry follows the existing ledger safety checks.
- **Regression coverage.** Startup returns even while a simulated Recorder recovery stalls; early ESPHome events remain queued until HA starts; the existing Recorder-failure cursor invariants are retained.

### Safety and acceptance

Requires **Home Assistant Core 2026.11.1 or newer**. This release does not reset ESP32 NVS, delete pending energy history, modify original cost-repair journals, or silently write historical cost corrections. ESPHome reliable-history delivery is recommended for restart durability. Test coverage cannot reproduce the user's actual HAOS Recorder load, so verify startup and last imported hour after updating.


## 0.3b7

### What's Changed

- **One guided fix for energy changed since cost preview.** A failed historical cost repair now offers **Assess and fix changed energy** in the PalPulse Cost reconciliation tab. This performs a read-only comparison of the original, saved monetary snapshots with currently recorded hourly meter energy and the saved contract-effective tariff, listing the changed dates, hours and costs.
- **Automatically recover when independently verifiable.** If all original planned monetary hours still have verifiable evidence and the reconstructed plan is safe, an administrator may explicitly confirm **Back up, rebuild and continue safely**. PalPulse verifies a *new* Recorder-inclusive backup and all original/previously written monetary rows, creates an immutable archived copy of the earlier journal and original backup, then rebuilds and resumes only its own cumulative cost statistics from the newest energy snapshot. Earlier written rows can be adjusted again without double billing.
- **No guesswork or unsafe automatic writes.** No backup or monetary write is attempted if the energy review is stale, an original Recorder cost was independently changed, meter/contract data cannot support repricing or a new billing hour's existing cost was not covered by the original snapshot. Missing historical readings remain missing rather than being assumed free. Explicitly unsafe original plans cannot be bypassed.
- **Restart-resilient revision history.** A rebased journal retains its original plan/archive, previous approved monetary targets and old backup reference. This allows later interrupted writes to be verified against the original and earlier approved values before any retry. Prepared-but-rebased journals with previously committed rows are never auto-aborted by the ordinary unused-preview cleanup.
- **Clearer dashboard recovery.** Shows affected local hours, prior and revised charges, missing data counts, and a confirmation step. If full auto-fix is unsafe it explains what evidence is missing, pointing to the Hourly history recovery before reassessment.
- **Regression tests.** Simulated 12/24 partial repairs with recovered meter history, financially equivalent Recorder metadata changes, stale approval rejection, original cost tampering, missing source energy, and new-backup failure protection, plus browser action tests.

### Safety and acceptance notes

Requires **Home Assistant 2026.11.1**. This is an experimental, backup-first historical monetary repair feature. It never resets NVS, the meter or historical energy. The repair stops if current Recorder evidence cannot be reconciled with the original frozen journal, and still requires explicit user approval for monetary changes. The real Home Assistant Recorder must be checked after installation; CI does not establish that the user's incomplete 12/66-hour repair has finished.


## 0.3b6

### What's Changed

- **Fix the 12/66 paused repair from earlier beta formats.** In 0.3b3–0.3b5, the resume writer treated an absent `repair_safe` field in a 0.3b1/0.3b2 journal as a definitively unsafe cost plan. Older saved partial repairs can now be independently audited against their immutable pre-repair monetary values, original source classifications, all measured energy hours and the actual date-effective tariff. Only when EVERY original intended cost row agrees with that independent reconstruction can the existing verified backup and journal safely resume. This also supports genuine legacy cost-counter decreases with independently proven energy.
- **No bypass of an actual safety failure.** A repair whose journal explicitly sets `repair_safe: false` is never upgraded by this compatibility check. The specific first unverified cost-break hour is surfaced instead, with all previously verified rows preserved.
- **No duplicate billing or journal reset.** The original preview digest and backup remain authoritative. Already-committed cost rows must still match either their old or corrected state and sum before the next verified batch can be written. Any mismatch, incomplete historical evidence, invalid old monetary row or changed energy fingerprint stops without touching Recorder.
- **More precise recovery diagnostics.** Report whether a failed repair was verifying its original backup, auditing old-format cost history, protecting newly recorded cost hours, verifying Recorder write batches or updating the live cost checkpoint.
- **Regression coverage.** Simulate a verified 12/24 partially written legacy journal lacking the modern safety field, proven historical cost decreases, altered original cost amounts, false source classifications, and explicit unsafe verdicts. Confirm unsafe attempts never write new Recorder rows.

### Safety and acceptance

Requires **Home Assistant 2026.11.1**. Back up Home Assistant including Recorder before upgrading; keep all PalPulse repair Store files. This is a compatibility upgrade, not an instruction to reset an ESP32, undo historical cost corrections, or bypass a genuine missing-hour safety block. The dashboard will display the precise reason if an old frozen plan cannot be validated. The user's real Home Assistant Recorder repair still requires acceptance testing before it can be considered completed.


## 0.3b5

### What's Changed

- **Stopped historical repairs are no longer shown as stuck at 12/66.** The Cost reconciliation page now surfaces the actual failure and stage instead of hiding the exception behind the old paused-journal message. Retains verified progress, original backup and replay journal across Home Assistant restarts.
- **Correct resume counts and prompt.** A resumed job starts at its last confirmed row rather than 0/0. The dashboard shows **Continue previous repair** with the original dates and verified row count. Automatic polling increases to every 15 seconds only while a correction is actually running; the browser may be closed without interrupting the Home Assistant task.
- **Live hours recorded after the original preview.** If the cost writer has advanced beyond the saved repair range, PalPulse now creates and verifies an *additional Recorder-inclusive backup* of the newer hours. It then journals a guarded extension, preserving those hours' original cost differences while carrying only the proven correction to their cumulative state/sum. It does not reprice those later hours or reset the live daily free/supply ledger.
- **Safer crash recovery.** A resumed repair checks the original and supplementary backups, old or already-corrected Recorder rows, live cumulative writer checkpoint, and unchanged energy fingerprint. Missing or conflicting live data pauses with an explicit reason. Existing repaired rows are verified, never double-charged.
- **Regression tests.** Verify a second-batch interruption, continued live metering, supplementary backup, preserved daily free/supply context, inconsistent live checkpoint refusal, and visible failed-resume status with exact error.

### Test and safety notes

Requires **Home Assistant 2026.11.1**. This patch is intended for the saved 2026-11-10 through 2026-11-13 interrupted 12/66-hour repair. It preserves the saved original plan instead of silently deleting it. The integration still requires an explicitly approved, digest-matched and backup-first historical correction; it cannot reconstruct truly missing native minute measurements. The failure status is read-only, and if a live counter fails validation Recorder is not altered by the new extension. CI is not a substitute for acceptance testing against the user's actual Home Assistant Recorder.


## 0.3b4

### What's Changed

- **One date range for the whole electricity contract.** Reconciliation no longer imposes a 14-day limit. Any period from the earliest saved contract-effective tariff date through the current local billing date is eligible, with the actual saved tariff profile selected separately for each measured hour. Dates before the first known tariff are still refused rather than invented.
- **Simpler dashboard.** The Cost reconciliation tab automatically starts at the first saved tariff date and offers a **Use full contract term** shortcut, one full-range read-only cost preview, and one backup-first **Fix selected dates** action. No need to split a contract into fortnightly corrections.
- **Keep the browser responsive.** After a confirmed, digest-matched preview, the existing guarded cost writer now runs as a Home Assistant task. It validates the Recorder and source tariff, makes a verified Recorder-inclusive backup, journals its work, and shows verified progress in 12-hour batches. Closing the dashboard does not cancel the HA task.
- **Fix stranded old repair journals.** An earlier failed cost correction is no longer a silent date-range dead end. If every original monetary Recorder row is *provably unchanged* after the Recorder queue drains, the obsolete journal is safely marked aborted and the newly selected range may proceed. If any historical row differs, the dashboard shows the saved dates and a **Resume previous repair** action using the original journal and verified backup instead. Never erase partially applied history.
- **Safe restart.** The repair journal persists through Home Assistant restarts; status and previous dates remain visible. The server serializes launch/resume attempts and re-checks tariff revision, original energy rows, cumulative monetary rows and the live checkpoint before committing any repair.
- **Smaller previews.** Large plans remain server-side; the frontend receives the per-day cost summary, risk flags, progress and immutable digest instead of thousands of raw write rows.
- **Regression testing.** September-to-November contract spans, safe untouched-journal replacement, refusal to discard a changed Recorder row, crash-resume and dashboard full-range/resume click paths.

### Important safety and test notes

Requires **Home Assistant 2026.11.1** and retains all PalPulse 0.3b3 protections. The plan-effective rates must be verified against your electricity contract. Saving the tariff and previewing costs never change Recorder. Only a separately confirmed correction creates a backup and applies verified source-supported cost hours. An unsafe, partially written older journal cannot be silently abandoned; it must be resumed or reviewed. Live Home Assistant and Powerpal hardware acceptance remains required.


## 0.3b3

### What's Changed

- **Legacy cost-counter resets are visible and repairable when evidence supports them.** Home Assistant's November 11, 7 am (AEDT) decrease in the cumulative cost sum previously raised `Invalid existing cost counter`, preventing even a preview. PalPulse now creates a read-only audit of original decreases, missing-cost gaps, invalid monetary values and exact local timestamps.
- **Counter resets are never treated as free electricity.** A discontinuity in the old monetary statistic does not imply $0 usage. The planner independently prices hours from verified cumulative energy and the effective Red Energy tariff; uncertain hours remain explicitly missing, with an incomplete-source label rather than a fabricated cost difference.
- **Strict repair eligibility.** A signed old cost baseline remains intact. A cost-counter reset may be corrected only when independently verified energy and the effective tariff provide a replacement charge. Missing or nonfinite counter values or unverified reset-hour energy block historical writes before a backup or Recorder mutation. Historical correction still requires a digest-matched preview, explicit confirmation, verified Recorder-inclusive backup, durable journal and read-after-write checks.
- **Correct billing-day presentation.** Days with incomplete original cost history show partial recorded totals and no false recorded-versus-proposed difference. Every source counter discontinuity is listed with its timestamps and original monetary sums.
- **Latest live checkpoint guard.** A preview that stops before the currently recorded cost checkpoint remains read-only; include the latest recorded billing date before attempting to apply a cost correction, so the live writer's cumulative sum does not silently diverge.
- **ESPHome settings clarity.** Show 'Not enabled / no ACK' instead of a misleading 1 January timestamp when reliable history is disabled. Correct the native ACK-action and statistic-source input placeholders and instructions.
- **Regression coverage.** Add Nov 11 cost reset detection, backup-first correction when all source energy is independently verified, no-write blocks for missing energy and invalid old cost statistics, and existing end-to-end dashboard click-path validation.

### Experimental preview and acceptance

Requires **Home Assistant 2026.11.1**. Recommended ESPHome firmware remains **0.2b4**. The user's Red Power5 tariff rates and contract effective date must be independently verified. Recover clearly provable missing energy hours before previewing historical cost repair, then preview from Nov 9 **through the current billing date**, not an earlier day that excludes the live cost checkpoint. Never use cost-counter reset previews as proof of a paid invoice. Back up Home Assistant including Recorder. Corrections are opt-in and blocked when independent evidence is insufficient. Hardware and live Recorder acceptance remain outstanding.


## 0.3b2

### What's Changed

- **Cost reconciliation handles signed historical baselines.** An earlier finite negative cumulative monetary sum is preserved as a valid signed starting offset. The 0.3b1 preview incorrectly rejected these with `No valid earlier monetary baseline`. An invalid or corrupt earlier row now yields a read-only preview with an explicit warning and blocks any Recorder correction.
- **Show exactly where ESPHome history stops flowing.** The native PalPulse dashboard independently counts actual `esphome.palpulse_record` and `esphome.palpulse_checkpoint` events received per Powerpal meter, displays the last event and valid checkpoint timestamp, and shows rejected checkpoint telemetry. Counters start afresh on integration reload.
- **Firmware BLE link visibility.** Accept optional `ble_connected` status from PalPulse ESPHome 0.2b4 and prevent replay while the Powerpal BLE link is disconnected. Earlier firmware events without that flag remain compatible.
- **Always subscribe to checkpoint events.** Listen on the Home Assistant event bus regardless of whether the optional sidebar HTTP component is available. This prevents the panel lifecycle from suppressing real meter telemetry.
- **Regression coverage.** New signed monetary baseline, corrupt-baseline no-write, and legacy/new ESPHome checkpoint tests.

### Experimental preview

Requires Home Assistant **2026.11.1**. Recommended ESPHome **0.2b4** with `history_events: true` and `api.homeassistant_services: true` to emit a checkpoint heartbeat even during temporary meter BLE disconnections. Also enable **Allow the device to perform Home Assistant actions** for ESPHome. Buffered ACK and replay need `reliable_history: true`, `api.custom_services: true` and the actual unique ACK action. Preserve ESP32 NVS, and back up Home Assistant including Recorder before upgrading. The cost reconciliation preview never modifies Recorder; actual corrections remain independently confirmed and backup-first. Real hardware acceptance remains outstanding.


## 0.3b1

### What's Changed

- **Requires Home Assistant 2026.11.1.** The Marketplace minimum is raised, and manual installations of older Core versions are rejected when Core reports its version.
- **Retailer-effective tariff dates.** Specify when a rate plan actually started in the sidebar Electricity tab. Versioned profiles preserve earlier effective dates, in the Home Assistant billing timezone. Saving a backdated profile NEVER automatically rewrites historical Recorder statistics.
- **Repair 9–12 November costs.** The new Cost reconciliation tab defaults to the last four local billing days and accepts a bounded 14-day range. Preview the existing monetary charges, proposed recomputed charges and difference per day, based on the saved effective tariff and actual Recorder hourly energy. Peak pricing begins at the configured 16:00 and free power is allocated across the whole local date up to the 15 kWh cap.
- **Best effort without fabricated readings.** Reconstruct known fixed-rate hours from genuine cumulative energy statistics even when the minute-level transport missed events. Leave unknown free-cap allocation or genuinely missing energy explicitly flagged and retain existing unverified charges instead of silently inventing a price.
- **Protected corrections.** Only after the administrator reviews a digest-matched preview and confirms does PalPulse create and verify a Home Assistant Recorder-inclusive backup. A durable journal and per-batch read-after-write checks then update its own cost statistic's cumulative *state and sum*, followed by the live cost checkpoint. Interrupted writes are resumable without doubling a charge. Active migrations, unsafe conflicts and pending cost jobs block writes.
- **Historical energy gap recovery.** Include the separate backup-first, journalled repair for isolated previously skipped energy hours from PR #41. This only offers exact single-hour gap recovery where adjacent physical meter counters prove the omission. Multi-hour or otherwise ambiguous gaps remain unmodified.
- **Regression tests.** Added tariff version, Nov 9 4pm peak / 11am–4pm free cap, prior underbilling, dashboard click path, backup, changed-source refusal and simulated crash-resume tests.

### Experimental preview and limitations

Back up Home Assistant (including Recorder history) before installing. Verify the actual contract start date and Red Energy charges before reconciling. The dashboard preview does not alter Recorder. A confirmation performs an additional verified backup. Cost corrections are not retailer billing statements; uncertain free-cap allocations and genuinely missing energy cannot become exact without further evidence. Existing backup-first historical Import / Resume / Undo flows are unchanged. Real Home Assistant 2026.11.1 browser, ESP32 and physical Recorder acceptance testing remains required.


## 0.2b8

### What's Changed

- **Best-effort exact energy.** Recover verified hourly usage when up to 58 interior minutes are missing but first/last counter endpoints agree, or up to 59 when the preceding hour's durable final counter provides the first boundary. Retain the original timestamp, detect counter conflicts and refuse fabricated allocations.
- **Recover more actual costs.** Price sparse counter-verified hours that fall entirely in one tariff band, including a tracked capped-free interval. Recover other missing fixed-band cost hours from adjacent Home Assistant Recorder energy states only when physical and cumulative deltas agree. Never overwrite existing different Recorder cost statistics.
- **Repair legacy blocked tariff jobs.** Once the owner enters the real positive excess rate and corrects the known Red Power5 evening-peak omission, upgrade only uncommitted matching cost jobs and account for confirmed historic energy. Preserve the earlier tariff provenance for unrelated profiles.
- **Self-healing replay and retry.** On startup and every five minutes, retry persisted energy and cost work independently of new ESP32 events. With ESPHome 0.2b3 acknowledged firmware and the verified ACK service, request safe buffered replay when a saved checkpoint is behind. Avoid the ACK deadlock caused by genuine native-minute gaps.
- **Correct Red Power5 setup.** The Red Power5 guided wizard includes 4pm-9pm evening peak windows; the dashboard warns when a nonzero peak rate lacks time windows. Show recorded cost repair counts.

### Experimental preview

Back up Home Assistant including Recorder/history before upgrading; preserve the ESP32 NVS and original YAML. This is a beta, not a retailer-verified bill. No already-committed history or cost rows are rewritten, and missing data beyond Powerpal's buffer may remain unrecoverable. Uncertain free-cap allocations, mixed-band sparse hours, earlier skipped historical gaps and Recorder conflicts are reported rather than guessed. Live HAOS and Powerpal hardware acceptance remain outstanding.


## 0.2b7

### What's Changed

- **Edit tariffs without leaving PalPulse.** The sidebar Electricity screen now lets administrators edit flat and time-of-use rates, daily supply, free daily allowances, excess rates and all weekday/weekend free, peak and shoulder windows. The Settings screen includes ESPHome history transport, verified Energy cost-link preferences and original migration sources.
- **Safe saves.** All changes use server-side tariff validation, per-meter permission checks, edit conflict protection and migration-source locks. The integration reloads to record the new tariff boundary without overwriting past Recorder statistics.
- **Connect verified costs.** A confirmed button links an actual PalPulse cost statistic to an unpriced matching Home Assistant Energy grid source. Another provider's existing tariff or cost choice is never overwritten.
- **Resume provably free within-cap hours.** Historical queued hours with complete genuine minute measurements can be priced as zero while still inside an independently known allowance, even if a legacy excess rate was zero. Over-cap or uncertain hours remain blocked. Unrelated paid hours can continue.
- **Recorder executor fix.** The panel queries statistics using Home Assistant's Recorder executor. Dashboard save and button actions have automated regression coverage.

### Experimental preview

**Back up Home Assistant including Recorder/history before updating.** Confirm any positive excess rate against your retailer's actual plan; PalPulse does not guess it. A missing free-hour interval may make later daily allowance usage unknowable, so the cost writer refuses to invent charges. Do not retry an old history import just to fill a gap. The separate historical Import, Resume and Undo operations still use their original backup-first protected Home Assistant flow. Actual Home Assistant 2026.11 browser and Powerpal/ESP32 testing is still required.


## 0.2b6

### What's Changed

- **Fix the PalPulse sidebar controls.** The navigation tabs, Refresh, settings links, CSV download, backup request and ESP32 replay actions now respond to clicks.
- **Keep the fix tested.** New click-path tests check the actual button interactions, not just JavaScript syntax.
- **Refresh the dashboard script.** Updating this beta gives the sidebar a new asset URL so Home Assistant can load the corrected version instead of a cached script.

### Experimental preview

Back up Home Assistant including Recorder/history before updating. The button fix does not modify existing energy readings, historical migration records, pricing settings, or ESP32 firmware. Backup and recovery requests still need confirmation and should be checked for successful completion.

## 0.2b5

### What's Changed

- **PalPulse in your Home Assistant sidebar.** A dedicated control centre brings energy recording, electricity prices, cost problems, history and backups together, without needing a manual dashboard import.
- **See the gaps.** View recent hourly Recorder readings, identify missing electricity or cost records, and export an audit CSV. Missing values aren't shown as free or zero-priced usage.
- **ESP32 checkpoint recovery.** With the companion ESPHome 0.2b2 firmware and acknowledged-history setup, inspect saved meter and ACK checkpoints and request a guarded replay of readings still buffered by Powerpal. A replay request does not reset checkpoints or rewrite old statistics.
- **Safer updates and migration.** Request a Home Assistant backup from the panel, then verify it completed before using the existing Preview, Import or Undo tools.

### Experimental preview

Back up Home Assistant **including Recorder/history** before installing this build. Preserve your ESP32 firmware configuration and NVS data before upgrading the companion firmware. The new sidebar is for testing and is available to Home Assistant administrators. Real-meter and browser validation are still required. Buffered-history recovery cannot recreate readings that the meter has already discarded or guess time-of-use costs for incomplete minutes. Existing historical migrations and statistic identifiers remain unchanged.

## 0.2b4

### What's Changed

- **Safer cost history recovery.** PalPulse can now recognise an already-recorded electricity cost hour when its saved checkpoint falls behind Recorder. It checks the corresponding energy readings, tariff and recorded charge before advancing the checkpoint, without rewriting the original cost row.
- **Better error messages.** The Red Energy Power5 setup now points to the specific invalid rate when a capped free allowance has a zero excess rate. It also explains conflicting peak and shoulder schedules instead of making you guess.
- **Clearer troubleshooting.** When cost history cannot be safely reconciled, diagnostics show which existing hours are involved so they can be reviewed without deleting or changing Recorder data.

### Experimental preview

**Back up Home Assistant, including Recorder/history, before updating.** This beta is for testing, not billing. Automatic recovery is intentionally limited to previously recorded, independently verified paid hours. Missing free-power measurements, ambiguous tariffs and mismatched costs remain blocked rather than estimated. Historical imports, original energy statistics and ESPHome firmware are not changed by this release. Check the cost import status after upgrading.

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

## 0.1.0.1b15

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

## 0.1.0.1b14

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

## 0.1.0.1b13

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

## 0.1.0.1b12

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


## 0.1.0.1b11

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


## 0.1.0.1b10 (experimental, opt-in)

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

## Initial beta development (0.1.0.1b1, unpublished)

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
- **Automatic history ACK by default:** Resolve the correct ESPHome per-meter ACK action using the firmware's exact FNV-1a MAC identity, without manual settings for new or existing installations. Recheck when ESPHome services register after startup, and refuse ambiguous matching devices.
- **Explicit opt-out checkbox:** Settings now offers **Disable automatic history acknowledgements**, unchecked by default. Existing explicitly configured actions keep working, and an advanced manual override remains for special setups.
- **Recovery diagnostics:** Display effective action, discovery status and missing-action warnings. Automatic and manual checkpoint replay use the currently resolved action, not only previously saved manual configuration.
- **No metering reset:** The change does not modify Powerpal NVS, durable ACK timestamp, Recorder statistics or monetary repair journals. Compatible ESPHome firmware still needs `history_events: true`, `reliable_history: true`, `api.homeassistant_services: true` and `api.custom_services: true`.
