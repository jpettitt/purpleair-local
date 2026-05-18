"""Tests for the PurpleAir Local options flow.

Each test starts by creating a fully-formed ConfigEntry (host + unique
SensorId) and then opens the options flow against it. PurpleAirClient
is patched at the symbol the flow imports for any test that exercises
the host-change probe.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.purpleair_local.api import (
    PurpleAirConnectionError,
    PurpleAirInvalidResponseError,
    PurpleAirTimeoutError,
)
from custom_components.purpleair_local.const import (
    AQI_CORRECTION_AQANDU,
    AQI_CORRECTION_EPA,
    AQI_CORRECTION_LRAPA,
    AQI_CORRECTION_RAW,
    CONF_AQI_CORRECTIONS,
    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
    CONF_SCAN_INTERVAL_S,
    DEFAULT_AQI_CORRECTIONS,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    DEFAULT_SCAN_INTERVAL_S,
    DOMAIN,
)


_HOST = "192.168.1.42"
_OTHER_SENSOR_PAYLOAD = {
    "SensorId": "ff:ff:ff:ff:ff:ff",
    "place": "outside",
    "hardwarediscovered": "2.0+BME280+PMSX003-A",
    "version": "7.02",
    "pm2_5_atm": 0.0,
    "pm2_5_cf_1": 0.0,
    "pm2.5_aqi": 0,
}


def _patch_client(*, payload: dict | None = None, side_effect: Any = None):
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


def _entry(hass, indoor_payload, *, options: dict | None = None) -> MockConfigEntry:
    """Create and register a config entry that matches the indoor fixture."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Indoor e7fc",
        data={CONF_HOST: _HOST},
        unique_id=indoor_payload["SensorId"],
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


async def _start_options_flow(hass, entry: MockConfigEntry) -> dict:
    return await hass.config_entries.options.async_init(entry.entry_id)


# --- form rendering -------------------------------------------------------


async def test_options_form_uses_defaults_when_no_prior_options(
    hass, indoor_payload
):
    entry = _entry(hass, indoor_payload)

    result = await _start_options_flow(hass, entry)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    # Schema-default extraction: the form's schema carries the defaults
    # we'll fall back to when the user hasn't customized anything yet.
    schema_dict = {k.schema: k.default() for k in result["data_schema"].schema}
    assert schema_dict[CONF_HOST] == _HOST
    assert schema_dict[CONF_SCAN_INTERVAL_S] == DEFAULT_SCAN_INTERVAL_S
    assert schema_dict[CONF_AQI_CORRECTIONS] == list(DEFAULT_AQI_CORRECTIONS)
    assert (
        schema_dict[CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3]
        == DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3
    )
    assert (
        schema_dict[CONF_CHANNEL_DISAGREEMENT_MIN_PCT]
        == DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT
    )


async def test_options_form_prefilled_from_existing_options(
    hass, indoor_payload
):
    existing = {
        CONF_SCAN_INTERVAL_S: 60,
        CONF_AQI_CORRECTIONS: [AQI_CORRECTION_EPA, AQI_CORRECTION_LRAPA],
        CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: 10.0,
        CONF_CHANNEL_DISAGREEMENT_MIN_PCT: 40.0,
    }
    entry = _entry(hass, indoor_payload, options=existing)

    result = await _start_options_flow(hass, entry)

    schema_dict = {k.schema: k.default() for k in result["data_schema"].schema}
    assert schema_dict[CONF_SCAN_INTERVAL_S] == 60
    assert schema_dict[CONF_AQI_CORRECTIONS] == [
        AQI_CORRECTION_EPA,
        AQI_CORRECTION_LRAPA,
    ]
    assert schema_dict[CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3] == 10.0
    assert schema_dict[CONF_CHANNEL_DISAGREEMENT_MIN_PCT] == 40.0


# --- save without host change --------------------------------------------


