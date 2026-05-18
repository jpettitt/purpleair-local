# PurpleAir Local — Home Assistant Integration

A HACS custom integration that reads PurpleAir PA-II (and compatible) sensors
directly over the LAN, with no dependency on the PurpleAir cloud API.

Status: design draft, not yet implemented.

## Goals

1. **Local-only.** Every read goes to a sensor's IP on the LAN. No internet
   round-trip, no API key, no cloud rate limits.
2. **Multi-sensor from day one.** A typical install has at least one indoor
   and one outdoor sensor; the integration must treat that as the common case,
   not a follow-up feature.
3. **Useful AQI numbers out of the box.** Expose the sensor's raw `pm2_5_aqi`
   *and* the community-standard corrections (US EPA, AQandU, LRAPA) as
   first-class entities, so dashboards and automations don't need template
   sensors.
4. **Honest about channel health.** Surface the A vs. B disagreement that
   indicates a laser is failing or a bug is in the intake, instead of silently
   averaging.
5. **HACS-installable**, with a clean enough layout to upstream to HA core
   later if it's worth doing.

## Non-goals (for v1)

- PurpleAir cloud API support.
- Writing data back to the sensor (config, recalibration).
- Historical / SD card retrieval.
- Auto-discovery via zeroconf. PurpleAir devices don't advertise a standard
  service type; we'll revisit if firmware adds one.
- Anything ThingSpeak-related. The `status_4/5/8/9` fields are deprecated
  and we ignore them.

## The local API, in one page

