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
from homeassistant.const import (
    CONF_HOST,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.helpers import entity_registry as er
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

    Averaged succeeds, live fails: setup still returns True, and the live
    coordinator is kept, seeded from the averaged reading, so its
    entities exist and recover on their own.
    """
    entry = _entry(hass, indoor_payload, options={CONF_LIVE_ENTITIES: True})

    with _patch_client(
        side_effect=[indoor_payload, PurpleAirTimeoutError("live timed out")]
    ):
        assert await _setup(hass, entry) is True

    runtime = hass.data[DOMAIN][entry.entry_id]
    assert runtime.averaged.last_update_success is True
    assert runtime.live is not None
    assert runtime.live.last_update_success is False


def _entity_ids(hass, entry, unique_id_suffix: str) -> list[str]:
    """Enabled entities of `entry` whose unique_id ends with the suffix."""
    registry = er.async_get(hass)
    return [
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id.endswith(unique_id_suffix) and e.disabled_by is None
    ]


async def test_live_entities_start_unavailable_and_recover(
    hass, indoor_payload
):
    """They used to be skipped until the next reload or restart."""
    entry = _entry(hass, indoor_payload, options={CONF_LIVE_ENTITIES: True})

    with _patch_client(
        side_effect=[
            indoor_payload,
            PurpleAirTimeoutError("live timed out"),
            indoor_payload,
        ]
    ):
        assert await _setup(hass, entry) is True
        live_ids = _entity_ids(hass, entry, "_live")
        assert live_ids
        assert {hass.states.get(e).state for e in live_ids} == {
            STATE_UNAVAILABLE
        }

        await hass.data[DOMAIN][entry.entry_id].live.async_refresh()
        await hass.async_block_till_done()

    states = {hass.states.get(e).state for e in live_ids}
    assert STATE_UNAVAILABLE not in states


async def test_online_reads_off_not_unavailable_when_a_poll_fails(
    hass, indoor_payload
):
    """`to: off` automations must fire; `unavailable` never matched them."""
    entry = _entry(hass, indoor_payload)

    with _patch_client(
        side_effect=[indoor_payload, PurpleAirTimeoutError("down")]
    ):
        assert await _setup(hass, entry) is True
        (online,) = _entity_ids(hass, entry, "_diag_online")
        assert hass.states.get(online).state == STATE_ON

        await hass.data[DOMAIN][entry.entry_id].averaged.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(online).state == STATE_OFF
