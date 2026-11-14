# PalPulse for Home Assistant

**Local Powerpal energy monitoring, historical statistics, electricity costs and intelligent recovery.**

PalPulse connects Powerpal electricity meters to Home Assistant through ESPHome, providing accurate, timestamped energy monitoring without relying on a cloud service.

Built for users who want greater control over their electricity data, PalPulse combines live monitoring, historical energy statistics, tariff-based cost calculations and resilient data recovery in a native Home Assistant experience.

## Features

### Energy monitoring

- Live electricity consumption and power monitoring through Bluetooth Low Energy.
- Timestamped hourly energy statistics for Home Assistant's Energy dashboard.
- Cumulative energy tracking with continuity checks and duplicate protection.
- Historical energy consumption, trends and usage analysis.
- Support for multiple Powerpal meters.

### Electricity pricing and cost tracking

- Flat-rate and time-of-use electricity tariffs.
- Configurable peak, off-peak and free-electricity periods.
- Weekday and weekend pricing schedules.
- Daily free-energy allowances, excess rates and supply charges.
- Historical tariff versions with effective dates.
- Electricity cost statistics integrated with the Home Assistant Energy dashboard.

### Reliable history and automatic recovery

- Acknowledged ESPHome history delivery with automatic service discovery.
- Buffered measurement replay following connection interruptions.
- Persistent ESP32 checkpoints and recovery state.
- Automatic retry of interrupted energy and cost processing.
- Detection of missing, delayed and incomplete hourly records.
- Evidence-based recovery using verified meter counters and Recorder statistics.
- Protection against duplicate measurements, incorrect timestamps and unsupported estimates.

### Historical data and migration

- Import compatible energy and cost history from previous integrations.
- Preserve existing consumption history during migration.
- Preview and validate historical data before making changes.
- Backup-first import, recovery and rollback workflows.
- Tools for reviewing and repairing independently verifiable historical gaps.
- Preview and recalculate a complete local billing day's electricity costs from verified energy and the dated tariff, with backup-first cumulative rebase of later recorded charges. See [Full-day cost recalculation](docs/day-cost-recalculation.md).
- Auditable corrections with preserved original records and recovery journals.

### Integrated management dashboard

- Dedicated PalPulse interface in the Home Assistant sidebar.
- Real-time monitoring and integration health.
- Interactive electricity tariff configuration.
- Hourly energy and cost history.
- Missing-data and recovery diagnostics.
- ESP32 connection, checkpoint and replay management.
- Historical migration and backup controls.
- CSV export for further analysis.

### Privacy and local operation

- Local communication between Powerpal, ESPHome and Home Assistant.
- No mandatory cloud account or external data service.
- Energy history stored within Home Assistant.
- Open-source implementation with configurable local infrastructure.

## Designed for data integrity

PalPulse prioritises verifiable measurements and consistent historical statistics.

When data is delayed or interrupted, the integration attempts safe recovery using available meter and Recorder evidence. Measurements that cannot be independently reconstructed remain identified as missing rather than being replaced with fabricated values.

Historical corrections are protected by validation, backups and recovery safeguards.

## Compatibility

- **Home Assistant:** Core 2026.11.1 or newer.
- **Hardware:** Compatible Powerpal electricity meter and ESP32.
- **Firmware:** [PalPulse ESPHome component](https://github.com/iamhasantan/palpulse-esphome), with compatible firmware required for reliable-history acknowledgements and replay.
- **Connectivity:** ESPHome native API and Bluetooth Low Energy.

## Installation

1. Open **Home Assistant → Settings → Marketplace → Add from link**, and enter `https://github.com/iamhasantan/palpulse-homeassistant`.
2. Install **PalPulse for Home Assistant**. For an existing installation, choose the latest available version under Updates.
3. Restart Home Assistant after updating and add **PalPulse for Home Assistant** under **Settings → Devices & services → Add integration**, entering your Powerpal meter's BLE MAC address.
4. Connect your ESP32 using the companion PalPulse ESPHome component and enable native history events. Allow the ESPHome device to perform Home Assistant actions.
5. Confirm that history is recording before selecting PalPulse as your Energy dashboard grid-consumption source.

**Do not add both the original ESPHome cumulative-energy sensor and PalPulse's timestamped energy statistic as grid-consumption sources.** See the [setup and recovery guide](docs/setup-and-recovery.md) for firmware configuration, migration and troubleshooting.

## Project status

PalPulse is an open-source integration under active development.

The current release is experimental and intended for testing and feedback. Electricity cost calculations are estimates and should be verified against the applicable retailer tariff.

**A Home Assistant backup, including Recorder history, is recommended before installation or upgrade.** Preserve your ESP32's NVS data when changing firmware.

## Links

- [PalPulse for Home Assistant](https://github.com/iamhasantan/palpulse-homeassistant)
- [PalPulse ESPHome component](https://github.com/iamhasantan/palpulse-esphome)
- [Installation and recovery guide](docs/setup-and-recovery.md)
- [Technical documentation](docs/advanced.md)
- [Version changelog](CHANGELOG.md)
- [Issues and feedback](https://github.com/iamhasantan/palpulse-homeassistant/issues)

PalPulse is a community project and is not an official Powerpal or Home Assistant integration. Licensed under [MIT](LICENSE). Thanks to the community contributors whose public protocol research helped inform its development.
