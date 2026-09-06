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

# Interval for the second (live) coordinator, used only when live
# entities are enabled. 15 s rather than PurpleAir's 10 s floor:
# measured on a PA-II (fw 7.02), `pm2_5_atm` on ?live=true produced a
# new value only every ~19 s median (max 43 s) — the laser counter's
# own cycle, roughly the payload's `loggingrate: 15`. Detection latency
# is dominated by that cadence, so polling at 10 s would shave ~5 s off
# a 19-43 s budget while giving up the margin above the documented
# floor. Bounded by the same MIN/MAX as the averaged interval.
DEFAULT_LIVE_SCAN_INTERVAL_S = 15

# A live poll that fails must not immediately push entities to
# `unavailable`. Some PA-II units block ?live=true for ~30 s of every
# 120 s cycle, so isolated failures are expected firmware behaviour, not
# an outage — and an automation watching a live PM entity is worse off
# seeing `unavailable` than a value a few seconds stale.
#
# The tolerance is derived from the poll interval rather than being a
# fixed count, so the grace period always spans more than this many
# seconds no matter how fast the user polls (at 15 s that's 5 failures,
# at 37 s it's 2). 60 s comfortably outlasts one ~30 s block window plus
# scheduling jitter, while still surfacing a genuinely dead sensor
# quickly. Only the live coordinator uses this — the averaged one keeps
# failing fast, since it is the canonical series and backs `online`.
LIVE_FAILURE_GRACE_S = 60

# Default total timeout for a single HTTP call to the sensor. Healthy
# sensors respond in well under 500 ms on a LAN; 10 s gives generous
# headroom for a slow Wi-Fi cycle without making the coordinator hang.
DEFAULT_REQUEST_TIMEOUT_S = 10.0

# --- options flow keys ----------------------------------------------------

CONF_SCAN_INTERVAL_S = "scan_interval_s"
CONF_LIVE_ENTITIES = "live_entities"
CONF_LIVE_SCAN_INTERVAL_S = "live_scan_interval_s"
CONF_AQI_CORRECTIONS = "aqi_corrections"
CONF_AQI_COLOR_SCHEME = "aqi_color_scheme"
CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3 = "channel_disagreement_min_diff_ugm3"
CONF_CHANNEL_DISAGREEMENT_MIN_PCT = "channel_disagreement_min_pct"

DEFAULT_AQI_COLOR_SCHEME = "us_epa"

# Live entities are additive: enabling them adds a second set of
# measurement entities fed by ?live=true, it does not replace the
# averaged ones. Off by default — the averaged series is what belongs
# in history and long-term statistics, and live values are noisy (the
# firmware reports whole µg/m³, so at ~2 µg/m³ the AQI swings 4↔13 on
# quantization alone).
DEFAULT_LIVE_ENTITIES = False

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
