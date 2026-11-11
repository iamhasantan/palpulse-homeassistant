# PalPulse for Home Assistant

Local electricity monitoring for **Powerpal** meters using an ESP32 and Home Assistant. PalPulse brings live readings, timestamped energy history and optional electricity-cost tracking into Home Assistant without a cloud account.

> **Early preview: 0.2b1.** PalPulse is still experimental. Expect rough edges, especially around missed measurements, ESP32 replacement and historical imports. **Back up Home Assistant (including Recorder/history) before updating**, and preserve your ESP32 firmware and NVS data before reflashing. Cost estimates are not a substitute for your retailer's bill.

## What it does

- Reads Powerpal measurements from a compatible ESPHome BLE component.
- Records verified hourly energy data in Home Assistant's long-term statistics.
- Offers optional flat and time-of-use electricity prices, including free-power periods.
- Can preview a migration from existing compatible energy and cost histories, with backup-first Import and Undo.
- Shows recording status and missing-hour diagnostics so you can see when something needs attention.

PalPulse keeps its historical energy statistic separate from your existing live ESPHome sensors. **Do not add both as grid-consumption sources** in the Energy dashboard.

## Before you install

You'll need Home Assistant **2026.11 or newer**, an ESP32, a supported Powerpal meter, and the companion [PalPulse ESPHome component](https://github.com/iamhasantan/palpulse-esphome). The ESP32 needs to be paired with your meter and connected to Home Assistant.

1. Open **Settings → Marketplace → Add from link** and enter `https://github.com/iamhasantan/palpulse-homeassistant`.
2. Install **PalPulse for Home Assistant**. If you're upgrading, choose **0.2b1** under Updates, then restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → PalPulse for Home Assistant** and enter your Powerpal BLE MAC address.
4. Enable history events in the companion ESPHome component. On the ESPHome device in Home Assistant, allow the device to perform Home Assistant actions.
5. Check **PalPulse live history status** before switching your Energy dashboard to the new statistic.

The optional **acknowledged history transport** is a preview feature. It requires matching ESPHome firmware and an additional native-API action setting. Use the [setup and recovery guide](docs/setup-and-recovery.md) before enabling it.

## Moving from another integration

You can select an existing energy statistic under **PalPulse → Configure → Choose previous energy source**, then use the read-only Preview. The source must belong to the **same physical Powerpal meter** and have compatible cumulative readings. PalPulse will reject mismatched history rather than change your existing data.

If you're replacing the ESP32 but keeping the same Powerpal meter, follow the separate hardware-replacement procedure. **Do not factory-reset an existing ESP32 or erase NVS just to install PalPulse.**

## Support and documentation

- [Installation, firmware and recovery](docs/setup-and-recovery.md)
- [Technical details and older migration notes](docs/advanced.md)
- [Release notes](CHANGELOG.md)
- [Report an issue or share test results](https://github.com/iamhasantan/palpulse-homeassistant/issues)

This is a community project, not an official Powerpal or Home Assistant integration. Licensed under [MIT](LICENSE). Thanks to the Powerpal and Home Assistant community members whose public protocol research helped inform the project.
