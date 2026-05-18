"""Tests for the PurpleAir Local config flow.

Strategy: patch `PurpleAirClient` at the symbol the config flow imports
so we don't hit the network. The flow itself runs through HA's real
config-entries machinery (provided by the `hass` fixture from
pytest_homeassistant_custom_component).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType

from custom_components.purpleair_local.api import (
    PurpleAirConnectionError,
    PurpleAirInvalidResponseError,
    PurpleAirTimeoutError,
)
from custom_components.purpleair_local.config_flow import (
    _derive_title,
    _normalize_host,
)
from custom_components.purpleair_local.const import DOMAIN
from custom_components.purpleair_local.models import Place, SensorReading


def _patch_client(*, payload: dict | None = None, side_effect: Any = None):
    """Patch PurpleAirClient where the config flow imports it.

    Returns a mock whose `get_reading` either returns the given payload
    or raises the given exception. The flow constructs the client with
    `PurpleAirClient(host, session)`, so the patched class returns our
    AsyncMock from its constructor.
    """
    client = AsyncMock()
    client.host = "patched"
    if side_effect is not None:
        client.get_reading.side_effect = side_effect
    else:
        client.get_reading.return_value = payload
    return patch(
        "custom_components.purpleair_local.config_flow.PurpleAirClient",
        return_value=client,
    )


async def _start_user_flow(hass) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


# --- happy path -----------------------------------------------------------


async def test_user_flow_creates_entry(hass, outdoor_payload):
    with _patch_client(payload=outdoor_payload):
        result = await _start_user_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "192.168.1.42"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # outdoor fixture has SensorId 00:00:00:00:00:02 → last-4 "0002",
    # place "outside" → "Outdoor 0002"
    assert result["title"] == "Outdoor 0002"
    assert result["data"] == {CONF_HOST: "192.168.1.42"}
    assert result["result"].unique_id == outdoor_payload["SensorId"]


async def test_user_flow_strips_scheme_and_trailing_slash(
    hass, indoor_payload
):
    """A user pasting a browser URL should still succeed."""
    with _patch_client(payload=indoor_payload):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_HOST: "  http://192.168.1.42/  "},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "192.168.1.42"


# --- error mapping --------------------------------------------------------


@pytest.mark.parametrize(
    "err,error_key",
    [
        (PurpleAirConnectionError("refused"), "cannot_connect"),
        (PurpleAirTimeoutError("timeout"), "cannot_connect"),
        (PurpleAirInvalidResponseError("HTTP 500"), "invalid_response"),
    ],
)
async def test_user_flow_maps_client_errors(hass, err, error_key):
    with _patch_client(side_effect=err):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "10.0.0.1"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error_key}


async def test_user_flow_malformed_payload_treated_as_invalid_response(hass):
    """A parseable response that lacks SensorId is reported as invalid."""
    with _patch_client(payload={}):  # no SensorId → ValueError in parser
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "10.0.0.1"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_response"}


async def test_user_flow_can_recover_after_error(hass, indoor_payload):
    """After an error, the user can correct the input and succeed."""
    with _patch_client(side_effect=PurpleAirConnectionError("nope")):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "10.0.0.1"}
        )
        assert result["errors"] == {"base": "cannot_connect"}

    with _patch_client(payload=indoor_payload):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "192.168.1.42"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


# --- duplicate handling ---------------------------------------------------


async def test_duplicate_sensor_aborts_and_updates_host(
    hass, indoor_payload
):
    """Re-running the flow with the same SensorId updates host in place."""
    # First run: create the entry at one IP.
    with _patch_client(payload=indoor_payload):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "192.168.1.42"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.data[CONF_HOST] == "192.168.1.42"

    # Second run: same SensorId, new IP. Should abort with the host
    # field on the existing entry updated.
    with _patch_client(payload=indoor_payload):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "10.99.99.99"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.99.99.99"


# --- helpers --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("192.168.1.42", "192.168.1.42"),
        ("  192.168.1.42  ", "192.168.1.42"),
        ("http://192.168.1.42", "192.168.1.42"),
        ("HTTPS://192.168.1.42/", "192.168.1.42"),
        ("192.168.1.42/", "192.168.1.42"),
        ("purpleair.lan", "purpleair.lan"),
    ],
)
def test_normalize_host(raw, expected):
    assert _normalize_host(raw) == expected


def _reading(sensor_id: str, place: Place) -> SensorReading:
    """Build a minimal SensorReading for the title-derivation tests."""
    # We only care about the two fields _derive_title reads.
    from custom_components.purpleair_local.models import (
        ChannelReading,
        Diagnostics,
        ParticleCounts,
    )

    empty_counts = ParticleCounts(None, None, None, None, None, None)
    empty_channel = ChannelReading(
        None, None, None, None, None, None, None, empty_counts
    )
    return SensorReading(
        sensor_id=sensor_id,
        firmware_version=None,
        hardware_version=None,
        hardware_discovered=None,
        place=place,
        lat=None,
        lon=None,
        device_time=None,
        channel_a=empty_channel,
        channel_b=None,
        environment=None,
        diagnostics=Diagnostics(None, None, None, None, None),
    )


@pytest.mark.parametrize(
    "sensor_id,place,expected",
    [
        ("84:f3:eb:98:e7:fc", Place.INSIDE, "Indoor e7fc"),
        ("84:f3:eb:90:01:19", Place.OUTSIDE, "Outdoor 0119"),
        ("aa:bb:cc:dd:ee:ff", Place.UNKNOWN, "PurpleAir eeff"),
    ],
)
def test_derive_title(sensor_id, place, expected):
    assert _derive_title(_reading(sensor_id, place)) == expected