async def test_options_save_without_host_change_skips_probe(
    hass, indoor_payload
):
    entry = _entry(hass, indoor_payload)

    # No _patch_client wrapper — if the flow tries to network at all,
    # this test will fail with a "tried to use socket" error and we
    # know the host-unchanged path isn't probing as expected.
    result = await _start_options_flow(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: _HOST,
            CONF_SCAN_INTERVAL_S: 30,
            CONF_AQI_CORRECTIONS: [
                AQI_CORRECTION_RAW,
                AQI_CORRECTION_EPA,
                AQI_CORRECTION_AQANDU,
            ],
            CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: 7.5,
            CONF_CHANNEL_DISAGREEMENT_MIN_PCT: 60.0,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SCAN_INTERVAL_S: 30,
        CONF_AQI_CORRECTIONS: [
            AQI_CORRECTION_RAW,
            AQI_CORRECTION_EPA,
            AQI_CORRECTION_AQANDU,
        ],
        CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: 7.5,
        CONF_CHANNEL_DISAGREEMENT_MIN_PCT: 60.0,
    }
    # Host (which lives in data) is untouched.
    assert entry.data[CONF_HOST] == _HOST


# --- host change paths ----------------------------------------------------


async def test_options_host_change_with_matching_sensor_updates_data(
    hass, indoor_payload
):
    entry = _entry(hass, indoor_payload)
    new_host = "10.0.0.55"

    with _patch_client(payload=indoor_payload):
        result = await _start_options_flow(hass, entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: new_host,
                CONF_SCAN_INTERVAL_S: DEFAULT_SCAN_INTERVAL_S,
                CONF_AQI_CORRECTIONS: list(DEFAULT_AQI_CORRECTIONS),
                CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
                CONF_CHANNEL_DISAGREEMENT_MIN_PCT: DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_HOST] == new_host


async def test_options_host_change_with_mismatched_sensor_id_errors(
    hass, indoor_payload
):
    """Pointing at a different physical PurpleAir must be refused."""
    entry = _entry(hass, indoor_payload)

    with _patch_client(payload=_OTHER_SENSOR_PAYLOAD):
        result = await _start_options_flow(hass, entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "10.0.0.55",
                CONF_SCAN_INTERVAL_S: DEFAULT_SCAN_INTERVAL_S,
                CONF_AQI_CORRECTIONS: list(DEFAULT_AQI_CORRECTIONS),
                CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
                CONF_CHANNEL_DISAGREEMENT_MIN_PCT: DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "sensor_mismatch"}
    # The mismatch must NOT silently update the host on the entry.
    assert entry.data[CONF_HOST] == _HOST


@pytest.mark.parametrize(
    "err,error_key",
    [
        (PurpleAirConnectionError("refused"), "cannot_connect"),
        (PurpleAirTimeoutError("timeout"), "cannot_connect"),
        (PurpleAirInvalidResponseError("HTTP 500"), "invalid_response"),
    ],
)
async def test_options_host_change_probe_errors(
    hass, indoor_payload, err, error_key
):
    entry = _entry(hass, indoor_payload)

    with _patch_client(side_effect=err):
        result = await _start_options_flow(hass, entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "10.0.0.55",
                CONF_SCAN_INTERVAL_S: DEFAULT_SCAN_INTERVAL_S,
                CONF_AQI_CORRECTIONS: list(DEFAULT_AQI_CORRECTIONS),
                CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
                CONF_CHANNEL_DISAGREEMENT_MIN_PCT: DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error_key}
    assert entry.data[CONF_HOST] == _HOST


async def test_options_host_normalization_applied(hass, indoor_payload):
    """A pasted URL with scheme/slash should be stored as bare host."""
    entry = _entry(hass, indoor_payload)

    with _patch_client(payload=indoor_payload):
        result = await _start_options_flow(hass, entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "  http://10.0.0.55/  ",
                CONF_SCAN_INTERVAL_S: DEFAULT_SCAN_INTERVAL_S,
                CONF_AQI_CORRECTIONS: list(DEFAULT_AQI_CORRECTIONS),
                CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
                CONF_CHANNEL_DISAGREEMENT_MIN_PCT: DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_HOST] == "10.0.0.55"


# --- empty AQI selection allowed ------------------------------------------


async def test_options_empty_aqi_corrections_is_allowed(
    hass, indoor_payload
):
    """A user who wants no AQI entities at all can clear the list."""
    entry = _entry(hass, indoor_payload)

    result = await _start_options_flow(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: _HOST,
            CONF_SCAN_INTERVAL_S: DEFAULT_SCAN_INTERVAL_S,
            CONF_AQI_CORRECTIONS: [],
            CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
            CONF_CHANNEL_DISAGREEMENT_MIN_PCT: DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_AQI_CORRECTIONS] == []
