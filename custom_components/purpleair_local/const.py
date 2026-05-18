"""Constants for the PurpleAir Local integration."""

from homeassistant.const import Platform

DOMAIN = "purpleair_local"

# Platforms this integration provides. sensor and binary_sensor cover
# the full v0.1 entity set; both share the same per-entry coordinator
# instance stored in hass.data[DOMAIN][entry.entry_id].
PLATFORMS: tuple[Platform, ...] = (Platform.SENSOR, Platform.BINARY_SENSOR)

# Sensor docs recommend no faster than once every 10 seconds; we default
# to the natural /json averaging window (2 minutes) so consecutive polls
# usually see fresh data.
DEFAULT_SCAN_INTERVAL_S = 120
MIN_SCAN_INTERVAL_S = 15
MAX_SCAN_INTERVAL_S = 3600  # 1 hour — beyond this the integration isn't really doing anything

# Default total timeout for a single HTTP call to the sensor. Healthy
# sensors respond in well under 500 ms on a LAN; 10 s gives generous
# headroom for a slow Wi-Fi cycle without making the coordinator hang.
DEFAULT_REQUEST_TIMEOUT_S = 10.0

# --- options flow keys ----------------------------------------------------

CONF_SCAN_INTERVAL_S = "scan_interval_s"
CONF_AQI_CORRECTIONS = "aqi_corrections"
CONF_AQI_COLOR_SCHEME = "aqi_color_scheme"
CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3 = "channel_disagreement_min_diff_ugm3"
CONF_CHANNEL_DISAGREEMENT_MIN_PCT = "channel_disagreement_min_pct"

DEFAULT_AQI_COLOR_SCHEME = "us_epa"

# AQI correction identifiers — used as values in the multi-select and
# (later) as suffixes on entity unique_ids, so they need to be stable.
AQI_CORRECTION_RAW = "raw"
AQI_CORRECTION_EPA = "epa"
AQI_CORRECTION_AQANDU = "aqandu"
AQI_CORRECTION_LRAPA = "lrapa"
AQI_CORRECTIONS_ALL: tuple[str, ...] = (
    AQI_CORRECTION_RAW,
    AQI_CORRECTION_EPA,
    AQI_CORRECTION_AQANDU,
    AQI_CORRECTION_LRAPA,
)
DEFAULT_AQI_CORRECTIONS: tuple[str, ...] = (
    AQI_CORRECTION_RAW,
    AQI_CORRECTION_EPA,
)

# Channel disagreement defaults match PurpleAir's own data-quality
# threshold: A and B are considered to disagree when their PM2.5 differ
# by at least 5 µg/m³ AND at least 70 % relative.
DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3 = 5.0
DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT = 70.0
