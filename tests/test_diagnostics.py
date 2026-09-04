"""Tests for diagnostics.py.

Verifies that the diagnostics payload includes what a bug report needs
and redacts what users wouldn't want public (LAN IP, MAC, GPS, SSID).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.purpleair_local.api import PurpleAirTimeoutError
from custom_components.purpleair_local.const import (
    CONF_SCAN_INTERVAL_S,
    DOMAIN,
)
from custom_components.purpleair_local.coordinator import PurpleAirRuntime
from custom_components.purpleair_local.diagnostics import (
    async_get_config_entry_diagnostics,
)


REDACTION_MARKER = "**REDACTED**"


def _register(
    hass,
    payload,
    *,
    last_update_success: bool = True,
    last_exception=None,
    live_payload: dict | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.0.42"},
        options={CONF_SCAN_INTERVAL_S: 60},
        unique_id=payload["SensorId"],
    )
    entry.add_to_hass(hass)

    coord = MagicMock()
    coord.update_interval = timedelta(seconds=60)
    coord.last_update_success = last_update_success
    coord.last_exception = last_exception
    coord.last_raw_payload = payload

    live_coord = None
    if live_payload is not None:
        live_coord = MagicMock()
        live_coord.update_interval = timedelta(seconds=15)
        live_coord.last_update_success = True
        live_coord.last_exception = None
        live_coord.last_raw_payload = live_payload

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = PurpleAirRuntime(
        averaged=coord, live=live_coord
    )
    return entry


async def test_diagnostics_includes_entry_coordinator_payload(
    hass, outdoor_payload
):
    entry = _register(hass, outdoor_payload)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert set(diag) == {
        "entry",
        "coordinator",
        "last_raw_payload",
        "live_coordinator",
        "live_last_raw_payload",
    }
    # Live is off for this entry, so both live keys are null rather
    # than absent — a bug report shouldn't be ambiguous about it.
    assert diag["live_coordinator"] is None
    assert diag["live_last_raw_payload"] is None
    assert diag["entry"]["options"] == {CONF_SCAN_INTERVAL_S: 60}
    assert diag["coordinator"]["last_update_success"] is True
    assert diag["coordinator"]["last_exception"] is None
    assert diag["coordinator"]["update_interval_s"] == 60.0


async def test_diagnostics_redacts_identifying_fields(hass, outdoor_payload):
    entry = _register(hass, outdoor_payload)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    # In entry.data: host is redacted
    assert diag["entry"]["data"][CONF_HOST] == REDACTION_MARKER
    # The unique_id (MAC) is fully replaced, not partially preserved
    assert diag["entry"]["unique_id"] == REDACTION_MARKER

    # In the raw payload: SensorId, Geo, lat, lon, ssid all redacted
    payload = diag["last_raw_payload"]
    for key in ("SensorId", "Geo", "lat", "lon", "ssid"):
        assert payload[key] == REDACTION_MARKER, key


async def test_diagnostics_preserves_firmware_fields_for_debugging(
    hass, outdoor_payload
):
    """The whole point of including the raw payload is so bug reports
    can show the actual firmware shape (including dot-keyed AQI and
    other quirks). Make sure we don't redact those."""
    entry = _register(hass, outdoor_payload)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    payload = diag["last_raw_payload"]
    # Firmware version + hardware string + the dot-keyed AQI all survive.
    assert payload["version"] == "7.02"
    assert payload["hardwarediscovered"] == "2.0+BME280+PMSX003-B+PMSX003-A"
    assert "pm2.5_aqi" in payload  # dot-keyed firmware quirk


async def test_diagnostics_handles_no_payload_yet(hass, outdoor_payload):
    """Before the first successful poll, last_raw_payload is None."""
    entry = _register(hass, outdoor_payload, last_update_success=False)
    coord = hass.data[DOMAIN][entry.entry_id].averaged
    coord.last_raw_payload = None

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["last_raw_payload"] is None


async def test_diagnostics_surfaces_last_exception(hass, outdoor_payload):
    err = PurpleAirTimeoutError("timed out talking to host")
    entry = _register(
        hass, outdoor_payload, last_update_success=False, last_exception=err
    )
    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["coordinator"]["last_update_success"] is False
    assert "timed out" in diag["coordinator"]["last_exception"]


async def test_diagnostics_includes_live_coordinator_when_enabled(
    hass, outdoor_payload, indoor_payload
):
    """A bug report about live behaviour needs the live payload too.

    Uses a different payload for the live side so a swapped-key
    regression (live reported under the averaged key) would show up.
    """
    entry = _register(hass, outdoor_payload, live_payload=indoor_payload)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["live_coordinator"]["update_interval_s"] == 15.0
    assert diag["live_coordinator"]["last_update_success"] is True
    assert (
        diag["live_last_raw_payload"]["hardwarediscovered"]
        == indoor_payload["hardwarediscovered"]
    )
    # Averaged keys still carry the averaged series.
    assert diag["coordinator"]["update_interval_s"] == 60.0
    assert (
        diag["last_raw_payload"]["hardwarediscovered"]
        == outdoor_payload["hardwarediscovered"]
    )


async def test_diagnostics_redacts_the_live_payload_too(
    hass, outdoor_payload, indoor_payload
):
    """Redaction must not be averaged-only — live carries the same MAC/GPS."""
    entry = _register(hass, outdoor_payload, live_payload=indoor_payload)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    live = diag["live_last_raw_payload"]
    for key in ("SensorId", "lat", "lon"):
        if key in indoor_payload:
            assert live[key] == REDACTION_MARKER, key
