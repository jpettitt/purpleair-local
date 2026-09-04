"""Tests for async_setup_entry's coordinator wiring.

The options flow can save `live_entities` perfectly and the entities can
build perfectly, and the feature still does nothing if setup never reads
the option — so these tests assert the option actually reaches a second
coordinator on the live endpoint.

The client is patched at the symbol `__init__.py` imports; platform
forwarding is stubbed because entity construction has its own coverage
in test_sensor.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.purpleair_local.api import PurpleAirTimeoutError
from custom_components.purpleair_local.const import (
    CONF_LIVE_ENTITIES,
    CONF_LIVE_SCAN_INTERVAL_S,
    CONF_SCAN_INTERVAL_S,
    DEFAULT_LIVE_SCAN_INTERVAL_S,
    DOMAIN,
)

_HOST = "192.168.0.42"


def _entry(hass, payload, *, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: _HOST},
        options=options or {},
        unique_id=payload["SensorId"],
    )
    entry.add_to_hass(hass)
    return entry


def _patch_client(*, side_effect=None, payload=None):
    """Patch PurpleAirClient so every instance shares one mock.

    Setup builds a second client for the live coordinator, so a
    `side_effect` list spans both: index 0 is the averaged first
    refresh, index 1 the live one.
    """
    client = AsyncMock()
    client.host = _HOST
    if side_effect is not None:
        client.get_reading.side_effect = side_effect
    else:
        client.get_reading.return_value = payload
    return patch(
        "custom_components.purpleair_local.PurpleAirClient",
        return_value=client,
    )


async def _setup(hass, entry) -> bool:
    """Run setup through HA rather than calling the coroutine directly.

    `async_config_entry_first_refresh` refuses to run unless the entry
    is in SETUP_IN_PROGRESS, which only the real config-entries manager
    arranges. Going through it also exercises platform forwarding for
    free.
    """
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.state is ConfigEntryState.LOADED


async def test_setup_without_live_option_builds_only_averaged(
    hass, indoor_payload
):
    entry = _entry(hass, indoor_payload)

    with _patch_client(payload=indoor_payload):
        assert await _setup(hass, entry) is True

    runtime = hass.data[DOMAIN][entry.entry_id]
    assert runtime.averaged.live is False
    assert runtime.live is None


async def test_setup_with_live_option_builds_both_coordinators(
    hass, indoor_payload
):
    entry = _entry(
        hass,
        indoor_payload,
        options={
            CONF_SCAN_INTERVAL_S: 120,
            CONF_LIVE_ENTITIES: True,
            CONF_LIVE_SCAN_INTERVAL_S: 20,
        },
    )

    with _patch_client(payload=indoor_payload):
        assert await _setup(hass, entry) is True

    runtime = hass.data[DOMAIN][entry.entry_id]
    assert runtime.averaged.live is False
    assert runtime.averaged.update_interval.total_seconds() == 120
    assert runtime.live is not None
    assert runtime.live.live is True
    assert runtime.live.update_interval.total_seconds() == 20


async def test_live_coordinator_uses_default_interval_when_unset(
    hass, indoor_payload
):
    entry = _entry(hass, indoor_payload, options={CONF_LIVE_ENTITIES: True})

    with _patch_client(payload=indoor_payload):
        assert await _setup(hass, entry) is True

    runtime = hass.data[DOMAIN][entry.entry_id]
    assert (
        runtime.live.update_interval.total_seconds()
        == DEFAULT_LIVE_SCAN_INTERVAL_S
    )


async def test_live_poll_failure_does_not_block_setup(hass, indoor_payload):
    """A dead live endpoint must not take the averaged entities down.

    Averaged succeeds, live fails: setup still returns True and the
    entry loads. Live is left None because entity construction reads
    `coordinator.data`, so there'd be nothing to build from.
    """
    entry = _entry(hass, indoor_payload, options={CONF_LIVE_ENTITIES: True})

    with _patch_client(
        side_effect=[indoor_payload, PurpleAirTimeoutError("live timed out")]
    ):
        assert await _setup(hass, entry) is True

    runtime = hass.data[DOMAIN][entry.entry_id]
    assert runtime.averaged.last_update_success is True
    assert runtime.live is None
