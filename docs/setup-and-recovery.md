# Installation, updates and recovery

PalPulse is in early preview. Take a full Home Assistant backup **including Recorder/history** before upgrading or using Import, Undo or device-replacement tools. Back up your ESPHome configuration and preserve the ESP32 NVS data before firmware updates.

## Installing or updating

1. In Home Assistant 2026.11+, open **Settings > Marketplace > Add from link**. Add `https://github.com/iamhasantan/palpulse-homeassistant` if you have not already installed it.
2. Choose **0.2b1** in **Settings > Updates**. For an existing installation, keep the same integration and update it in place. Restart Home Assistant afterwards.
3. Open **Settings > Devices & services > PalPulse for Home Assistant**. Add a service using your physical Powerpal BLE MAC address.
4. Check **PalPulse live history status** for current events and completed hourly data before selecting the new statistics under your Energy dashboard.

Marketplace installs require a matching published GitHub release and manifest version. It can take several hours for an update to appear. The 0.2b1 release is published as a regular GitHub release so existing 0.1.0b15 installations can discover it without enabling the Marketplace pre-release switch. The code itself remains **experimental**.

## ESPHome firmware

Use the [matching ESPHome component](https://github.com/iamhasantan/palpulse-esphome) with `history_events: true` and `api.homeassistant_services: true`. The ESPHome device must have **Allow the device to perform Home Assistant actions** enabled. Never run two active BLE bridges against the same meter.

### Optional acknowledged history, 0.2b1 preview

For reliability testing, enable `reliable_history: true` in your Powerpal BLE sensor YAML and `custom_services: true` under your existing `api:` block. Flash the matching 0.2b1 ESPHome firmware.

Home Assistant registers the device's native action, typically named `esphome.<node_name>_palpulse_ack`. Check its exact name under Developer Tools > Actions. Under **PalPulse > Configure > History delivery and acknowledgments**, enter that full action name. Do not enable acknowledged mode on an older firmware build that lacks the action.

The ESP32 keeps a separate durable acknowledgment cursor. Your old cumulative energy checkpoint is not erased. Acknowledged replay can only retrieve records still in the Powerpal buffer. If the buffer has rolled past the last acknowledged minute, the firmware stops replay rather than making up readings.

### Checking it works

On the PalPulse device's Entities tab, open **PalPulse live history status**. Confirm the last completed hour moves forward. For acknowledged mode, check `last_acknowledged_utc_minute` and `ack_error`. If the action is missing, verify firmware compilation, native API options and the configured service name. Check the Recorder's hourly statistic `palpulse:powerpal_<mac>_energy` in Developer Tools > Statistics.

## Migrating older energy data

Open **PalPulse > Configure > Choose previous energy source** and select the cumulative energy statistic for the **same physical Powerpal meter**. Run **Preview usage history**. It checks the existing hour at cutover and whether the old and new meter states are compatible.

Preview is read-only. Import requires confirmation and a verified Recorder-inclusive backup; Undo also makes a backup. Existing source records are never changed. Incompatible state baselines and missing source statistics are refused. Don't point the migration at an unrelated meter or an arbitrary energy sensor.

## Replacing an ESP32

Keep the meter, pulse calibration and MAC the same. Prefer restoring the compatible NVS checkpoint when possible.

If the old checkpoint cannot be recovered, back up Home Assistant, then use **PalPulse > Configure > Replace an ESP32**. This explicitly allows **one** verified hour within 24 hours to establish the new ESP32's lower raw meter counter. PalPulse continues the saved cumulative Recorder sum. It does not invent electricity consumed during any missing changeover period.

Use this only for an ESP32 replacement, **not** a new physical meter. Confirm the new firmware and event timestamps before arming. It is experimental and should be tested on a non-production system first.

## Costs, gaps and support

Electricity cost calculation uses the tariff configuration active when an hour was queued, where the versioned tariff snapshot is available. A gap in native-minute measurements may leave an unpriced hour, even when the cumulative Powerpal meter proves the kWh. We won't guess time-of-use or capped-free electricity charges. Pending fully observed cost hours are kept for retry after Recorder errors.

Historic missing energy or cost hours are **not automatically repaired** by an upgrade. Check the underlying Recorder data and make a separate backup before attempting any correction.

[Open an issue](https://github.com/iamhasantan/palpulse-homeassistant/issues) with your integration version, Home Assistant version, ESPHome firmware commit, anonymised status attributes and relevant logs. Never share pairing codes, cloud credentials or backup files publicly.
