"""PalPulse Home Assistant constants."""

DOMAIN = "palpulse"
EVENT_TYPE = "esphome.palpulse_record"
CONF_METER_MAC = "meter_mac"
STORAGE_VERSION = 1
STAT_NAME = "PalPulse timestamped total energy"
CONF_ACK_SERVICE = "history_ack_service"
CONF_LEGACY_ENERGY_SOURCE = "legacy_energy_statistic_id"
LEGACY_STATISTIC_ID = "sensor.powerpal_proxy_powerpal_total_energy"  # Upgrade default only
