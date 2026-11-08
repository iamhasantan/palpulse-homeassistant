# PalPulse for Home Assistant

HACS-installable experimental Home Assistant companion for
[PalPulse ESPHome](https://github.com/iamhasantan/palpulse-esphome).
It receives original timestamped Powerpal BLE records and imports **complete UTC
hours** into Home Assistant Recorder as a **separate, external energy statistic**.

**Early beta, not billing-grade.** Do not replace your working whole-home energy
source or select both sources in Home Assistant's Energy dashboard. This importer
requires an opt-in firmware event sender and real-world validation before use.

**Compatibility for the next release:** Home Assistant **2026.11.0b2 or newer**.
The minimum is declared in `hacs.json`. Existing GitHub release tags retain the
compatibility metadata they were originally published with.

## Install through Marketplace / HACS

The repository uses the standard `custom_components/palpulse` layout and
`hacs.json` packaging. **Home Assistant 2026.11 and newer include HACS as
the built-in Marketplace**. The older standalone HACS integration is replaced.

1. On **HA 2026.11.0b2+**, open **Settings > Marketplace > Custom repositories**
   and add `https://github.com/iamhasantan/palpulse-homeassistant` as an
   **Integration**. Home Assistant 2026.10 and older are not supported by
   builds published with the updated minimum requirement.
2. Install **PalPulse for Home Assistant** after Marketplace/HACS recognises
   the repository. On Home Assistant 2026.11 and later, a **first installation**
   of a config-flow integration can be loaded immediately. **No restart prompt
   is expected**. If you see **Add service**, continue to step 3.
3. Open **Settings > Devices & services > Add integration > PalPulse for
   Home Assistant**. Enter the **Powerpal BLE meter MAC**, not the ESP32 Wi-Fi MAC.
4. Update your PalPulse ESPHome YAML as described below and rebuild/test it.
5. In the ESPHome integration options for your proxy, enable **Allow the device
   to perform Home Assistant actions**. This is essential for event delivery.
6. Listen to `esphome.palpulse_record` under Developer Tools > Events before
   selecting the new historical statistics source.

**Beta status:** the source is available on `main`. This is an
experimental integration: validate timestamped imports before selecting the
external statistic for grid consumption. Never select both the old Powerpal
entity and the new external statistic.

### Experimental migration and Undo (0.1.0b4 through 0.1.0b6)

**New in 0.1.0b6:** Visual status and percentage are shown while importing,
with a safe explicit resume path after an interrupted or failed import. See
**Visual migration status, progress and interruption recovery** below.
Never restart an actively running older migration solely to install this update.

**Earlier 0.1.0b5 behaviour:** If the Preview button previously failed with
`Legacy cumulative energy decreases`, it now completes a read-only
diagnostic. Check **Notifications** for each affected hour (first ten shown,
plus total count), the previous/current Home Assistant cumulative sums,
and the original sensor states. The preview warns about historical decreases and preserves their recorded
values, subject to the migration planner's strict baseline and consistency
checks. Some negative steps remain unsafe and block import. No backups or
writes run during Preview; the original history remains untouched.



**Not production ready. This feature is off by default and requires Home
Assistant 2026.11.0b2 or newer.** This beta is published as a GitHub
pre-release for manual selection under Marketplace > Other versions. It is
not offered as a normal automatic update.

The experiment is scoped to this exact existing Powerpal source statistic:

- Original, read-only: `sensor.powerpal_proxy_powerpal_total_energy`
- New target: `palpulse:powerpal_d8a5d6d91964_energy` (computed from the
  meter configured in the integration)

**The source entity and its long-term statistics are never deleted,
renamed, edited, or reset.** PalPulse never changes Energy dashboard settings.

1. Confirm that the original Powerpal sensor has historical hourly readings,
   and PalPulse has at least its first imported hour. Keep the ESP32 running.
2. Go to **Settings > Devices & services > PalPulse for Home Assistant >
   Entities**. Find and enable these experimental button entities, disabled
   by default:
   **Preview historical migration**, **Import old Powerpal history**,
   **Undo historical migration**.
3. Press **Preview historical migration**. Read the Home Assistant persistent
   notification. It reports the cutoff, number of old hours, gaps and unusually
   large source jumps. Preview has no write side effects.
4. Within ten minutes, press **Import old Powerpal history**. It rechecks the
   plan and cutoff and refuses to run if anything changed.
5. Before inserting any rows, the integration calls the Home Assistant Backup
   manager, explicitly includes the Recorder database, **waits for the backup
   job to complete**, and verifies that the backup is available from a
   destination. If backup creation or verification fails, no import occurs.
6. It writes only legacy hours strictly **before** the earliest existing
   PalPulse row. It never replaces the target's original zero baseline,
   any later measurement, or the original sensor's statistics. The migration
   saves a persistent journal with the exact new rows and backup ID.
7. To reverse, press **Undo historical migration**. The integration checks
   that the imported rows haven't been edited, creates and verifies another
   Recorder-inclusive backup, then clears **only the journalled target time
   range**. Existing PalPulse rows at and after the cutoff and the entire
   original Powerpal history are preserved.

**Limitations:** Historical spikes already in the original data remain
present in the copied history. Missing source hours are not fabricated. The
first PalPulse synthetic baseline row is kept as-is, so the transition may
retain a zero-usage baseline interval. The migration shifts historical
`sum` values by a constant, which can make older sums negative. Energy
graphs should use differences between consecutive sums, but this behavior
needs live Home Assistant validation. Review the preview before committing.

**Failure recovery:** If Home Assistant stops during an import, the journal
stays on disk and the Undo button can remove the subset that was inserted.
Undo refuses to clear a range containing unfamiliar or altered statistics.
The backup identifiers are displayed in the buttons' attributes. Never
delete the source sensor or manually clear its statistics.

### Live energy recovery and health (0.1.0b8)

The independent **PalPulse live history status** diagnostic entity is visible
under **Settings > Devices & services > PalPulse for Home Assistant > Entities**.
It is not the legacy migration progress indicator. Inspect it for:

- `last_event_received`, `last_completed_utc_hour`, `blocked_utc_hour`
- `skipped_hours`, `last_gap_utc_hour`, `pending_minute_records`
- `message` and `last_import_error`

Statuses include **Collecting**, **Active**, **Waiting For Complete Hour**,
**Active With Gaps**, **No Events**, **Recorder Error** and **Halted**.

A missing hour is not converted into a guessed value. After the later hour
is fully verified and the 15-minute late-event grace expires, PalPulse skips
the unverified hour and imports the later hour's consumption only. Cumulative
sums are carried forward from the last verified hour, with no catch-up spike.

If an older version was already stalled, the saved minute buffer and cursors
are loaded automatically after the integration restarts. Later valid hours
are processed on the next incoming ESPHome event. If record delivery has
stopped, investigate `esphome.palpulse_record` under Developer Tools > Events.

**Important:** Do not rerun the legacy migration, reset the energy sensor,
or edit Home Assistant Recorder to resolve a live gap. Historical gaps remain
unfilled. This beta has automated recovery tests, but verify its behaviour
against your own meter before treating it as production ready.

### Backup verification fix (0.1.0b7)

On Home Assistant OS/Supervised, the value Home Assistant returns from
`async_create_backup` is a **backup job ID**, which is not always the
**saved backup ID**. Older experimental builds used the former to look up the
latter and could fail after a backup had successfully completed.

This beta creates a uniquely named Recorder-inclusive backup, waits until
Home Assistant's backup job finishes, lists saved backups, finds its actual
backup ID, and verifies both Home Assistant and Recorder are included at the
selected backup destination. Any lookup or verification failure stops
before writing a single historical statistic.

If the import status says **Failed**, open the migration status entity's
attributes and inspect `last_error` and `last_message`. The generic
Home Assistant activity dialog stating "No cause was recorded" is a
logbook detail and does not reflect the PalPulse diagnostic attributes.

### Visual migration status, progress and interruption recovery (0.1.0b6+)


PalPulse now exposes two **enabled-by-default** diagnostic entities under
**Settings > Devices & services > PalPulse for Home Assistant > Entities**:

- **PalPulse migration status**, with stages such as Planning, Ready, Backing up,
  Reconciling, Importing, Verifying, Completed, Interrupted, Blocked and Failed.
- **PalPulse migration progress**, a numeric percentage from 0 to 100 when the
  number of planned hours is known. Its attributes include
  `completed_rows`, `total_rows`, `progress_percent`, `resume_available`,
  `last_message`, `last_error` and the verified backup identifier.

You can place **PalPulse migration progress** on a Home Assistant **Gauge**
dashboard card for an optional graphical progress bar. The status entity makes
the current action visible without opening logs or guessing whether the backup
is running. The existing persistent migration notification is updated after
approximately each 10% of verified hourly records.

**Resuming an interrupted import:**

1. If HA restarts or the old job fails after preparing its journal, the status
   entity shows **Interrupted** (or **Failed**) and marks `resume_available`
   true. The journal's last saved count is only an estimate: it may lag writes
   already committed to Recorder.
2. **Do not press Preview, Undo, or Import repeatedly.** Allow the previous
   action to stop, then press **Resume interrupted historical migration**,
   which is disabled by default and must be enabled in the Entities view.
   Alternatively, the existing **Import old Powerpal history** button now
   detects an incomplete journal and performs the same safe resume path.
3. PalPulse first waits for pending Recorder work, verifies the original
   Recorder-inclusive backup, validates the original source statistic and
   unmodified PalPulse cutover baseline, and reads the actual pre-cutover
   target rows from Recorder.
4. Every existing target row must match the saved import plan. Unknown or
   altered rows, a missing backup, a changed source, or conflicting short-term
   statistics **block** the resume. The integration does **not** overwrite them.
5. Only missing, pre-cutover rows are submitted, in batches of up to 240,
   then read back and verified. The confirmed count and phase are saved to
   the durable journal after each successful batch. A crash after a Recorder
   commit but before the journal update is handled by reconciling Recorder
   again rather than importing the same hour twice.
6. After all rows are verified, the stage becomes **Completed**. The original
   source and all existing PalPulse hours at/after the cutover are left intact.

**Safety:** The companion never resumes automatically during Home Assistant
startup, and it never silently ignores mismatched data. Resume is an explicit,
human-triggered operation. The existing Undo button still targets only the
journalled hours and makes a separate Recorder-inclusive backup.

If an **older** migration is currently running, **do not restart Home Assistant
just to install the progress update**. Existing code cannot retrospectively
report progress for an already-running job. Let it finish, or establish its
journal and Recorder state before changing versions.

### Marketplace update procedure (0.1.0b3 and later)

Starting with `0.1.0b3`, PalPulse uses a **regular GitHub release** so
Marketplace's normal update detection uses the version tag rather than the
latest commit hash. The version and release title still clearly identify
**experimental beta** software. The earlier `0.1.0b2` GitHub release is a
pre-release, which is why Marketplace displayed a commit hash instead.

1. Keep the PalPulse meter configured and leave the ESP32 firmware unchanged.
2. Open **Settings > Marketplace > PalPulse for Home Assistant**.
3. Refresh its information if that control is available. Custom GitHub
   repositories can take time to update their version metadata.
4. Confirm **Available** shows `0.1.0b3`, not a seven-character commit hash.
   Inspect its release notes, then click **Update**.
5. Confirm **Installed** shows `0.1.0b3`, and the update is no longer offered.
6. Follow Home Assistant's update/Repairs notice and restart Home Assistant if
   prompted. The updated release is metadata-only, but restarting is a
   useful validation of the end-to-end Marketplace update procedure.
7. Verify `esphome.palpulse_record` events still arrive and that no
   PalPulse integration errors appear in Core logs. Do not change the
   existing Energy dashboard source while validating historical statistics.

**Version policy:** Because automatic update discovery excludes GitHub
pre-releases by default for custom repositories, PalPulse test releases are
published in the normal GitHub release channel with `bN` beta version
numbers and prominent beta warnings. This deliberately makes experimental
updates discoverable to everyone who installs this custom repository.
Use a pre-release only when intentionally requiring a manual version choice.

**Current limitation:** Records rejected by the previous thread-safety bug
were not added to the importer ledger. This version cannot restore them
without a separate, reviewed historical replay path.

### Does Home Assistant need restarting?

- **First installation on Home Assistant 2026.11+:** Normally **no**. Marketplace
  hot-loads a newly installed integration with a config flow such as PalPulse.
  If you can see its **Add service** button in Devices & services, no restart
  is required before adding the Powerpal meter.
- **Update or removal of an already loaded integration:** **Yes**. Marketplace
  creates a Home Assistant **Repairs** notification with a restart action.
  This prompt is owned by Marketplace, not by the PalPulse integration.
- **Home Assistant 2026.10 and older:** Not supported by releases using
  the new `2026.11.0b2` minimum; older release documentation is historical.
- **If PalPulse is missing from Add integration after installation:** Check
  **Settings > System > Repairs** and the Home Assistant logs. A manual
  restart is a reasonable troubleshooting step.

The integration cannot force an install-time restart prompt because its code
does not run before Marketplace has loaded it. PalPulse deliberately does
**not** require or trigger an unnecessary restart on a first installation.
See the official [Marketplace restart behaviour](
https://contributors.home-assistant.io/marketplace/guides/integrations/#restarts)
for the exact rules.

### Where do the energy readings appear?

The Marketplace creates an `update.palpulse_historical_energy` entity and may
show integration information after installation. **Neither is a consumption
sensor.** PalPulse intentionally creates **external hourly statistics** instead
of a regular sensor entity. You will not see a separate historical-energy
sensor under Developer Tools > States.

First select **Add service** in **Settings > Devices & services**, enter the
Powerpal meter BLE MAC, and verify `esphome.palpulse_record` events appear under
**Developer Tools > Events**. A complete hour of valid minute records must
arrive before any hourly statistic can be imported. Inspect the resulting
`palpulse:powerpal_<mac>_energy` under **Developer Tools > Statistics**.
Keep the existing Energy dashboard source unchanged until these statistics
have been independently checked.

The new statistic ID is
`palpulse:powerpal_<12 lowercase hex digits>_energy`.
You can find it via **Developer Tools > Statistics**, and it can be selected
as a grid consumption source in the Energy dashboard **after validation**.

## Required firmware YAML additions

Use PalPulse firmware containing the new opt-in history-event sender.
The existing sensors and entity names remain unchanged. Add:

```yaml
api:
  encryption:
    key: !secret esphome_api_encryption_key
  reboot_timeout: 0s
  homeassistant_services: true

sensor:
  - platform: powerpal_ble
    ble_client_id: powerpal
    pairing_code: !secret powerpal_pairing_code
    notification_interval: 5
    pulses_per_kwh: !secret powerpal_pulses_per_kwh
    time_id: homeassistant_time
    history_events: true  # New opt-in; keep other sensor children unchanged
```

Don't create a second `api:` or `sensor:` block. Add these options to your
existing blocks. See the companion PalPulse ESPHome PR for the revised code.

## How timestamp preservation works

1. ESPHome extracts the original Powerpal timestamp, pulses, and cumulative
   pulse count for every accepted native-minute measurement.
2. Over the encrypted native API, ESPHome sends an
   `esphome.palpulse_record` event containing:
   `meter`, `timestamp` (Unix UTC seconds), `pulses`,
   `total_pulses`, `pulses_per_kwh`.
3. This integration verifies the configured MAC, calibration, timestamp range,
   monotonic cumulative counts and 60 consecutive native-minute records
   **before** importing any hourly statistics.
4. It stores a per-meter ledger under Home Assistant `.storage`, and uses
   `async_add_external_statistics` to import aligned UTC hour starts. Home
   Assistant Recorder can upsert the same hour idempotently.
5. The first accepted complete hour establishes a zero-sum baseline, avoiding
   the existing meter's accumulated total appearing as new consumption.
6. A missing minute or hour is left unknown. PalPulse waits for late
   delivery, then advances to a later verified 60-record hour after a 15-minute
   grace period. Only the later hour's measured consumption is added to the
   cumulative statistic, never the unrecorded interval.

The resulting external statistic is **not** the existing
`sensor.powerpal_total_energy`. The original live sensor remains unchanged.
Do not configure both as separate grid-consumption sources.

### Known limitations

- Powerpal provides 60-second records. This integration produces **hourly**
  historical statistics via Home Assistant's supported API, not 5-minute
  recorder state backfills.
- The ESPHome-to-HA event transport is not acknowledged or replayable by
  this beta. If a native-minute event is permanently lost, that hour is
  reported as missing. A subsequent verified complete hour can resume the
  series without counting missing consumption.
- If the Powerpal counter resets, calibration changes or conflicting records
  arrive, imports halt for manual investigation rather than guessing a value.
- Only the last seven days are accepted. Older imports need a separate
  deliberate migration workflow.
- A meter calibration change or conflicting record stops imports until a
  reviewed recovery procedure is performed.
- The historical statistic starts when the first complete 60-minute period
  is received after enabling both components. It does **not** retroactively
  repair incorrect statistics produced by an earlier firmware installation.
- UTC timestamps are required; ESPHome does not set the date of normal HA
  sensor states.
- Events are delivered only if the device is allowed to perform Home
  Assistant actions. Enabling this grants the ESPHome proxy broader HA action
  permissions, so use it only on a trusted ESPHome node.
- Changes are never made directly to the Home Assistant database.

## Testing

```sh
python -m unittest discover -s tests -v
```

This project remains beta until confirmed by tests for power loss, HA restart,
midnight/DST, reconnection and missing event recovery.

## Licence and prior work

PalPulse for Home Assistant is independently authored MIT-licensed code
using Home Assistant's documented Recorder statistics APIs. It contains no
third-party Powerpal BLE transport implementation. The ESPHome companion has
its own [provenance record](https://github.com/iamhasantan/palpulse-esphome/blob/feat/independent-powerpal-ble-foundation/docs/PROVENANCE.md)
for earlier original engineering work, protocol research and retained on-device
checkpoint compatibility.


## Experimental electricity cost and Red Energy tariffs

The verified energy statistic remains unchanged: palpulse:powerpal_<meter_mac>_energy.
With optional custom pricing, a separate monetary external statistic appears:
palpulse:powerpal_<meter_mac>_cost. Select this statistic using Home Assistant's
Energy Dashboard option **Use an entity tracking total costs**. Home Assistant
cannot attach current-price or static-price entities directly to externally
recorded PalPulse energy statistics.

In **Settings > Devices & services > PalPulse for Home Assistant > Configure**:

- **Home Assistant manages costs** (default): no PalPulse cost statistics;
  your existing native HA setup is untouched.
- **Custom flat tariff**: usage rate plus optional daily supply fee.
- **Custom time of use**: weekday/weekend peak, shoulder, off-peak, optional free
  windows, local-day free allowance and paid excess rate. Time boundaries are
  local to your Home Assistant timezone, including Australian daylight saving.

For the older **Red Energy Red Power5** plan used in the existing local
dashboard, set **11:00-16:00** as both weekday and weekend free windows,
**15 kWh/day** as the free cap, and enter the actual paid rates from your bill.
The prior dashboard used a smart-paid rate of **16.412 c/kWh**. Confirm it
is still the correct rate. The old A$1.25/day supply fee was only an
unverified dashboard assumption, not a tariff confirmed from a bill.

The newer Victorian **Midday Power Saver** standing offer is different:
**11:00-14:00** with up to **24 kWh/day** free primary-load energy. Do not
silently apply that schedule to the older Red Power5 plan.

### Importing previous historical electricity costs

If the previous implementation had a genuine Recorder-backed cumulative
cost statistic, add its **exact statistic ID** in the optional **Previous
cumulative electricity cost statistic** field. A daily display sensor such
as sensor.electricity_cost_today is eligible only when it has actual
cumulative hourly sums. The price-per-kWh entity
sensor.electricity_usage_price is NOT a historical cost source.

After PalPulse has written its first cost baseline, separately enable the
disabled-by-default **Preview historical cost migration**, **Import old
electricity costs**, and **Undo historical cost migration** controls.

The cost migration is separate from the previously completed kWh migration.
Its read-only preview lists the old cost hours, gaps, anomalies, and cutover.
It copies the **recorded old sums**, adjusted by a single offset to meet the
new cost baseline, without repricing history using today's tariffs. New
PalPulse hours, original usage history, and the original cost source remain
unchanged. Both Import and Undo require fresh, verified Recorder-inclusive
Home Assistant backups. Recorder writes are journalled and verified; Undo
refuses altered or unjournalled rows.

Costs use native one-minute meter timestamps so free and peak transitions
are assigned accurately. A daily free allowance is shared across local
hours, including DST days. If free allowance use cannot be established after
a gap, the cost writer fails closed instead of guessing.

**Supported**: flat rates, time-of-use rates, weekday/weekend schedules,
free windows, capped free electricity, paid overflow and optional daily
supply fees. **Not yet supported**: seasonal/public-holiday exceptions,
controlled-load circuits, solar feed-in, tiered billing blocks and
demand charges. These require additional meter readings or tariff rules.
Avoid claiming the resulting estimate matches a bill until the plan's
complete billing terms have been verified. This feature is **experimental,
not billing-grade**.
