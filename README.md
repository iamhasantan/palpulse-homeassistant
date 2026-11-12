# PalPulse for Home Assistant

Local electricity monitoring for **Powerpal** meters using an ESP32 and Home Assistant. PalPulse brings live readings, timestamped energy history and optional electricity-cost tracking into Home Assistant without a cloud account.

> **Early preview: 0.2b8.** PalPulse is still experimental. Expect rough edges, especially around missed measurements, ESP32 replacement and historical imports. **Back up Home Assistant (including Recorder/history) before updating**, and preserve your ESP32 firmware and NVS data before reflashing. Cost estimates are not a substitute for your retailer's bill.

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
2. Install **PalPulse for Home Assistant**. If you're upgrading, choose **0.2b8** under Updates, then restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → PalPulse for Home Assistant** and enter your Powerpal BLE MAC address.
4. Enable history events in the companion ESPHome component. On the ESPHome device in Home Assistant, allow the device to perform Home Assistant actions.
5. Check **PalPulse live history status** before switching your Energy dashboard to the new statistic.

The optional **acknowledged history transport** is a preview feature. It requires matching ESPHome firmware and an additional native-API action setting. Use the [setup and recovery guide](docs/setup-and-recovery.md) before enabling it.

## PalPulse sidebar dashboard

The new **PalPulse** item in Home Assistant's sidebar opens a dedicated control
centre. It brings together current Powerpal health, your electricity plan,
cost-writer errors, recent hourly Recorder statistics, migration progress,
backups and ESP32 recovery in one place.

- **Overview** shows energy and cost status. A missing cost record appears as
  missing, not as free electricity.
- **Electricity** edits every tariff rate, daily free allowance, weekday/weekend time window and supply fee directly in the sidebar. It also shows the cost checkpoint and offers a confirmed link of verified PalPulse cost data to the Energy dashboard.
- **Hourly history** displays real Recorder hours and lets you export a CSV.
- **ESP32 recovery** compares the firmware's saved meter and ACK checkpoints
  with Home Assistant's last verified hour. If supported and safe, you can ask
  ESPHome to retry buffered history from its existing acknowledged cursor.
- **Settings** edits the ESPHome ACK action, automatic verified-cost linking and original energy/cost source identifiers. A started migration locks its source.
- **Migration** takes you to the existing backup-first Preview, Import, Resume
  and Undo screens. It doesn't bypass their confirmations.
- **Backups** can request an automatic Home Assistant backup. **Confirm that it
  finished and includes Recorder/history before changing anything.**

Rate editing uses strict server-side validation. A capped free window must have a positive retailer-confirmed excess rate for future configuration, and saved tariff changes start a new profile from a safe hour boundary. Missing native-minute events are never invented; exact hourly kWh may be restored from independently verified cumulative counters. The panel is visible to Home Assistant administrators. It handles multiple
Powerpal meters separately and doesn't require a manually imported Lovelace
dashboard.

**Checkpoint replay needs PalPulse ESPHome 0.2b2 or newer**, reliable history
enabled, and the matching ACK action configured in the Home Assistant
integration. Earlier ESPHome builds can still record electricity as before,
but won't provide checkpoint telemetry or the manual replay action. Replaying
only works while the original minute records remain in the Powerpal's buffer.
It cannot fill hours that are no longer available or derive missing
time-of-use charges.

## Automatic best-effort recovery (next experimental preview)

PalPulse retries saved energy hours after a restart and every five minutes, even
if no new ESPHome events arrive. When original native-minute events are missing
but the first and last cumulative meter readings survive, it can recover the
**exact hourly kWh** with up to 58 missing interior minutes, or up to 59 when the previous hour's durable final reading also anchors the interval. A verified hour
with one constant billing band can also recover the exact tariff cost.

