"""Constants for the PurpleAir Local integration."""

DOMAIN = "purpleair_local"

# Sensor docs recommend no faster than once every 10 seconds; we default
# to the natural /json averaging window (2 minutes) so consecutive polls
# usually see fresh data.
DEFAULT_SCAN_INTERVAL_S = 120
MIN_SCAN_INTERVAL_S = 15

# Default total timeout for a single HTTP call to the sensor. Healthy
# sensors respond in well under 500 ms on a LAN; 10 s gives generous
# headroom for a slow Wi-Fi cycle without making the coordinator hang.
DEFAULT_REQUEST_TIMEOUT_S = 10.0
