# PurpleAir Local

A Home Assistant custom integration that polls PurpleAir PA-II (and
compatible) sensors directly on the LAN — no cloud, no API key.

**Status:** early development. Design is locked, implementation has not
started. See [DESIGN.md](DESIGN.md) for architecture and decisions, and
[TODO.md](TODO.md) for the roadmap.

## Why local

- Works with the internet down.
- No PurpleAir cloud rate limits, no API key, no third-party data sharing.
- Sub-minute polling is possible (sensor minimum is 10 s; we default to
  120 s to match the sensor's natural averaging cadence).

## What it will provide

Per sensor, on first release:

- PM1.0 / PM2.5 / PM10 mass concentration (µg/m³), per channel and as a
  cross-channel primary.
- PM2.5 AQI in the sensor's raw form **and** with the EPA (Barkjohn 2021)
  correction applied. AQandU and LRAPA corrections are available via the
  options flow.
- Temperature, humidity, dewpoint, pressure (when the sensor has a BME).
- Particle-count entities (per 0.1 L of air, six bins) — created but
  disabled by default.
- Channel-disagreement binary sensor using PurpleAir's own
  `≥5 µg/m³ AND ≥70 %` thresholds.
- Diagnostics: RSSI, uptime, firmware version, free memory.

Sensors with only one laser (some indoor PA-II units) skip the channel-B
entities and the disagreement binary sensor automatically.

## Install (eventually)

Not yet installable. When ready:

1. Add this repo to HACS as a custom integration.
2. Install "PurpleAir Local".
3. Restart Home Assistant.
4. Settings → Devices & Services → Add Integration → "PurpleAir Local".
5. Enter the sensor's IP. Repeat for each sensor.

If a sensor's IP changes later (DHCP), edit it from the integration's
**Configure** screen — you do not need to delete and re-add the entry.

## License

MIT.