Missing cost entries can also be rebuilt from two adjacent Home Assistant
Recorder energy hours when their raw meter-state difference agrees with the
cumulative energy difference. This uses the original timestamp and tariff
profile, not today's meter usage. Uncommitted legacy jobs with an accidentally
unset Power5 excess rate can adopt the now-confirmed rate only if the remaining
plan settings match, including the corrected 4-9pm peak windows. Already
committed historical cost statistics are never silently rewritten.

With a compatible ESPHome build and a configured native ACK action, a fresh,
safe firmware checkpoint can automatically request buffered meter-history
replay. The normal native ACK sequence protects durable Home Assistant storage
from lost acknowledgments. This **cannot** restore minutes discarded from the
Powerpal's own buffer, backfill skipped hours whose historical Recorder series
has since advanced, or prove per-minute allocation when a sparse hour crosses
tariff bands. Those hours remain explicitly missing/unpriced, rather than zero.

**Red Power5:** Verify your actual invoice/fact sheet. Configure 11:00-16:00
free (first 15 kWh), the retailer-confirmed excess rate, 16:00-21:00
evening peak on weekdays and weekends, and off-peak otherwise. Having a
numeric peak rate without peak windows does *not* activate peak pricing.
The guided Power5 wizard sets these published windows but does not guess
your rates.

## A simpler settings screen

Open **Settings → Devices & services → PalPulse for Home Assistant → Configure**.
The main screen has five sections:

- **Status** shows live recording, pricing and import health.
- **Set up PalPulse** walks through the initial tariff setup.
- **Electricity prices** contains tariff and free-power settings.
- **History and migration** has optional historical energy/cost imports and the read-only continuity check.
- **Advanced** contains connection checks, acknowledged delivery, ESP32 replacement and dashboard setup.

The device page now focuses on live health. Older duplicate import buttons,
completed-migration progress indicators and the standalone daily supply charge
are hidden by default, not deleted. They still work if you have automations or
dashboards using them. If you customised an entity's name or visibility, PalPulse
leaves it alone. You can show hidden entities again from Home Assistant's entity
settings. Historical statistics, migration journals and tariff settings are
unchanged.

## If usage shows but costs are A$0.00

First check **PalPulse cost import status** under the device's diagnostic entities.
It now distinguishes a cost recording delay, queued retry, missing cost hours
and an Energy dashboard that has no PalPulse cost source selected.

If the cost writer has committed hourly costs but the Energy dashboard
doesn't display them, open **PalPulse → Configure → Electricity prices →
Fix Energy dashboard costs**. Review the current source and confirm the link.
You can optionally let PalPulse restore that exact link automatically if it
later disappears. It will **never** replace a different price or cost source.

PalPulse retries pending cost writes every five minutes and after a Home
Assistant restart. A missing minute or an unknown capped-free allocation
still cannot be priced accurately. Older hours with no preserved native
minute data remain unpriced, not guessed or silently changed.

The cost link controls the dashboard display. It does not change tariffs,
create retroactive charges or modify existing energy history.

## Moving from another integration

You can select an existing energy statistic under **PalPulse → Configure → History and migration → Energy history → Choose previous energy source**, then use the read-only Preview. The source must belong to the **same physical Powerpal meter** and have compatible cumulative readings. PalPulse will reject mismatched history rather than change your existing data.

If you're replacing the ESP32 but keeping the same Powerpal meter, follow the separate hardware-replacement procedure. **Do not factory-reset an existing ESP32 or erase NVS just to install PalPulse.**

## Support and documentation

- [Installation, firmware and recovery](docs/setup-and-recovery.md)
- [Technical details and older migration notes](docs/advanced.md)
- [Release notes](CHANGELOG.md)
- [Report an issue or share test results](https://github.com/iamhasantan/palpulse-homeassistant/issues)

This is a community project, not an official Powerpal or Home Assistant integration. Licensed under [MIT](LICENSE). Thanks to the Powerpal and Home Assistant community members whose public protocol research helped inform the project.
