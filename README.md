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
2. Install **PalPulse Historical Energy** after Marketplace/HACS recognises
   the repository. On Home Assistant 2026.11 and later, a **first installation**
   of a config-flow integration can be loaded immediately. **No restart prompt
   is expected**. If you see **Add service**, continue to step 3.
3. Open **Settings > Devices & services > Add integration > PalPulse Historical
   Energy**. Enter the **Powerpal BLE meter MAC**, not the ESP32 Wi-Fi MAC.
4. Update your PalPulse ESPHome YAML as described below and rebuild/test it.
5. In the ESPHome integration options for your proxy, enable **Allow the device
   to perform Home Assistant actions**. This is essential for event delivery.
6. Listen to `esphome.palpulse_record` under Developer Tools > Events before
   selecting the new historical statistics source.

**Beta status:** the source is available on `main`. This is an
experimental integration: validate timestamped imports before selecting the
external statistic for grid consumption. Never select both the old Powerpal
entity and the new external statistic.

### Update-test procedure for 0.1.0b2

The beta is published as a GitHub **pre-release** with tag `0.1.0b2`,
matching the integration manifest. A tag alone is not a Marketplace release.

1. In **Settings > Marketplace**, open **PalPulse Historical Energy** and
   enable its **Pre-release** option in the repository options. Leave the
   existing configured Powerpal meter untouched.
2. Use the repository's **Update information / Check for updates** action
   if available. GitHub custom-repository refresh and update discovery are
   not necessarily immediate.
3. When `0.1.0b2` is offered in **Settings > Updates**, review the release
   notes and install it through Home Assistant (not by copying files).
4. Follow the restart/Repairs notice and restart Home Assistant. This update
   changes Python code; restarting is required before the new listener runs.
5. Confirm no `palpulse` thread-safety exceptions occur in **Settings >
   System > Logs**, and verify ESPHome `esphome.palpulse_record` events
   arrive. A complete valid UTC hour is needed before the external statistic
   `palpulse:powerpal_<mac>_energy` is created.
6. Do not add the new statistic alongside your existing Powerpal source in
   the Energy dashboard. Preserve the original source until comparisons pass.

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
6. A missing minute or hour blocks further import rather than silently
   attributing a backlog to the wrong time. This is a deliberate trade-off.

The resulting external statistic is **not** the existing
`sensor.powerpal_total_energy`. The original live sensor remains unchanged.
Do not configure both as separate grid-consumption sources.

### Known limitations

- Powerpal provides 60-second records. This integration produces **hourly**
  historical statistics via Home Assistant's supported API, not 5-minute
  recorder state backfills.
- The ESPHome-to-HA event transport is not acknowledged or replayable by this
  beta. If the Home Assistant API is offline, or its event queue loses
  measurements, an hour may remain incomplete and imports stop. This
  **fails closed**, not by inventing or moving kWh into a later hour.
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

PalPulse Historical Energy is independently authored MIT-licensed code
using Home Assistant's documented Recorder statistics APIs. It contains no
third-party Powerpal BLE transport implementation. The ESPHome companion has
its own [provenance record](https://github.com/iamhasantan/palpulse-esphome/blob/feat/independent-powerpal-ble-foundation/docs/PROVENANCE.md)
for earlier original engineering work, protocol research and retained on-device
checkpoint compatibility.
