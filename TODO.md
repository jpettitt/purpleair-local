# TODO

Roadmap toward a HACS-installable v0.1.0.

## v0.1.0 — first usable release

Locked in [DESIGN.md](DESIGN.md). Order matters; each step should be
landable on its own.

1. **Repo scaffolding** — `custom_components/purpleair_local/` with empty
   `manifest.json`, `const.py`, package init. `hacs.json` at root.
   GitHub Actions for hassfest + HACS validate + pytest.
   _Partially done: package skeleton, `manifest.json`, `const.py`, `hacs.json`,
   `pyproject.toml`, and `tests/` are in. GitHub Actions still TODO._
2. **`api.py`** — async aiohttp client around `GET /json`. Timeouts,
   single retry, surface `PurpleAirError` subclasses for caller.
   _Done on `feature/api-client`: 9 unit tests + live smoke against both
   real sensors green._
3. **`models.py` + parser** — `SensorReading` dataclass. Parser tolerates
   both `pm2.5_aqi` (dot) and `pm2_5_aqi` (underscore), `place=inside/outside`
   vs `indoor/outdoor`, and any missing field. Unit tests against the
   redacted indoor + outdoor fixtures.
   _Done on `feature/api-client`: 31 unit tests (fixture-driven + synthetic
   quirks) + live smoke through api → parser against both real sensors green._
4. **`aqi.py`** — pure functions for EPA (Barkjohn 2021), AQandU, LRAPA
   corrections, plus the EPA 24-hour PM2.5 → AQI breakpoint table.
   Unit-tested against published worked examples.
   _Done on `feature/api-client`: 44 unit tests covering correction math,
   all 2024-revised band boundaries, truncation, extrapolation above 500,
   category labels, and None propagation. Live smoke through api → parser
   → AQI against both real sensors green._
5. **`coordinator.py`** — `DataUpdateCoordinator` per sensor IP, 120 s
   default, configurable.
   _Done on `feature/coordinator`: 8 unit tests (happy path, all three
   client error types, malformed payload, default/override scan interval,
   transient-failure recovery) + live smoke through coordinator against
   both real sensors green. Bumped `hacs.json` minimum HA to 2024.10
   because `config_entry=` kwarg on DataUpdateCoordinator was added then._
6. **`config_flow.py`** — user step: host, validate, derive name from
   `place` + MAC suffix, unique-id = `SensorId`.
   _Done on `feature/coordinator`: single user step, normalizes host
   (strips scheme/trailing slash/whitespace), maps client errors to
   `cannot_connect` / `invalid_response`, derives title "Indoor e7fc"
   from place + last-4 of MAC. Re-running the flow with the same
   SensorId updates host in place (DHCP-change recovery). 17 unit
   tests + minimal __init__.py setup stubs. Test infra switched off
   `pip install -e .` because the PEP 660 path-hook shim breaks HA's
   integration loader on Python 3.14; test deps now live in
   `requirements_test.txt`._
7. **Options flow** — host (with SensorId guard), poll interval,
   AQI corrections to enable, channel-disagreement thresholds.
   _Done on `feature/coordinator`: single-step options form with all four
   fields, host validated against the entry's unique SensorId on change
   (refuses to silently rebind to a different physical sensor),
   pre-filled with current values or hard-coded defaults. 10 unit
   tests + reload-on-options-change listener wired in __init__.py._
8. **`sensor.py`** — PM mass, AQI (raw + EPA default, AQandU/LRAPA
   optional), particle counts (disabled by default), environment,
   diagnostics. Skip entities whose source field is absent.
   _Done on `feature/sensor-entities`: full entity catalog with primary +
   per-channel variants (dual only), conditional environment, diagnostic
   category. __init__.py now builds the coordinator, runs first-refresh
   (ConfigEntryNotReady on failure), stores in hass.data, forwards to
   sensor platform. 18 unit tests + live smoke through coordinator +
   build_entities against both real sensors (20 entities single / 30
   dual, all populated)._
9. **`binary_sensor.py`** — `online`, `channel_disagreement` (only if
   both channels present).
   _Done on `feature/binary-sensors`: two entities (online =
   connectivity class; channel_disagreement = problem class, dual-only,
   thresholds from options). Refactored shared base entity out of
   sensor.py into entity.py so the new platform doesn't import from a
   sibling. 15 unit tests + live smoke against both real sensors green._
10. **`diagnostics.py`** — redacted dump for bug reports.
    _Done on `feature/diagnostics`: includes config entry + coordinator
    health + last raw /json payload, with host / SensorId / lat / lon /
    ssid / Geo all redacted. Coordinator now stashes `last_raw_payload`
    so the download captures real firmware quirks for bug reports. 5
    diagnostics tests + 1 new coordinator test._
11. **`strings.json` + `translations/en.json`** — config/options flow
    labels and errors.
    _Done on `feature/translations-review`: audited strings vs code,
    confirmed no drift, added 3 regression tests (en mirrors strings;
    every `errors["base"]` key in config_flow.py has a string; the
    `already_configured` abort has one). Entity-name translations
    deferred — entities use `_attr_name` directly, which is fine for
    English-only v0.1._
12. **Manual smoke test** against both real sensors, documented.
    _In progress on `feature/ha-dev-docker`: `docker-compose.yml` + Makefile
    targets (`ha-up` / `ha-down` / `ha-logs` / `ha-restart` / `ha-reset`)
    spin up HA in a container with `custom_components/purpleair_local/`
    bind-mounted. `.dev/configuration.yaml.example` seeded with
    `default_config:` + debug logging for our domain. Container booted
    cleanly (HA 2026.5.2, no import errors). User onboards
    at <http://localhost:8123> then adds the integration from Settings →
    Devices & Services; the actual click-through smoke test is the user's
    to perform and document in this README section._
13. **Tag `v0.1.0`** and cut a HACS release.

## Before announcing v0.1.0

- Watch the HACS validator: brand assets now ship in-tree under
  `custom_components/purpleair_local/brand/` (per the Feb 2026
  Brands Proxy API change; the brands repo stopped accepting new
  `custom_integrations/` PRs). The HACS validator still flags
  missing brands-repo entries — we suppress that with
  `ignore: brands` in `.github/workflows/validate.yml`. Drop the
  ignore once the HACS validator recognizes in-tree brand dirs.

## Post-v0.1.0 (not committed)

- Zeroconf discovery if PA firmware ever advertises one.
- `?live=true` toggle per sensor.
- Multi-sensor "site average" derived entity (outdoor average of all
  outdoor sensors).
- HA core upstream PR if there's demand.
