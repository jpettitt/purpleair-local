"""Reloads triggered by the config and options flows on a set-up entry.

There is no update listener: from HA 2026.12 one may not coexist with a
flow-scheduled reload, so every reload comes from HA itself, via the
user flow's re-add path or OptionsFlowWithReload. These tests set the
entry up for real and record each `async_setup_entry` call, so a double
reload, a reload with stale options, or a missing one shows up directly.
Only the client is faked, keyed by host.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.purpleair_local as integration
from custom_components.purpleair_local.api import PurpleAirTimeoutError
from custom_components.purpleair_local.const import (
    CONF_AQI_COLOR_SCHEME,
    CONF_AQI_CORRECTIONS,
    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
    CONF_LIVE_ENTITIES,
    CONF_LIVE_SCAN_INTERVAL_S,
    CONF_SCAN_INTERVAL_S,
    DEFAULT_AQI_COLOR_SCHEME,
    DEFAULT_AQI_CORRECTIONS,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    DEFAULT_LIVE_SCAN_INTERVAL_S,
    DEFAULT_SCAN_INTERVAL_S,
    DOMAIN,
)

_OLD = "192.168.0.42"
_NEW = "192.168.0.99"

# What the options form stores when every field is left at its default.
_OPTIONS: dict[str, Any] = {
    CONF_SCAN_INTERVAL_S: DEFAULT_SCAN_INTERVAL_S,
    CONF_LIVE_ENTITIES: False,
    CONF_LIVE_SCAN_INTERVAL_S: DEFAULT_LIVE_SCAN_INTERVAL_S,
    CONF_AQI_CORRECTIONS: list(DEFAULT_AQI_CORRECTIONS),
    CONF_AQI_COLOR_SCHEME: DEFAULT_AQI_COLOR_SCHEME,
    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: float(
        DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3
    ),
    CONF_CHANNEL_DISAGREEMENT_MIN_PCT: float(
        DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT
    ),
}


@pytest.fixture
def sensors(indoor_payload: dict) -> Iterator[dict[str, Any]]:
    """Host -> payload it serves, or the exception it raises.

    The same physical sensor answers at both addresses unless a test
    makes one fail.
    """
    reachable: dict[str, Any] = {_OLD: indoor_payload, _NEW: indoor_payload}

    def make_client(host: str, session: Any) -> AsyncMock:
        async def get_reading(*, live: bool = False) -> dict:
            outcome = reachable[host]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        client = AsyncMock()
        client.host = host
        client.get_reading.side_effect = get_reading
        return client

    with (
        patch(
            "custom_components.purpleair_local.PurpleAirClient",
            side_effect=make_client,
        ),
        patch(
            "custom_components.purpleair_local.config_flow.PurpleAirClient",
            side_effect=make_client,
        ),
    ):
        yield reachable


@pytest.fixture
def setups(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int | None]]:
    """(host, scan interval) as each async_setup_entry call saw them."""
    calls: list[tuple[str, int | None]] = []
    real_setup = integration.async_setup_entry

    async def recording_setup(hass: HomeAssistant, entry: Any) -> bool:
        calls.append(
            (entry.data[CONF_HOST], entry.options.get(CONF_SCAN_INTERVAL_S))
        )
        return await real_setup(hass, entry)

    monkeypatch.setattr(integration, "async_setup_entry", recording_setup)
    return calls


async def _set_up(
    hass: HomeAssistant, payload: dict, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: _OLD},
        options=options or {},
        unique_id=payload["SensorId"],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _readd(hass: HomeAssistant, host: str) -> FlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_HOST: host}
    )
    await hass.async_block_till_done()
    return result


async def _save_options(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    host: str,
    changes: dict[str, Any] | None = None,
) -> FlowResult:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_HOST: host, **_OPTIONS, **(changes or {})},
    )
    await hass.async_block_till_done()
    return result


# --- user flow: re-adding a known sensor at a new address -----------------


async def test_readding_a_loaded_sensor_at_a_new_ip_reloads_once(
    hass: HomeAssistant,
    sensors: dict[str, Any],
    setups: list[tuple[str, int | None]],
    indoor_payload: dict,
) -> None:
    """The update listener used to reload as well as HA: two setups."""
    entry = await _set_up(hass, indoor_payload)
    setups.clear()

    result = await _readd(hass, _NEW)

    assert result["reason"] == "already_configured"
    assert setups == [(_NEW, None)]
    assert entry.state is ConfigEntryState.LOADED


async def test_readding_a_sensor_stuck_in_setup_retry_reloads_it(
    hass: HomeAssistant,
    sensors: dict[str, Any],
    setups: list[tuple[str, int | None]],
    indoor_payload: dict,
) -> None:
    """The usual DHCP case: HA restarted and the old address is dead."""
    sensors[_OLD] = PurpleAirTimeoutError("timed out")
    entry = await _set_up(hass, indoor_payload)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    setups.clear()

    await _readd(hass, _NEW)

    assert setups == [(_NEW, None)]
    assert entry.state is ConfigEntryState.LOADED


# --- options flow ---------------------------------------------------------


async def test_options_change_reloads_once_with_the_new_options(
    hass: HomeAssistant,
    sensors: dict[str, Any],
    setups: list[tuple[str, int | None]],
    indoor_payload: dict,
) -> None:
    entry = await _set_up(hass, indoor_payload, _OPTIONS)
    setups.clear()

    await _save_options(hass, entry, _OLD, {CONF_SCAN_INTERVAL_S: 60})

    assert setups == [(_OLD, 60)]
    assert entry.state is ConfigEntryState.LOADED


async def test_options_host_and_option_change_reload_once(
    hass: HomeAssistant,
    sensors: dict[str, Any],
    setups: list[tuple[str, int | None]],
    indoor_payload: dict,
) -> None:
    """The listener reloaded on the host write, before the options were
    saved, then again after: once with stale options."""
    entry = await _set_up(hass, indoor_payload, _OPTIONS)
    setups.clear()

    await _save_options(hass, entry, _NEW, {CONF_SCAN_INTERVAL_S: 60})

    assert setups == [(_NEW, 60)]
    assert entry.state is ConfigEntryState.LOADED


async def test_options_host_only_change_reloads(
    hass: HomeAssistant,
    sensors: dict[str, Any],
    setups: list[tuple[str, int | None]],
    indoor_payload: dict,
) -> None:
    """Unchanged options don't trigger OptionsFlowWithReload's reload."""
    entry = await _set_up(hass, indoor_payload, _OPTIONS)
    setups.clear()

    await _save_options(hass, entry, _NEW)

    assert setups == [(_NEW, DEFAULT_SCAN_INTERVAL_S)]
    assert entry.state is ConfigEntryState.LOADED


async def test_options_host_change_reloads_an_entry_stuck_in_setup_retry(
    hass: HomeAssistant,
    sensors: dict[str, Any],
    setups: list[tuple[str, int | None]],
    indoor_payload: dict,
) -> None:
    """The listener only existed after a successful setup, so this fix
    used to wait for HA's next retry."""
    sensors[_OLD] = PurpleAirTimeoutError("timed out")
    entry = await _set_up(hass, indoor_payload, _OPTIONS)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    setups.clear()

    await _save_options(hass, entry, _NEW)

    assert setups == [(_NEW, DEFAULT_SCAN_INTERVAL_S)]
    assert entry.state is ConfigEntryState.LOADED


async def test_options_save_with_nothing_changed_does_not_reload(
    hass: HomeAssistant,
    sensors: dict[str, Any],
    setups: list[tuple[str, int | None]],
    indoor_payload: dict,
) -> None:
    entry = await _set_up(hass, indoor_payload, _OPTIONS)
    setups.clear()

    result = await _save_options(hass, entry, _OLD)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setups == []
