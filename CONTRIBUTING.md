# Contributing

Thanks for considering a contribution. This guide is for human
contributors. If you're an AI coding agent, read
[`AGENTS.md`](AGENTS.md) instead — it has stricter rules.

## Reporting issues

File issues at <https://github.com/jpettitt/purpleair-local/issues>.
Useful bug reports include:

- The integration version (Settings → Devices & Services →
  PurpleAir Local → device page).
- The Home Assistant version.
- The sensor's hardware string (`hardwarediscovered` — easiest path
  is the integration's **Download diagnostics** button, which gives
  you a redacted dump including the raw `/json` payload).
- The behaviour you saw vs. what you expected.

Diagnostics download is the gold standard — it carries firmware
quirks the parser may not have accounted for and pre-redacts the
SensorId / MAC / lat / lon / SSID.

## Development setup

Python 3.13 or 3.14, plus Docker if you want to test inside Home
Assistant.

```sh
git clone https://github.com/jpettitt/purpleair-local
cd purpleair-local
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
make test                                  # baseline should be all green
```

Note: do **not** `pip install -e .` — there is no `[project]` block
in `pyproject.toml` on purpose. The PEP 660 path-hook shim that
editable installs create on Python 3.14 breaks Home Assistant's
integration loader. See the comment at the top of
[`pyproject.toml`](pyproject.toml) for the full story.

### Local HA testbed (Docker)

[`docker-compose.yml`](docker-compose.yml) bind-mounts
`custom_components/purpleair_local/` read-only into a disposable
Home Assistant container, so edits to the integration are picked up
by the next HA reload.

```sh
make ha-up        # boots HA at http://localhost:8123 (first start ~1 min)
make ha-logs      # tail container logs
make ha-restart   # restart HA after editing the integration
make ha-down      # stop, keep config
make ha-reset     # wipe runtime config — back to the onboarding screen
```

First boot walks through HA's user-creation flow; after that, add
the integration from **Settings → Devices & Services → Add
Integration → "PurpleAir Local"**. Runtime data (database, secrets,
logs) lives under `.dev/ha-config/` and is gitignored.

## Making changes

1. Branch off `main`. Naming conventions:
   - `feature/<short-name>` for new functionality
   - `fix/<short-name>` for bug fixes
   - `docs/<short-name>` for documentation-only changes
   - `issue-<number>-<short-description>` when addressing a tracked
     issue
2. Write a test that fails on `main` and passes with your change.
   The pattern depends on the area — see existing tests in
   [`tests/`](tests/) for templates.
3. Run `make test` and confirm everything stays green.
4. Update docs that describe the change ([`README.md`](README.md)
   for user-visible features, [`DESIGN.md`](DESIGN.md) for
   architecture / new firmware quirks, [`TODO.md`](TODO.md) for
   shipped / new follow-up items).
5. Commit with a meaningful one-line subject. The body should
   explain the *why*, not the *what*.
6. Push the branch and open a PR. The
   [PR template](.github/PULL_REQUEST_TEMPLATE.md) auto-populates
   the body — fill every section.

CI will run `pytest`, `hassfest`, `HACS validate`, and CodeQL. All
must pass before merge.

## Architecture & coding standards

This project sits on top of Home Assistant. PRs are reviewed against
[Home Assistant's published Python conventions][ha-core-agents] —
type hints, async-first, comment philosophy ("explain *why*, never
*what*"). The integration follows HA's standard custom-component
layout under `custom_components/<domain>/`.

[`DESIGN.md`](DESIGN.md) is the place to read before touching:

- The parser ([`models.py`](custom_components/purpleair_local/models.py))
  and the firmware-vs-docs discrepancies it tolerates.
- The AQI breakpoint table and the three correction formulas in
  [`aqi.py`](custom_components/purpleair_local/aqi.py).
- The channel-disagreement rule shared between
  [`binary_sensor.py`](custom_components/purpleair_local/binary_sensor.py)
  and the primary-value fallback in
  [`sensor.py`](custom_components/purpleair_local/sensor.py).

## Releases

Releases are maintainer-driven. The procedure is documented in
[`AGENTS.md`](AGENTS.md) under "Cutting a release" — version-only
PRs may need admin merge bypass when CodeQL skips analysis.

## Licence

MIT. By contributing you agree your contribution is licensed under
the same terms.

[ha-core-agents]: https://github.com/home-assistant/core/blob/dev/AGENTS.md
