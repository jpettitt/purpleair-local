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

An entry may run a *second* coordinator against the same sensor when live
entities are enabled; see "Live entities" below.

### Polling cadence

- Default: **120 s**, matching the natural cadence of `/json`.
- Minimum allowed in the options flow: **15 s** (safely above the docs'
  10 s floor).
- Endpoint: `/json` (averaged) for the canonical series. `?live=true` is
  available as an additive opt-in, never as a replacement.

### Live entities

Added in response to [issue #7](https://github.com/jpettitt/purpleair-local/issues/7):
an automation shutting down mechanical ventilation on a wood-smoke plume
cares about detection latency more than about measurement noise.

**Additive, not a mode switch.** Enabling live adds a second set of
measurement entities fed by `?live=true`, alongside the averaged ones.
A straight toggle was rejected: the averaged series is what belongs in
history and long-term statistics, so swapping it out would fix the
automation and ruin the graphs.

Implementation:

- **Two coordinators per entry**, not one coordinator fetching both.
  `PurpleAirEntity` derives its `DeviceInfo` from `coordinator.data.sensor_id`,
  so the second coordinator's entities attach to the same device with no
  entity-layer change, and the averaged entities keep their coordinator,
  unique_ids and history untouched. The alternative — one coordinator
  holding both payloads — would turn `coordinator.data` into a container
  and ripple through every entity value accessor for no user-visible gain.
- Only the averaged coordinator gates setup (`ConfigEntryNotReady`) and
  backs the `online` binary sensor. A live-endpoint failure must not blank
  the averaged entities.
- **Live is a subset**: PM mass, AQI and particle counts only, primary
  channel only. Environment and diagnostic fields are returned identically
  on both endpoints, so twins of those would be duplicates; per-channel
  entities exist to expose a failing laser, which is a slow-drift
  judgement better made on averaged data.
- Live entities carry a `_live` unique_id suffix and a " (live)" name
  suffix. Averaged ids are unchanged — a regression test pins the full
  dual-laser id set, since changing one would orphan a user's history.

**Measured behaviour** (one PA-II, fw 7.02, polling `?live=true` every 2 s
for 3 min — 90/90 HTTP 200, median 73 ms; see "The PA-II live block
window" below for a second unit that behaves very differently):

| field | value changes in 89 polls | median gap |
| --- | --- | --- |
| `pressure` | 80 | 2 s |
| `pm2_5_atm` | 11 | **19 s** (max 43 s) |

The BME fields stream instantaneously, but PM is bounded by the laser
counter's own cycle — roughly the payload's `loggingrate: 15`, with real
jitter. Hence a **15 s live default** rather than PurpleAir's 10 s floor:
detection latency is dominated by that 19–43 s cadence, so polling at 10 s
would shave ~5 s off the budget while giving up the documented margin.
`MIN_SCAN_INTERVAL_S` (15 s) applies to both intervals.

Live values are also quantized to whole µg/m³, so at ~2 µg/m³ the AQI
oscillates between 4 and 13 on quantization alone. Automations should
trigger on a sustained change (`for:`, or a filter/derivative helper),
never a single reading.

### The PA-II live block window

Reported on [#7](https://github.com/jpettitt/purpleair-local/issues/7)
after v0.2.0b1 shipped, and characterised from a one-hour debug log.

**Root cause: `?live=true` blocks behind the sensor's once-per-120 s
outbound HTTPS.** On its `period` boundary every PA-II connects out to
PurpleAir, and some units make a second connection elsewhere. Those are
blocking, and the live endpoint waits for them. The averaged `/json` does
not, because it serves the buffer the firmware has just computed rather
than reading the sensor hardware — which is why the two endpoints
behave so differently on the same device at the same moment.

When uploads succeed in a few hundred milliseconds the stall is
invisible. When one hangs, live hangs with it for exactly as long. So
the "block window" is not a fixed firmware property and not a defect
peculiar to certain units — **its width is however long that sensor's
upload takes to fail.**

On the reporting unit (hardware 2.0, firmware 7.02, installed 2018) the
release point sat at ~90.5 s into the cycle, and requests arriving
anywhere from ~60 s to ~86.5 s all completed there:

| arrived (cycle phase) | waited | completed (cycle phase) |
| --- | --- | --- |
| 86.5 s | 4.0 s | 90.5 s |
| 81.5 s | 9.0 s | 90.5 s |
| 71.4 s | 18.9 s | 90.3 s |
| ~60 s | >10 s (timed out) | — |

Across 33 clean stalls the completion phase only ever landed in
[91, 92]. It is endpoint-specific: the same device's averaged `/json`
never exceeded **76 ms** across 41 fetches in the same log. A PurpleAir
Flex (hardware 3.0, firmware 7.04) on the same network showed nothing —
294 live polls, max 321 ms.

**Demonstrated causally.** A PA-II here with healthy uploads showed one
2.6 s stall in 90 polls. Blocking its internet access at the firewall —
dropping packets rather than rejecting them — immediately produced
repeated **15 s** stalls, while `httpsuccess` froze and `httpsends` kept
climbing. The router log showed the cause: 7 TCP SYN retransmits at 3 s
intervals, ~18 s per upload target, starting every 120 s. Restoring
access returned the same sensor to 64 ms.

A second unit here reproduced it permanently with no firewall involved,
and its cause is the one worth telling users about: a **stale Data
Processor**. PurpleAir lets an owner forward readings to a third party,
configured **server-side in the PurpleAir account, not on the device** —
which is why the sensor's local `/config` page (a WiFi setup form) has
no field for it. This unit's target was a long-dead Weather Underground
link. Every cycle the sensor connected, waited, and gave up with
`response: -11` (`HTTPC_ERROR_READ_TIMEOUT`), running **~2.1 sends
against ~1.1 successes per cycle** and stalling live up to **36.6 s**.
It survived a reboot: clean for ~160 s after restart, then the target
was attempted again and the stalls resumed.

**Removing it fixed the stall outright**, closing the causal loop:

| indoor unit | sends/cycle | successes/cycle | stalls | worst |
| --- | --- | --- | --- | --- |
| dead Data Processor | 2.03 | 1.03 | 9 / 48 polls | 36.6 s |
| after removal | 1.55 | 1.55 | 1 / 60 polls | 1.90 s |

`response_date` stopped advancing entirely, confirming the sensor no
longer attempts the target.

So `httpsends` outrunning `httpsuccess` on a sensor with a `response`
field usually means a dead forwarding target the owner has forgotten
about, and removing it on the PurpleAir site fixes the stall at source.
That is worth checking before blaming the integration or the network.

Two connections per cycle means two chances to stall, and the unit
making them stalls roughly twice as often as the one that doesn't.

**Diagnosing it on any sensor:** compare `httpsends` against
`httpsuccess` in the payload. If sends outrun successes, that sensor has
a failing outbound connection and will stall its live endpoint by however
long the failure takes. `response` carries the ESP8266 HTTP client error
code for the second target when one exists, and `pa_latency` / `latency`
the per-target times. Where the failure can be fixed, that removes the
stall at source; everything below only stops us making it worse.

Consequences the implementation has to live with:

- **No poll interval avoids it.** A ~30 s window in a 120 s cycle is hit
  by roughly a quarter of polls whatever the interval.
- **No timeout covers it.** A request arriving at the window's start
  waits ~30 s. Raising the timeout that far would block the coordinator
  for a quarter of its duty cycle.
- **Intervals that appear to "work" are phase locks, not fixes.** At 37 s
  the reporter saw a stable 9 s stall because 3 × 37 = 111, and
  120 − 111 = 9: once a stall completes at the release point, the third
  following poll lands exactly 9 s before the next one, re-establishing
  the lock. That 9 s sat 1 s under the 10 s request timeout — a
  coincidence, not a safety margin.

So the integration treats the window as something to *survive*:

1. **Live requests are not retried** ([`api.py`](custom_components/purpleair_local/api.py)).
   An immediate retry lands in the same block window, doubling a 10 s
   stall to 20 s and putting a second request on a sensor that is
   already refusing to answer. The averaged endpoint keeps its retry.
2. **The live coordinator rides out consecutive transient failures**,
   serving the previous reading rather than dropping entities to
   `unavailable`. The allowance is derived from the poll interval so the
   grace period always exceeds `LIVE_FAILURE_GRACE_S` (60 s) — long
   enough to outlast a block window plus jitter, short enough to surface
   a genuinely dead sensor. Invalid responses are *not* ridden out;
   those are persistent, not transient.

Neither is a workaround for a bug on our side — the delay is the
sensor's own blocking upload. What we control is not amplifying it and
not reporting it to the user as an outage. The real fix, where a user
can apply it, is on the sensor: a PA-II whose uploads succeed does not
stall at all.

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
- `pm2_5_aqi_raw` — uncorrected ATM density run through the EPA
  breakpoint table. We **do not** pass through the on-device
  `pm2.5_aqi` field for per-channel entities because the firmware
  uses the pre-2024 EPA breakpoints while our table is post-2024;
  doing both would produce inconsistent numbers across the channel-A,
  channel-B, and primary "raw" entities for the same input. Users
  who want the literal on-device value can pull it from the
  diagnostics download.
- `pm2_5_aqi_epa` — Barkjohn 2021 EPA correction (the formula HA's
  PurpleAir cloud integration also uses)
- `pm2_5_aqi_aqandu` — AQandU correction
- `pm2_5_aqi_lrapa` — LRAPA correction (wood-smoke-tuned)

The corrections all consume `pm2_5_cf_1` plus relative humidity; we hold the
formulas in `aqi.py` as pure functions and unit-test them against published
worked examples.

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
Both the binary sensor and the primary-value fallback below evaluate the
condition on `pm2_5_atm` via a shared `channels_disagree()` helper, so
they can't drift out of sync.

When the flag trips:

- The `channel_disagreement` binary sensor turns on.
- The **primary** PM mass, PM2.5 AQI, and particle-count entities fall
  back to the *lower* of the two channels' values rather than averaging.
  The canonical PurpleAir failure mode is laser degradation (dust
  occlusion, end-of-life drift) which causes the affected channel to
  read high; the lower value is the conservative, usually-correct
  pick. The per-channel A and B entities are unaffected — they keep
  reporting their own readings so you can see exactly which laser is
  out of step.

A more sophisticated fallback (lowest-short-term-variance, requires
multi-poll history) was considered for v0.1 but deferred — the
lower-of-two rule covers the common stuck-high failure cleanly without
state, and the binary sensor surfaces the condition so automations can
react. Switching to variance-based selection is a non-breaking change
when we want it.

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
- **Conditional fields** — `response`, `response_date` and `latency`
  appear only when the owner has a Data Processor configured in their
  PurpleAir account (see "The PA-II live block window"). Some `status_*`
  indices appear only when their subsystem ran.
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
