# Full-day electricity cost recalculation

PalPulse can preview and recalculate **one completed local billing day**, using the tariff version effective on that date and the original Home Assistant Recorder energy statistics.

This tool is intended for days with incorrect or missing monetary statistics where earlier individual-hour repair could not safely establish whether the daily supply charge had already been billed.

## Where to find it

Open **PalPulse → Hourly history → Recalculate an entire billing day**. Select the historical local date and click **Preview whole day**. Nothing is modified during preview. The table shows the real verified kWh, original independently verified hourly charge (or **Unverified**) and the newly calculated charge.

The recalc includes peak/off-peak rates, the capped free window and **one daily supply charge**. It uses the actual date-effective tariff. No zero consumption is invented for missing energy, and no unavailable hour is assigned a price.

## Safety requirements

The preview refuses to proceed unless all 23, 24 or 25 local billing hours have adjacent, agreeing Recorder energy state and sum counters. Other necessary conditions include:

- A historical tariff exists for every hour and the day is complete.
- The preceding hour has a validated original monetary sum and state, with its baseline preserved.
- The final hour of the selected day has an original finite cost state and sum, to establish exactly how later cumulative totals must shift.
- Existing monetary records are not silently interpreted as zero when they are missing or unverified.
- No earlier queued cost journal or unresolved conflicting repair is allowed.
- The original day-end cost record and the live monetary checkpoint have not changed before commit.

When a historical day is approved for correction, PalPulse creates and verifies a Home Assistant backup containing Recorder data, freezes the signed day plan, and, if there are newer existing cost rows, creates and verifies a **second backup** before including their cumulative totals in the plan.

Later recorded hourly charges are **not recalculated**. Their cumulative sum and state are shifted by the proven end-of-day difference, preserving their original increments, while missing cost rows remain missing.

The existing cost-reconciliation worker checks all original rows against their saved snapshot before the first write, writes and verifies small batches, saves progress for interrupted operation, and requires protected resumption. Closing the browser does not abort an accepted Home Assistant job.

## Known limitations

- A day with missing energy or a missing previous/final monetary anchor cannot be fully recalculated safely.
- A source tariff that changed without a recorded effective date cannot be reconstructed.
- The tool only edits PalPulse's own monetary statistic. It does not edit the energy statistic, raw Powerpal measurements or ESP32 NVS state.
- A correction changes historical cost totals and therefore Home Assistant's reported electricity cost. Review the preview and verify it against your actual retailer tariff and bill.
- No automated rewrite is attempted merely because an audit reports missing monetary hours. Day recalculation requires explicit administrator confirmation and a successful backup.

If a previous historical correction is incomplete, finish or resume that saved journal before approving another day.
