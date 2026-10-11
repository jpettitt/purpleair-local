"""Tests for PurpleAirCoordinator.

The client is mocked because we're not exercising the HTTP layer here;
api.py has its own tests for that. We do use a real HomeAssistant
instance via the `hass` fixture (from pytest_homeassistant_custom_component)
so the coordinator's scheduling, listener notification, and
UpdateFailed → last_update_success bookkeeping run for real.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.purpleair_local.api import (
    PurpleAirConnectionError,
    PurpleAirInvalidResponseError,
    PurpleAirTimeoutError,
)
from custom_components.purpleair_local.const import DOMAIN
from custom_components.purpleair_local.coordinator import PurpleAirCoordinator
from custom_components.purpleair_local.models import SensorReading

# Any fixed instant; the grace-period tests move the clock relative to it.
_START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def clock(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """HA's clock, frozen at _START."""
    freezer.move_to(_START)
    return freezer


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


async def test_averaged_coordinator_fails_fast(hass, indoor_payload):
    """The canonical series gets no grace period — it backs `online`."""
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=120)
    await coord.async_refresh()

    client.get_reading.side_effect = PurpleAirTimeoutError("blocked")
    await coord.async_refresh()
    assert coord.last_update_success is False


async def test_live_rides_out_transient_failures_serving_last_reading(
    hass, clock, indoor_payload
):
    """Entities must not flap to unavailable during the sensor's block window."""
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)
    await coord.async_refresh()

    client.get_reading.side_effect = PurpleAirTimeoutError("blocked")
    for elapsed in (25, 50, 60):
        clock.move_to(_START + timedelta(seconds=elapsed))
        await coord.async_refresh()
        assert coord.last_update_success is True, f"flapped at {elapsed} s"
        assert coord.data.sensor_id == indoor_payload["SensorId"]


async def test_live_gives_up_once_the_grace_period_has_passed(
    hass, clock, indoor_payload
):
    """Timed from the last real reading, not counted in failures.

    A single failure 61 s after the last reading is already an outage:
    the old count-based allowance (5 failures at 15 s) let the stale
    reading run for ~150 s, because each timed-out poll also spends the
    request timeout.
    """
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)
    await coord.async_refresh()

    client.get_reading.side_effect = PurpleAirTimeoutError("still blocked")
    clock.move_to(_START + timedelta(seconds=61))
    await coord.async_refresh()
    assert coord.last_update_success is False


async def test_live_grace_period_restarts_after_a_real_reading(
    hass, clock, indoor_payload
):
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)
    await coord.async_refresh()

    clock.move_to(_START + timedelta(seconds=50))
    await coord.async_refresh()  # a real reading at 50 s

    client.get_reading.side_effect = PurpleAirTimeoutError("blocked")
    clock.move_to(_START + timedelta(seconds=100))
    await coord.async_refresh()
    assert coord.last_update_success is True


@pytest.mark.parametrize(
    "persistent_failure",
    [
        PurpleAirInvalidResponseError("HTTP 500"),
        {"no": "SensorId"},  # malformed payload
    ],
)
async def test_live_never_serves_a_reading_from_before_a_persistent_failure(
    hass, clock, indoor_payload, persistent_failure
):
    """A timeout after a failed update must not bring the old reading back.

    The grace period used to check only a failure count, which a bad
    payload never incremented, so one timeout after any run of them
    served a reading of unbounded age.
    """
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, scan_interval_s=15, live=True)
    await coord.async_refresh()

    client.get_reading.side_effect = [
        persistent_failure,
        PurpleAirTimeoutError("blocked"),
    ]
    clock.move_to(_START + timedelta(seconds=15))
    await coord.async_refresh()
    clock.move_to(_START + timedelta(seconds=30))
    await coord.async_refresh()
    assert coord.last_update_success is False


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


# --- the sensor at the address must be the entry's sensor -----------------


async def test_sensor_id_mismatch_fails_the_update(
    hass, indoor_payload, outdoor_payload
):
    """DHCP handed this entry's address to another PurpleAir.

    Serving its data would silently put the other sensor's readings
    under this entry, so the update fails and the last good reading
    and raw payload are left alone.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "10.0.0.42"},
        unique_id=indoor_payload["SensorId"],
    )
    client = _fake_client()
    client.get_reading.return_value = indoor_payload
    coord = PurpleAirCoordinator(hass, client, config_entry=entry)
    await coord.async_refresh()
    assert coord.last_update_success is True

    client.get_reading.return_value = outdoor_payload
    await coord.async_refresh()

    assert coord.last_update_success is False
    assert coord.data.sensor_id == indoor_payload["SensorId"]
    assert coord.last_raw_payload is indoor_payload
