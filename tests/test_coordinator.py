"""Tests for PurpleAirCoordinator.

The client is mocked because we're not exercising the HTTP layer here;
api.py has its own tests for that. We do use a real HomeAssistant
instance via the `hass` fixture (from pytest_homeassistant_custom_component)
so the coordinator's scheduling, listener notification, and
UpdateFailed → last_update_success bookkeeping run for real.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.purpleair_local.api import (
    PurpleAirConnectionError,
    PurpleAirInvalidResponseError,
    PurpleAirTimeoutError,
)
from custom_components.purpleair_local.coordinator import PurpleAirCoordinator
from custom_components.purpleair_local.models import SensorReading


def _fake_client(host: str = "10.0.0.42"):
    """Return an AsyncMock pretending to be a PurpleAirClient."""
    client = AsyncMock()
    client.host = host
    return client


async def test_coordinator_happy_path_returns_sensor_reading(hass, indoor_payload):
    client = _fake_client()
    client.get_reading.return_value = indoor_payload

    coord = PurpleAirCoordinator(hass, client)
    await coord.async_refresh()

    assert coord.last_update_success is True
    assert isinstance(coord.data, SensorReading)
    assert coord.data.sensor_id == indoor_payload["SensorId"]
    # Raw payload kept for diagnostics download. Identity, not equality,
    # so we know it's the same dict instance the parser saw.
    assert coord.last_raw_payload is indoor_payload
    client.get_reading.assert_awaited_once()


async def test_coordinator_last_raw_payload_unchanged_on_failed_update(
    hass, indoor_payload
):
    """A failed poll must not overwrite the previous good payload."""
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client)
    await coord.async_refresh()
    assert coord.last_raw_payload is indoor_payload

    client.get_reading.side_effect = PurpleAirTimeoutError("nope")
    await coord.async_refresh()
    # The old payload is still there for diagnostics to surface.
    assert coord.last_raw_payload is indoor_payload


@pytest.mark.parametrize(
    "err",
    [
        PurpleAirConnectionError("connection refused"),
        PurpleAirTimeoutError("timed out"),
        PurpleAirInvalidResponseError("HTTP 500"),
    ],
)
async def test_coordinator_client_error_marks_update_failed(hass, err):
    client = _fake_client()
    client.get_reading.side_effect = err

    coord = PurpleAirCoordinator(hass, client)
    await coord.async_refresh()

    assert coord.last_update_success is False
    # The host should appear in the failure reason so logs are useful.
    assert "10.0.0.42" in str(coord.last_exception)


async def test_coordinator_malformed_payload_marks_update_failed(hass):
    client = _fake_client()
    client.get_reading.return_value = {}  # no SensorId → parser raises

    coord = PurpleAirCoordinator(hass, client)
    await coord.async_refresh()

    assert coord.last_update_success is False
    assert "malformed" in str(coord.last_exception).lower()


async def test_coordinator_scan_interval_default(hass):
    client = _fake_client()
    coord = PurpleAirCoordinator(hass, client)
    # Default per const.DEFAULT_SCAN_INTERVAL_S = 120
    assert coord.update_interval == timedelta(seconds=120)


async def test_coordinator_scan_interval_override(hass):
    client = _fake_client()
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=30)
    assert coord.update_interval == timedelta(seconds=30)


async def test_coordinator_recovers_after_transient_failure(
    hass, indoor_payload
):
    """Failure → success transitions should clear last_exception cleanly."""
    client = _fake_client()
    coord = PurpleAirCoordinator(hass, client)

    client.get_reading.side_effect = PurpleAirTimeoutError("once")
    await coord.async_refresh()
    assert coord.last_update_success is False

    client.get_reading.side_effect = None
    client.get_reading.return_value = indoor_payload
    await coord.async_refresh()
    assert coord.last_update_success is True
    assert coord.data.sensor_id == indoor_payload["SensorId"]
