"""Tests for binary_sensor.py.

These use a stub coordinator (no real HA) for the value math and a
real `hass` fixture only for the platform-setup test that exercises
the per-entry entity creation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.purpleair_local.binary_sensor import (
    _ChannelDisagreementBinarySensor,
    _OnlineBinarySensor,
)
from custom_components.purpleair_local.const import (
    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    DOMAIN,
)
from custom_components.purpleair_local.models import SensorReading


def _coordinator(payload: dict, *, last_update_success: bool = True):
    reading = SensorReading.from_payload(payload)
    coord = MagicMock()
    coord.data = reading
    coord.client = MagicMock(host="10.0.0.1")
    coord.last_update_success = last_update_success
    return coord


def _dual_payload(pm25_a: float, pm25_b: float) -> dict:
    return {
        "SensorId": "11:22:33:44:55:66",
        "hardwareversion": "2.0",
        "hardwarediscovered": "2.0+BME280+PMSX003-B+PMSX003-A",
        "version": "7.02",
        "place": "outside",
        "pm1_0_atm": 0.0,
        "pm2_5_atm": pm25_a,
        "pm10_0_atm": 0.0,
        "pm1_0_cf_1": 0.0,
        "pm2_5_cf_1": pm25_a,
        "pm10_0_cf_1": 0.0,
        "pm1_0_atm_b": 0.0,
        "pm2_5_atm_b": pm25_b,
        "pm10_0_atm_b": 0.0,
        "pm1_0_cf_1_b": 0.0,
        "pm2_5_cf_1_b": pm25_b,
        "pm10_0_cf_1_b": 0.0,
    }


# --- Online sensor --------------------------------------------------------


def test_online_is_true_when_last_update_succeeded(indoor_payload):
    coord = _coordinator(indoor_payload, last_update_success=True)
    assert _OnlineBinarySensor(coord).is_on is True


def test_online_is_false_when_last_update_failed(indoor_payload):
    coord = _coordinator(indoor_payload, last_update_success=False)
    assert _OnlineBinarySensor(coord).is_on is False


# --- Channel-disagreement value math --------------------------------------


@pytest.mark.parametrize(
    "a,b,expected",
    [
        # Both thresholds (5 µg/m³ and 70%) must be crossed.
        (0.0, 0.0, False),  # identical
        (10.0, 10.0, False),
        (10.0, 11.0, False),  # diff 1, rel 9%, neither
        (10.0, 15.0, False),  # diff 5, rel 33%, only abs
        (5.0, 50.0, True),  # diff 45, rel 90%, both
        (1.0, 4.0, False),  # diff 3, rel 75%, only rel
        (10.0, 100.0, True),  # diff 90, rel 90%, both
        # Exactly at thresholds — inclusive.
        (5.0, 16.7, True),  # diff 11.7, max 16.7, rel ~70.1%, ≥ both
    ],
)
def test_channel_disagreement_threshold_logic(a, b, expected):
    coord = _coordinator(_dual_payload(a, b))
    sensor = _ChannelDisagreementBinarySensor(
        coord,
        min_diff_ugm3=DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
        min_pct=DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    )
    assert sensor.is_on is expected


def test_channel_disagreement_uses_custom_thresholds():
    """Tighter user-set thresholds flip what would otherwise be False."""
    coord = _coordinator(_dual_payload(10.0, 15.0))  # diff 5, rel 33%
    sensor = _ChannelDisagreementBinarySensor(
        coord, min_diff_ugm3=3.0, min_pct=30.0
    )
    assert sensor.is_on is True


def test_channel_disagreement_missing_field_is_none():
    """If a channel's PM2.5 is missing, the sensor reports unknown, not False.

    Reporting False would silently hide a degraded channel; None lets
    HA display 'unknown' and any automation can see the difference.
    """
    payload = _dual_payload(10.0, 20.0)
    payload.pop("pm2_5_atm_b")  # B's value disappears
    coord = _coordinator(payload)
    sensor = _ChannelDisagreementBinarySensor(
        coord,
        min_diff_ugm3=DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
        min_pct=DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    )
    assert sensor.is_on is None


# --- platform setup -------------------------------------------------------


async def test_single_laser_skips_disagreement_entity(
    hass, indoor_payload
):
    """Single-laser sensors only get the online binary sensor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "10.0.0.1"},
        unique_id=indoor_payload["SensorId"],
    )
    entry.add_to_hass(hass)
    coord = _coordinator(indoor_payload)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []

    from custom_components.purpleair_local.binary_sensor import (
        async_setup_entry,
    )

    await async_setup_entry(hass, entry, added.extend)

    assert len(added) == 1
    assert isinstance(added[0], _OnlineBinarySensor)


async def test_dual_laser_creates_both_entities(
    hass, outdoor_payload
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "10.0.0.1"},
        unique_id=outdoor_payload["SensorId"],
    )
    entry.add_to_hass(hass)
    coord = _coordinator(outdoor_payload)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []

    from custom_components.purpleair_local.binary_sensor import (
        async_setup_entry,
    )

    await async_setup_entry(hass, entry, added.extend)

    classes = {type(e) for e in added}
    assert classes == {_OnlineBinarySensor, _ChannelDisagreementBinarySensor}


async def test_dual_laser_uses_option_thresholds(
    hass, outdoor_payload
):
    """Threshold values stored in options must flow into the entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "10.0.0.1"},
        unique_id=outdoor_payload["SensorId"],
        options={
            CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: 2.5,
            CONF_CHANNEL_DISAGREEMENT_MIN_PCT: 50.0,
        },
    )
    entry.add_to_hass(hass)
    coord = _coordinator(outdoor_payload)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []

    from custom_components.purpleair_local.binary_sensor import (
        async_setup_entry,
    )

    await async_setup_entry(hass, entry, added.extend)

    [disagreement] = [
        e for e in added if isinstance(e, _ChannelDisagreementBinarySensor)
    ]
    assert disagreement._min_diff_ugm3 == 2.5
    assert disagreement._min_pct == 50.0
