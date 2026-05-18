# PurpleAir Local

[![HACS Custom][hacs-shield]][hacs]
[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
![Maintenance][maintenance-shield]
[![Validate][validate-shield]][validate]
[![Tests][tests-shield]][tests]

A Home Assistant custom integration that polls PurpleAir PA-II (and
compatible) sensors directly on the LAN — no cloud, no API key.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.][hacs-install-shield]][hacs-install]
&nbsp;
[![Open your Home Assistant instance and start setting up a new integration.][add-shield]][add]

See [DESIGN.md](DESIGN.md) for architecture and the decisions behind
the implementation.

## Why local

- Works with the internet down.
- No PurpleAir cloud rate limits, no API key, no third-party data sharing.
- Sub-minute polling is possible (sensor minimum is 10 s; we default to
  120 s to match the sensor's natural averaging cadence).

## What it provides

Per configured sensor:

- **PM1.0 / PM2.5 / PM10 mass concentration** (µg/m³, ATM density) —
  always a primary entity; channel A and channel B added on dual-laser
  units. Primary on dual is the simple A/B average for v0.1.
- **PM2.5 AQI** — one entity per enabled correction, using the
  2024-revised US EPA breakpoint table. Default: raw (no concentration
  correction) and EPA (Barkjohn 2021). AQandU and LRAPA available via
  the options flow.
- **Temperature, humidity, dewpoint, pressure** — only when the sensor
  has a BME280 or BME680. BME680 values preferred when both are present.
  Note: the temperature reading runs a few degrees high. The BME sits
  inside the PurpleAir enclosure and picks up heat from the laser
  counters and the ESP processor; PurpleAir's own guidance for outdoor
  units is to subtract roughly 8 °F (≈ 4.4 °C) from the reported value.
- **VOC resistance** (Ω) — only when the sensor has a BME680.
- **Particle counts** in six size bins, primary only, disabled by
  default. Enable individually from the device page when you want them.
- **Diagnostics** — WiFi signal, uptime, free heap, firmware version,
  last reported timestamp. Free heap and firmware are disabled by
  default.
- **Online** binary sensor — reflects the coordinator's last poll status.
- **Channel disagreement** binary sensor — dual-laser only. Trips when
  both PurpleAir thresholds are crossed (default ≥5 µg/m³ AND ≥70 %),
  configurable in options.

Single-laser sensors (some indoor PA-II units) skip the channel-B
entities and the disagreement binary sensor automatically.

## Install

The quick way — two clicks if your Home Assistant browser session is
already authenticated:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.][hacs-install-shield]][hacs-install]
&nbsp;
[![Open your Home Assistant instance and start setting up a new integration.][add-shield]][add]

The first button opens HACS pointed at this repo (you'll still need to
click **Download** in HACS and then restart Home Assistant). The second
button opens the integration's config flow once it's installed.

The manual equivalent:

1. HACS → Integrations → ⋮ → Custom repositories.
2. Add `https://github.com/jpettitt/purpleair-local` as an Integration.
3. Install "PurpleAir Local" from the list, then restart Home Assistant.
4. Settings → Devices & Services → Add Integration → "PurpleAir Local".
5. Enter the sensor's IP. Repeat for each sensor.

If a sensor's IP later changes (DHCP), edit it from the integration's
**Configure** screen — the integration verifies the SensorId still
matches and updates the host in place. No need to delete and re-add.

## Development

### Test suite

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
make test
```

### Local HA testbed (Docker)

A `docker-compose.yml` at the repo root mounts
`custom_components/purpleair_local/` read-only into a disposable Home
Assistant container, so edits to the integration are picked up by HA on
the next reload. Requires Docker (Desktop or compatible).

```sh
make ha-up        # boots HA at http://localhost:8123 (first start ~1 min)
make ha-logs      # tail container logs
make ha-restart   # restart HA after editing the integration
make ha-down      # stop, keep config
make ha-reset     # wipe runtime config — back to the onboarding screen
```

First boot walks through HA's user-creation flow; after that, add the
integration from **Settings → Devices & Services → Add Integration →
"PurpleAir Local"**. Runtime data (database, secrets, logs) lives under
`.dev/ha-config/` and is gitignored.

## License

MIT.

<!-- Badge / link references -->

[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[hacs]: https://github.com/hacs/integration
[releases-shield]: https://img.shields.io/github/v/release/jpettitt/purpleair-local?style=for-the-badge
[releases]: https://github.com/jpettitt/purpleair-local/releases
[license-shield]: https://img.shields.io/github/license/jpettitt/purpleair-local?style=for-the-badge
[maintenance-shield]: https://img.shields.io/maintenance/yes/2026?style=for-the-badge
[validate-shield]: https://img.shields.io/github/actions/workflow/status/jpettitt/purpleair-local/validate.yml?label=Validate&style=for-the-badge
[validate]: https://github.com/jpettitt/purpleair-local/actions/workflows/validate.yml
[tests-shield]: https://img.shields.io/github/actions/workflow/status/jpettitt/purpleair-local/test.yml?label=Tests&style=for-the-badge
[tests]: https://github.com/jpettitt/purpleair-local/actions/workflows/test.yml
[hacs-install-shield]: https://my.home-assistant.io/badges/hacs_repository.svg
[hacs-install]: https://my.home-assistant.io/redirect/hacs_repository/?owner=jpettitt&repository=purpleair-local&category=integration
[add-shield]: https://my.home-assistant.io/badges/config_flow_start.svg
[add]: https://my.home-assistant.io/redirect/config_flow_start/?domain=purpleair_local
