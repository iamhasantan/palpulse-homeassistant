"""Safe branding migration for PalPulse config entries.

This only changes names that an older PalPulse release generated itself.
Names chosen by Home Assistant users are deliberately not modified.
The Marketplace repository/device is owned by Home Assistant, not PalPulse.
"""

INTEGRATION_NAME = "PalPulse for Home Assistant"

LEGACY_DEFAULT_TITLES = frozenset({
    "PalPulse Historical Energy",
    "PalPulse historical energy",
})


def is_legacy_default_entry_title(title: str | None, meter: str) -> bool:
    """Check only known historical integration default titles, not custom ones."""
    return (
        isinstance(title, str)
        and (
            title in LEGACY_DEFAULT_TITLES
            or title == f"PalPulse {meter.upper()}"
        )
    )
