# Changelog

## Unreleased

## 0.1.0b3

- Fix Marketplace's commit-hash update display by publishing a numbered GitHub
  release that the normal update channel can discover.
- Preserve the beta designation in the version and release title. This remains
  experimental software and is not billing-grade, despite the GitHub release
  being classified as a regular release for Marketplace compatibility.
- Raise the minimum Home Assistant requirement to 2026.11.0b2 in HACS metadata.
- Document how to validate Marketplace release discovery and installation.
- No changes to the ESP32 firmware, Powerpal entity names, event payloads, or
  historical energy accounting.

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
