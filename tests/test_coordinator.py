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


async def test_coordinator_defaults_to_averaged_endpoint(hass, indoor_payload):
    """The canonical series must stay on /json, not ?live=true."""
    client = _fake_client()
    client.get_reading.return_value = indoor_payload

    coord = PurpleAirCoordinator(hass, client)
    await coord.async_refresh()

    assert coord.live is False
    client.get_reading.assert_awaited_once_with(live=False)


async def test_coordinator_live_requests_live_endpoint(hass, indoor_payload):
    client = _fake_client()
    client.get_reading.return_value = indoor_payload

    coord = PurpleAirCoordinator(hass, client, live=True)
    await coord.async_refresh()

    assert coord.live is True
    client.get_reading.assert_awaited_once_with(live=True)


async def test_live_coordinator_name_distinguishes_it(hass):
    """Two coordinators on one host must be tellable apart in logs."""
    client = _fake_client()
    averaged = PurpleAirCoordinator(hass, client)
    live = PurpleAirCoordinator(hass, client, live=True)

    assert averaged.name != live.name
    assert live.name.endswith(" live")


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


# --- live failure tolerance ------------------------------------------------


@pytest.mark.parametrize(
    "interval,expected",
    [
        (15, 5),   # 75 s
        (20, 4),   # 80 s
        (30, 3),   # 90 s
        (37, 2),   # 74 s
        (60, 2),   # 120 s — 1 would be exactly 60 s, which isn't "more than"
        (61, 1),   # 61 s
        (120, 1),  # 120 s
    ],
)
async def test_live_failure_tolerance_always_spans_over_60s(
    hass, interval, expected
):
    """The grace period must exceed 60 s at every supported interval."""
    coord = PurpleAirCoordinator(
        hass, _fake_client(), scan_interval_s=interval, live=True
    )
    assert coord.max_consecutive_failures == expected
    assert coord.max_consecutive_failures * interval > 60


async def test_averaged_coordinator_has_no_failure_tolerance(hass):
    """The canonical series fails fast — it backs `online`."""
    coord = PurpleAirCoordinator(hass, _fake_client(), scan_interval_s=120)
    assert coord.max_consecutive_failures == 0


async def test_live_rides_out_transient_failures_serving_last_reading(
    hass, indoor_payload
):
    """Entities must not flap to unavailable during the sensor's block window."""
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)
    await coord.async_refresh()
    assert coord.last_update_success is True

    client.get_reading.side_effect = PurpleAirTimeoutError("blocked")
    for i in range(coord.max_consecutive_failures):
        await coord.async_refresh()
        assert coord.last_update_success is True, f"flapped on failure {i + 1}"
        assert coord.data.sensor_id == indoor_payload["SensorId"]


async def test_live_gives_up_after_the_grace_period(hass, indoor_payload):
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)
    await coord.async_refresh()

    client.get_reading.side_effect = PurpleAirTimeoutError("still blocked")
    for _ in range(coord.max_consecutive_failures):
        await coord.async_refresh()
    assert coord.last_update_success is True

    # One past the allowance: now it's a real outage.
    await coord.async_refresh()
    assert coord.last_update_success is False


async def test_live_failure_counter_resets_on_success(hass, indoor_payload):
    """A good poll must clear the tally, not leave it primed to trip."""
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)
    await coord.async_refresh()

    client.get_reading.side_effect = PurpleAirTimeoutError("blocked")
    for _ in range(coord.max_consecutive_failures):
        await coord.async_refresh()

    client.get_reading.side_effect = None
    await coord.async_refresh()
    assert coord.last_update_success is True

    # A fresh run of failures should get the full allowance again.
    client.get_reading.side_effect = PurpleAirTimeoutError("blocked again")
    for i in range(coord.max_consecutive_failures):
        await coord.async_refresh()
        assert coord.last_update_success is True, f"flapped on failure {i + 1}"


async def test_live_invalid_response_is_not_ridden_out(hass, indoor_payload):
    """Only transient errors get the grace period; a bad body is persistent."""
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)
    await coord.async_refresh()

    client.get_reading.side_effect = PurpleAirInvalidResponseError("HTTP 500")
    await coord.async_refresh()
    assert coord.last_update_success is False


async def test_live_with_no_prior_reading_fails_immediately(hass):
    """Nothing to serve on the first poll, so the grace period can't apply."""
    client = _fake_client()
    client.get_reading.side_effect = PurpleAirTimeoutError("blocked")
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)

    await coord.async_refresh()
    assert coord.last_update_success is False
    assert coord.data is None
