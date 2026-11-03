# PalPulse for Home Assistant

HACS-installable experimental Home Assistant companion for
[PalPulse ESPHome](https://github.com/iamhasantan/palpulse-esphome).
It receives original timestamped Powerpal BLE records and imports **complete UTC
hours** into Home Assistant Recorder as a **separate, external energy statistic**.

**Early beta, not billing-grade.** Do not replace your working whole-home energy
source or select both sources in Home Assistant's Energy dashboard. This importer
requires an opt-in firmware event sender and real-world validation before use.

## Install through Marketplace / HACS

The repository uses the standard `custom_components/palpulse` layout and
`hacs.json` packaging. **Home Assistant 2026.11 and newer include HACS as
the built-in Marketplace**. The older standalone HACS integration is replaced.

1. On **HA 2026.11+**, open **Settings > Marketplace > Custom repositories**
   and add `https://github.com/iamhasantan/palpulse-homeassistant` as an
   **Integration**. On **HA 2026.10 or older**, add the same URL under
   **HACS > Custom repositories**.
2. After this beta has been merged to the repo's default branch and passes
   marketplace validation, install **PalPulse Historical Energy**.
3. Open **Settings > Devices & services > Add integration > PalPulse Historical
   Energy**. Enter the **Powerpal BLE meter MAC**, not the ESP32 Wi-Fi MAC.
4. Update your PalPulse ESPHome YAML as described below and rebuild/test it.
5. In the ESPHome integration options for your proxy, enable **Allow the device
   to perform Home Assistant actions**. This is essential for event delivery.
6. Listen to `esphome.palpulse_record` under Developer Tools > Events before
   selecting the new historical statistics source.

**Development status:** the HACS/Marketplace custom repository installs from
the **default branch**, not this draft PR. Until the PR is merged and repo
metadata checks pass, use a manual copy of the draft `custom_components/palpulse`
folder only for isolated testing; do not assume Marketplace installation works.

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
  is received after enabling both components. It does **not** magically
  repair FreshPowerpal's existing incorrect Home Assistant statistics.
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

The companion implementation is new MIT-licensed code using Home Assistant's
documented Recorder APIs. This repo does **not** import WeekendWarrior1, Pento,
Gurrier or historical FreshPowerpal BLE transport code. It complements the
original FreshPowerpal measurement and checkpoint improvements already
acknowledged by [PalPulse ESPHome](https://github.com/iamhasantan/palpulse-esphome).