Source: PurpleAir community docs
([endpoint](https://community.purpleair.com/t/view-sensor-data-locally-over-wifi-json-data/5513),
[field list](https://community.purpleair.com/t/sensor-json-documentation/6917)).
Current as of firmware 7.04.

Two endpoints on every networked sensor:

| URL | Behavior |
| --- | --- |
| `http://<ip>/json` | Two-minute averaged reading. Same data the sensor pushes to the PurpleAir map. |
| `http://<ip>/json?live=true` | Latest raw reading, no averaging. Noisier. |

Notes from the docs:

- "We recommend doing so at least 10 seconds apart" — i.e. don't poll faster
  than 0.1 Hz.
- Temperature and a few other fields update on their own ~2-minute cadence
  regardless of how often you poll.
- No auth. No HTTPS. Plain HTTP on port 80.
- The response is JSON; **no auto-refresh** in a browser — every fresh value
  requires a new request.

### Doc vs. firmware: known discrepancies

The community docs ([linked above](#the-local-api-in-one-page)) describe
firmware 7.04. Real responses from sensors running 7.02 disagree in a few
places that the parser must handle:

| Doc says | Firmware actually returns | Notes |
| --- | --- | --- |
| `pm2_5_aqi`, `pm2_5_aqi_b` | `pm2.5_aqi`, `pm2.5_aqi_b` | Literal `.` in the JSON key. Same for `p25aqic` (the LED-RGB string). |
| `place: "indoor"` / `"outdoor"` | `place: "inside"` / `"outside"` | We accept both, normalize internally. |
| (not listed) | `pa_latency`, `latency` | Round-trip times to PurpleAir; ignore. |
| (not listed) | `status_7` | Undocumented; treat like other `status_*`. |

The parser treats the docs as a superset of expected fields and the
firmware as authoritative. New unknown keys are logged at debug level and
ignored.

### Fields we care about

Identity / metadata (used to build the HA device):

- `SensorId` — MAC, our unique ID.
- `hardwareversion`, `hardwarediscovered`, `version` — model + firmware.
- `lat`, `lon`, `place` — `place` is `"indoor"` or `"outdoor"`.
- `DateTime`, `uptime`, `rssi`, `wlstate`, `ssid` — diagnostics.

Particulate matter, per channel (A is unsuffixed, B has `_b`):

- Mass concentration (µg/m³): `pm1_0_cf_1`, `pm2_5_cf_1`, `pm10_0_cf_1`
  and the ATM variants `pm1_0_atm`, `pm2_5_atm`, `pm10_0_atm`.
- AQI as computed on-device: `pm2_5_aqi` (and `_b`).
- Particle counts per dL: `p_0_3_um` … `p_10_0_um` (and `_b`).

Environment (only present if the BME280/BME680 is detected):

- `current_temp_f`, `current_humidity`, `current_dewpoint_f`, `pressure`
  (the latter in millibar).
- BME680 variants suffixed `_680`, plus `gas_680` for VOC (marked
  experimental in the docs).

Why `cf_1` vs `atm` matters: PurpleAir applies both Plantower density curves
to every reading. `cf_1` ("indoor" curve) tends to read higher; `atm`
("atmospheric" curve) is what's typically reported outdoors. **Every
published AQI correction formula takes `pm2_5_cf_1` as input**, so we keep
that one regardless of where the sensor is placed.

## Architecture

Standard modern HA integration shape — nothing exotic.

```
custom_components/purpleair_local/
├── __init__.py            # async_setup_entry / async_unload_entry
├── manifest.json          # domain, version, deps, iot_class=local_polling
├── config_flow.py         # user step + options flow
├── const.py               # DOMAIN, defaults, field constants
├── coordinator.py         # DataUpdateCoordinator subclass, one per sensor
├── api.py                 # thin httpx/aiohttp client around /json
├── models.py              # @dataclass for a parsed sensor reading
├── aqi.py                 # EPA / AQandU / LRAPA conversions, pure functions
├── sensor.py              # SensorEntity definitions
├── binary_sensor.py       # channel-disagreement, sensor-online
├── diagnostics.py         # redacted dump for bug reports
├── strings.json + translations/en.json
└── tests/                 # see Testing
```

### Data flow

```
                       ┌────────────────────┐
   HA polling tick ──► │  PACoordinator     │ ── GET /json ──►  sensor
                       │  (per sensor IP)   │ ◄── JSON ────────
                       └─────────┬──────────┘
                                 │ parsed SensorReading
                  ┌──────────────┼──────────────┐
                  ▼              ▼              ▼
            SensorEntity   SensorEntity   BinarySensorEntity
            (pm2.5 EPA)    (temp_f)       (channel_disagreement)
```

One `DataUpdateCoordinator` per sensor IP — keeps a failing sensor from
stalling reads on the healthy ones, and lets each sensor have its own poll
interval if we ever expose that.

### Polling cadence

- Default: **120 s**, matching the natural cadence of `/json`.
- Minimum allowed in the options flow: **15 s** (safely above the docs'
  10 s floor).
- Endpoint: always `/json` (averaged). `?live=true` is noisier and there's
  no good reason to default to it; we may expose a per-sensor toggle later
  if anyone asks.

### Config flow

User step (single sensor at a time, "add another" is just running the flow
again, which is what HA users expect):

1. **Host** — IP or hostname. We validate by issuing a single `GET /json`
   with a short timeout.
2. From the response we read `SensorId` and use it as the config entry's
   unique ID, so re-adding the same physical sensor under a new IP updates
   the existing entry instead of creating a duplicate.
3. **Name** — prefilled from `place` (normalized to "Indoor"/"Outdoor")
   plus a short MAC suffix; user can override.

Options flow (everything reconfigurable without removing the integration):

- **Host / IP** — editable. Common case: a sensor's DHCP lease changed and
  the entry's host is now stale. Saving a new host triggers a validation
  poll against the new address; if `SensorId` matches the entry's unique
  ID we keep the entry and all entity history. If it doesn't match, we
  reject the change with a clear error rather than silently rebinding
  to a different physical device.
- Poll interval (seconds, default 120, min 15).
- AQI corrections to enable (multi-select: Raw, EPA, AQandU, LRAPA).
  Default per user: **Raw + EPA**.
- Channel-disagreement thresholds (see below).
- Particle-count entities: created by HA but **disabled by default**;
  users enable individually from the device page.

### Devices and entities

One HA **device** per sensor, identified by `SensorId`. Manufacturer
`PurpleAir`, model from `hardwarediscovered`, sw_version from `version`,
configuration_url `http://<ip>/`.

Per device, the following entities (those that have no source data are
simply not created — e.g. a sensor without a BME680 gets no `gas_680`):

**Air quality — Channel A, Channel B, and a derived "primary"**

For each of channels A, B, and a primary (= average when both healthy, else
whichever is healthy):

- `pm1_0` (µg/m³, ATM)
- `pm2_5` (µg/m³, ATM)
- `pm10_0` (µg/m³, ATM)
- `pm2_5_aqi_raw` — what the device computed
- `pm2_5_aqi_epa` — Barkjohn 2021 EPA correction (the formula HA's PurpleAir
  cloud integration also uses)
- `pm2_5_aqi_aqandu` — AQandU correction
- `pm2_5_aqi_lrapa` — LRAPA correction (wood-smoke-tuned)

The corrections all consume `pm2_5_cf_1` plus relative humidity; we hold the
formulas in `aqi.py` as pure functions and unit-test them against published
worked examples. Each AQI entity exposes the underlying corrected µg/m³ as
an attribute so power users can build their own breakpoints.

**Particle counts** (per channel, hidden by default — useful but noisy):
`p_0_3_um`, `p_0_5_um`, `p_1_0_um`, `p_2_5_um`, `p_5_0_um`, `p_10_0_um`.

**Environment** (only if the BME is present):
`temperature`, `humidity`, `dewpoint`, `pressure`. We prefer the BME680
fields when both are present. Temperature is presented in °F as the sensor
reports it; HA's unit system handles conversion.

**Diagnostics** (entity_category=diagnostic, disabled-by-default for the
noisy ones):
`rssi`, `uptime`, `free_memory`, `firmware_version`, `last_seen`.

**Binary sensors:**
- `channel_disagreement` — see below.
- `online` — true while the coordinator's last update succeeded.

### Channel disagreement

PurpleAir's own data-quality flag treats channels as disagreeing when
`|A − B| ≥ 5 µg/m³` **and** the relative difference is `≥ 70 %`. We use
those exact thresholds as the default, configurable in the options flow.
When the flag trips, the binary sensor goes on and the "primary" PM/AQI
entities fall back to whichever channel still tracks recent history
(lowest short-term variance), rather than silently averaging garbage with
good data.

**Single-channel sensors** (e.g. the user's indoor unit reports
`hardwarediscovered: 2.0+BME280+PMSX003-A` with no `PMSX003-B`) skip this
machinery entirely: no `_b` entities are created, the disagreement binary
sensor is not created, and the "primary" PM/AQI entities are simply
channel A.

### Error handling and missing fields

Not all documented fields exist on every sensor. The PA-II we tested showed
three distinct cases of "missing":

- **Missing hardware** — single-laser unit has no `pm*_b`, no `p_*_um_b`,
  no `pm2.5_aqi_b`. A unit without a BME has no environment fields. A
  unit with only a BME280 has no `*_680` fields and no `gas_680`.
- **Conditional fields** — `response`, `response_date` only present when
  the user configured a Data Processor. Some `status_*` indices appear
  only when their subsystem ran.
- **Firmware variation** — 7.02 uses `pm2.5_aqi` (dot); a future firmware
  may switch to `pm2_5_aqi` (underscore). Parser accepts either.

Runtime behavior:

- HTTP error or timeout → coordinator marks the update failed; entities
  go `unavailable` after one missed cycle (HA default behavior).
- Field present at setup but missing on a later poll → the entity reports
  `unknown` for that cycle, not `unavailable`.
- Field never present at setup → the entity is not created at all. We
  do not create "stub" entities that perpetually report `unknown`.
- IP changes (DHCP) → user edits Host in the options flow (preferred), or
  re-runs the config flow. Matching `SensorId` rebinds to the existing
  entry; mismatch is rejected.

## AQI correction formulas

Three corrections are implemented as pure functions in `aqi.py`. All
take `pm_cf1` (µg/m³, from `pm2_5_cf_1`) plus `rh` (%, from
`current_humidity`) where applicable. All return corrected µg/m³,
clamped at 0, which is then run through the EPA 24-hour PM2.5
breakpoint table to produce an integer AQI.

- **EPA (Barkjohn et al., 2021):**
  `corrected = 0.524 * pm_cf1 - 0.0862 * rh + 5.75`
- **AQandU (University of Utah):** `corrected = 0.778 * pm_cf1 + 2.65`
- **LRAPA (Lane Regional Air Protection Agency, OR):**
  `corrected = 0.5 * pm_cf1 - 0.66` (wood-smoke-tuned; under-corrects
  in non-smoke conditions)

If the EPA's correction evolves further (a 5-piece extension for very
high concentrations already exists and is what the AirNow Fire and
Smoke Map uses today), we add it as an additional option rather than
silently changing what "EPA" means in this integration.

### AQI breakpoint table

We use the **2024-revised** US EPA PM2.5 sub-index of the AQI
(effective 2024-05-06, 89 FR 16202). Notably this drops AQI 50 from
12.0 to 9.0 µg/m³ and tightens the upper bands.

| AQI | µg/m³ (upper) | Category |
| --- | --- | --- |
| 50 | 9.0 | Good |
| 100 | 35.4 | Moderate |
| 150 | 55.4 | Unhealthy for Sensitive Groups |
| 200 | 125.4 | Unhealthy |
| 300 | 225.4 | Very Unhealthy |
| 500 | 325.4 | Hazardous |

Input PM2.5 is truncated to one decimal place before lookup per the
AirNow Technical Assistance Document. Values above 325.4 µg/m³ are
**extrapolated** using the top band's slope rather than capped at 500,
so wildfire-era readings (which routinely exceed 500 µg/m³ in
Northern California) still produce a meaningful numeric signal for
automations.

## Testing

- **Unit tests** for `aqi.py` against published worked examples for each
  formula, plus breakpoint table boundary cases (0, 12, 35.4, 55.4,
  150.4, 250.4, 350.4, 500.4 µg/m³).
- **Snapshot tests** for the parser using captured `/json` payloads from
  the user's real indoor and outdoor sensors (committed under
  `tests/fixtures/`, with `SensorId`, `lat`, `lon`, `ssid` redacted).
- **Coordinator tests** exercising: clean read, HTTP timeout, malformed
  JSON, single-channel sensor, BME-less sensor, channel-disagreement
  trip and recovery.
- **Config-flow tests** for: happy path, unreachable host, duplicate
  SensorId (existing entry update), options-flow validation of interval
  bounds.
- One **manual smoke test** against each real sensor before each release,
  documented in README.

## Repo layout (HACS)

```
purple-air-local/                 ← repo root
├── README.md
├── DESIGN.md                     ← this file
├── TODO.md
├── hacs.json
├── custom_components/purpleair_local/
│   └── …                          ← as above
├── tests/
│   └── …
└── .github/workflows/
    ├── validate.yml              ← hassfest + HACS validation
    └── test.yml                  ← pytest matrix
```

`manifest.json` highlights:

```jsonc
{
  "domain": "purpleair_local",
  "name": "PurpleAir Local",
  "iot_class": "local_polling",
  "config_flow": true,
  "integration_type": "device",
  "requirements": ["aiohttp"],   // already in HA core; listed for clarity
  "version": "0.1.0"
}
```

## Decisions locked

- **Repo name:** `purpleair-local`. Standard HACS layout under
  `custom_components/purpleair_local/`.
- **Default AQI corrections:** Raw + EPA (others available via options).
- **Particle counts:** entities created, disabled by default.
- **Channel disagreement:** PurpleAir's `≥5 µg/m³ AND ≥70%` thresholds.
- **IP reconfiguration:** required, lives in the options flow, guarded
  by `SensorId` match.
- **Missing fields:** handled per the "Error handling and missing fields"
  section above.
- **Fixture sensors:** indoor `192.168.203.101`, outdoor
  `192.168.203.100`. Captured payloads live under `tests/fixtures/`
  with `SensorId`, `lat`, `lon`, `ssid`, and `Geo` redacted.

## References

- [View Sensor Data Locally Over WiFi (JSON Data)](https://community.purpleair.com/t/view-sensor-data-locally-over-wifi-json-data/5513)
- [Sensor JSON Documentation](https://community.purpleair.com/t/sensor-json-documentation/6917)
- [Local JSON endpoint documentation](https://community.purpleair.com/t/local-json-endpoint-documentation/6097)
- [Barkjohn et al. 2021 EPA correction](https://community.purpleair.com/t/is-there-a-field-that-returns-data-with-us-epa-pm2-5-conversion-formula-applied/4593)
