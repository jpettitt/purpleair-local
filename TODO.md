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
4. **`aqi.py`** — pure functions for EPA (Barkjohn 2021), AQandU, LRAPA
   corrections, plus the EPA 24-hour PM2.5 → AQI breakpoint table.
   Unit-tested against published worked examples.
5. **`coordinator.py`** — `DataUpdateCoordinator` per sensor IP, 120 s
   default, configurable.
6. **`config_flow.py`** — user step: host, validate, derive name from
   `place` + MAC suffix, unique-id = `SensorId`.
7. **Options flow** — host (with SensorId guard), poll interval,
   AQI corrections to enable, channel-disagreement thresholds.
8. **`sensor.py`** — PM mass, AQI (raw + EPA default, AQandU/LRAPA
   optional), particle counts (disabled by default), environment,
   diagnostics. Skip entities whose source field is absent.
9. **`binary_sensor.py`** — `online`, `channel_disagreement` (only if
   both channels present).
10. **`diagnostics.py`** — redacted dump for bug reports.
11. **`strings.json` + `translations/en.json`** — config/options flow
    labels and errors.
12. **Manual smoke test** against both real sensors, documented.
13. **Tag `v0.1.0`** and cut a HACS release.

## Post-v0.1.0 (not committed)

- Zeroconf discovery if PA firmware ever advertises one.
- `?live=true` toggle per sensor.
- Multi-sensor "site average" derived entity (outdoor average of all
  outdoor sensors).
- HA core upstream PR if there's demand.
