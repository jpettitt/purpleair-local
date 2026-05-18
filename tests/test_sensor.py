"""Tests for sensor.py entity construction and value resolution.

Strategy: build a coordinator with `data` populated from one of the
redacted fixtures (no network), call `build_entities`, then inspect
the resulting list. State queries go straight through `native_value`
on each entity, so this exercises the per-channel value math without
involving HA's full entity-platform machinery.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.purpleair_local.aqi import (
    aqi_aqandu,
    aqi_epa,
    aqi_lrapa,
    aqi_raw,
    correct_epa,
    pm25_to_aqi,
)
from custom_components.purpleair_local.const import (
    AQI_CORRECTION_AQANDU,
    AQI_CORRECTION_EPA,
    AQI_CORRECTION_LRAPA,
    AQI_CORRECTION_RAW,
    CONF_AQI_CORRECTIONS,
    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
)
from custom_components.purpleair_local.models import SensorReading
from custom_components.purpleair_local.sensor import build_entities


def _coordinator(payload: dict, host: str = "192.168.0.1"):
    """Build a stub coordinator with .data populated, no real HA needed."""
    reading = SensorReading.from_payload(payload)
    coord = MagicMock()
    coord.data = reading
    coord.client = MagicMock()
    coord.client.host = host
    # CoordinatorEntity reaches into these in __init__ for listener setup;
    # MagicMock auto-handles them but make the read of `data` stable.
    return coord


def _by_unique_id(entities) -> dict[str, Any]:
    return {e.unique_id: e for e in entities}


# --- single-laser indoor: no _b entities, no disagreement ----------------


def test_single_laser_has_only_primary_pm_and_aqi(indoor_payload):
    coord = _coordinator(indoor_payload)
    entities = build_entities(coord, options={})
    sid = indoor_payload["SensorId"]

    # 3 PM × 1 channel (primary) + 2 AQI × 1 channel (raw+EPA defaults)
    # + 6 particle bins + 4 env + 5 diag = 20
    assert len(entities) == 20

    keys = {e.unique_id for e in entities}
    # No _a / _b channel-suffixed entities for single-laser
    for k in keys:
        assert "_a_" not in k, k
        assert "_b_" not in k, k

    # The three PM mass entities are present, primary-keyed
    for field in ("pm1_0_atm", "pm2_5_atm", "pm10_0_atm"):
        assert f"{sid}_primary_{field}" in keys


def test_single_laser_pm25_value_is_channel_a(indoor_payload):
    coord = _coordinator(indoor_payload)
    [pm25] = [
        e
        for e in build_entities(coord, options={})
        if e.unique_id.endswith("_primary_pm2_5_atm")
    ]
    # Indoor fixture: channel A pm2_5_atm = 0.0; no B; primary = A.
    assert pm25.native_value == 0.0


# --- dual-laser outdoor: A + B + primary entities -------------------------


def test_dual_laser_creates_three_channel_pm_entities(outdoor_payload):
    coord = _coordinator(outdoor_payload)
    entities = build_entities(coord, options={})
    sid = outdoor_payload["SensorId"]

    # 3 PM × 3 channels (primary+a+b) + 2 AQI × 3 channels (raw+EPA)
    # + 6 particle + 4 env + 5 diag = 9 + 6 + 6 + 4 + 5 = 30
    assert len(entities) == 30

    keys = {e.unique_id for e in entities}
    for channel in ("primary", "a", "b"):
        assert f"{sid}_{channel}_pm2_5_atm" in keys
        assert f"{sid}_{channel}_aqi_raw" in keys
        assert f"{sid}_{channel}_aqi_epa" in keys


def test_dual_laser_primary_pm25_is_average_of_a_and_b():
    """Custom payload with distinct A and B values exercises the averaging."""
    payload = _dual_payload(pm25_a=10.0, pm25_b=20.0)
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]
    assert by_id[f"{sid}_a_pm2_5_atm"].native_value == 10.0
    assert by_id[f"{sid}_b_pm2_5_atm"].native_value == 20.0
    assert by_id[f"{sid}_primary_pm2_5_atm"].native_value == 15.0


# --- AQI: enabled set, raw vs corrections, primary derivation -------------


def test_aqi_default_corrections_are_raw_and_epa(indoor_payload):
    coord = _coordinator(indoor_payload)
    aqi_keys = {
        e.unique_id.rsplit("_", 1)[-1]
        for e in build_entities(coord, options={})
        if "_aqi_" in e.unique_id
    }
    assert aqi_keys == {"raw", "epa"}


def test_aqi_all_corrections_enabled_creates_four_per_channel(
    outdoor_payload,
):
    options = {
        CONF_AQI_CORRECTIONS: [
            AQI_CORRECTION_RAW,
            AQI_CORRECTION_EPA,
            AQI_CORRECTION_AQANDU,
            AQI_CORRECTION_LRAPA,
        ]
    }
    entities = build_entities(_coordinator(outdoor_payload), options=options)
    aqi_entities = [e for e in entities if "_aqi_" in e.unique_id]
    # 4 corrections × 3 channels = 12
    assert len(aqi_entities) == 12


def test_aqi_empty_corrections_creates_no_aqi_entities(indoor_payload):
    entities = build_entities(
        _coordinator(indoor_payload),
        options={CONF_AQI_CORRECTIONS: []},
    )
    assert not any("_aqi_" in e.unique_id for e in entities)


def test_aqi_raw_is_consistent_between_channel_and_primary():
    """All "raw" AQI entities must use the same breakpoint table.

    Before the fix, per-channel raw passed through the on-device
    `pm2.5_aqi` (pre-2024 EPA table from firmware) while primary raw
    used our 2024 table, so identical A/B readings could produce
    different AQI values across the channel-A, channel-B, and primary
    entities. Now they all use `aqi_raw()` on the ATM density.
    """
    payload = _dual_payload(
        pm25_a=10.0, pm25_b=20.0, aqi_a=999, aqi_b=999
    )
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]

    # On-device AQI (999) is intentionally bogus to prove we don't pass
    # it through. All three raw entities should reflect aqi_raw() of
    # their channel's ATM density.
    assert by_id[f"{sid}_a_aqi_raw"].native_value == pm25_to_aqi(10.0)
    assert by_id[f"{sid}_b_aqi_raw"].native_value == pm25_to_aqi(20.0)
    # Primary is the A/B average: (10+20)/2 = 15
    assert by_id[f"{sid}_primary_aqi_raw"].native_value == pm25_to_aqi(15.0)


def test_aqi_epa_uses_cf1_and_humidity():
    """Primary EPA AQI matches the published Barkjohn formula for cf1 avg + RH."""
    payload = _dual_payload(pm25_a=10.0, pm25_b=20.0, rh=50.0)
    # cf1 = atm * 1.5 in our synthetic payload, so:
    # A cf1 = 15.0, B cf1 = 30.0, primary cf1 = average = 22.5.
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]
    expected_corrected = correct_epa(22.5, 50.0)
    assert (
        by_id[f"{sid}_primary_aqi_epa"].native_value
        == pm25_to_aqi(expected_corrected)
    )


def test_aqi_epa_without_environment_is_none():
    """EPA correction needs RH; no BME → no humidity → entity reports None."""
    payload = _dual_payload(pm25_a=10.0, pm25_b=20.0, include_bme=False)
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]
    # No env entities created at all
    assert not any(e.unique_id.startswith(f"{sid}_env_") for e in by_id.values())
    # But EPA AQI is created with no value
    assert by_id[f"{sid}_primary_aqi_epa"].native_value is None


# --- particle counts ------------------------------------------------------


def test_particle_counts_are_primary_only_and_disabled(outdoor_payload):
    coord = _coordinator(outdoor_payload)
    entities = build_entities(coord, options={})
    counts = [
        e for e in entities if "_primary_count_" in e.unique_id
    ]
    assert len(counts) == 6
    assert all(e.entity_registry_enabled_default is False for e in counts)


def test_particle_count_primary_averages_a_and_b(outdoor_payload):
    coord = _coordinator(outdoor_payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = outdoor_payload["SensorId"]
    # Outdoor fixture: A p_0_3_um = 50.89, B p_0_3_um = 49.09 → 49.99
    val = by_id[f"{sid}_primary_count_p_0_3_um"].native_value
    assert val == pytest.approx(49.99, abs=0.01)


# --- environment: conditional creation ------------------------------------


def test_environment_entities_skipped_when_no_bme():
    payload = _dual_payload(pm25_a=0.0, pm25_b=0.0, include_bme=False)
    entities = build_entities(_coordinator(payload), options={})
    assert not any("_env_" in e.unique_id for e in entities)


def test_environment_temp_humidity_pressure_present_for_bme280(indoor_payload):
    entities = build_entities(_coordinator(indoor_payload), options={})
    env_keys = {
        e.unique_id.rsplit("_", 1)[-1]
        for e in entities
        if "_env_" in e.unique_id
    }
    assert env_keys == {"temperature", "humidity", "dewpoint", "pressure"}


def test_temperature_entity_value_matches_reading(indoor_payload):
    coord = _coordinator(indoor_payload)
    [temp] = [
        e
        for e in build_entities(coord, options={})
        if e.unique_id.endswith("_env_temperature")
    ]
    assert temp.native_value == 86.0  # from the indoor fixture


# --- diagnostics ----------------------------------------------------------


def test_diagnostics_always_created(indoor_payload):
    entities = build_entities(_coordinator(indoor_payload), options={})
    diag_keys = {
        e.unique_id.rsplit("_diag_", 1)[-1]
        for e in entities
        if "_diag_" in e.unique_id
    }
    assert diag_keys == {
        "rssi",
        "uptime",
        "free_heap",
        "firmware",
        "last_seen",
    }


def test_rssi_value_matches_reading(indoor_payload):
    coord = _coordinator(indoor_payload)
    [rssi] = [
        e
        for e in build_entities(coord, options={})
        if e.unique_id.endswith("_diag_rssi")
    ]
    assert rssi.native_value == -42  # from the indoor fixture


# --- device info ----------------------------------------------------------


# --- primary fallback when channels disagree ----------------------------


def test_primary_pm25_picks_lower_when_channels_disagree():
    """Stuck-high failure mode: pick the lower (likely-healthy) channel."""
    # 5 vs 50 µg/m³: |diff|=45 (≥5) AND rel=90% (≥70) → disagreement
    payload = _dual_payload(pm25_a=5.0, pm25_b=50.0)
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]
    # A and B still report their own values
    assert by_id[f"{sid}_a_pm2_5_atm"].native_value == 5.0
    assert by_id[f"{sid}_b_pm2_5_atm"].native_value == 50.0
    # Primary picks min, NOT (5+50)/2 = 27.5
    assert by_id[f"{sid}_primary_pm2_5_atm"].native_value == 5.0


def test_primary_pm25_averages_when_channels_agree():
    """Sanity: backwards-compatible path still works when agreement holds."""
    payload = _dual_payload(pm25_a=10.0, pm25_b=11.0)  # diff 1, well under
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]
    assert by_id[f"{sid}_primary_pm2_5_atm"].native_value == 10.5


def test_primary_aqi_raw_uses_fallback_when_channels_disagree():
    """AQI flows through _channel_atm, so it inherits the fallback."""
    from custom_components.purpleair_local.aqi import pm25_to_aqi

    payload = _dual_payload(pm25_a=5.0, pm25_b=50.0)
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]
    # Primary raw AQI is computed from min (5.0), NOT average (27.5)
    assert (
        by_id[f"{sid}_primary_aqi_raw"].native_value == pm25_to_aqi(5.0)
    )
    assert by_id[f"{sid}_a_aqi_raw"].native_value == pm25_to_aqi(5.0)
    assert by_id[f"{sid}_b_aqi_raw"].native_value == pm25_to_aqi(50.0)


def test_primary_particle_count_uses_fallback_when_channels_disagree():
    """Disagreement on PM2.5 ATM also flips particle counts to min."""
    # Make PM2.5 disagree but give the counts distinct values to compare.
    payload = _dual_payload(pm25_a=5.0, pm25_b=50.0)
    payload["p_0_3_um"] = 100.0
    payload["p_0_3_um_b"] = 999.0
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]
    assert (
        by_id[f"{sid}_primary_count_p_0_3_um"].native_value == 100.0
    )  # min, NOT average (549.5)


def test_primary_fallback_uses_option_thresholds():
    """User-tightened thresholds shift which readings trigger the fallback."""
    # diff 5, rel 33% — below default 70% but above a 30% custom threshold
    payload = _dual_payload(pm25_a=10.0, pm25_b=15.0)
    options = {
        CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: 3.0,
        CONF_CHANNEL_DISAGREEMENT_MIN_PCT: 30.0,
    }
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options=options))
    sid = payload["SensorId"]
    # With the user's tighter rule, these channels disagree → min wins
    assert by_id[f"{sid}_primary_pm2_5_atm"].native_value == 10.0


def test_primary_falls_back_only_for_primary_not_individual_channels():
    """Channel A and B entities are unaffected by disagreement."""
    payload = _dual_payload(pm25_a=5.0, pm25_b=50.0)
    coord = _coordinator(payload)
    by_id = _by_unique_id(build_entities(coord, options={}))
    sid = payload["SensorId"]
    assert by_id[f"{sid}_a_pm2_5_atm"].native_value == 5.0
    assert by_id[f"{sid}_b_pm2_5_atm"].native_value == 50.0


def test_single_laser_unaffected_by_thresholds():
    """Single-laser sensors have no B to disagree with; thresholds inert."""
    payload = indoor_payload_dict()  # define below
    coord = _coordinator(payload)
    options = {
        CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: 0.0,
        CONF_CHANNEL_DISAGREEMENT_MIN_PCT: 0.0,
    }
    by_id = _by_unique_id(build_entities(coord, options=options))
    sid = payload["SensorId"]
    # Primary equals channel A's value; even with absurd thresholds nothing breaks
    assert by_id[f"{sid}_primary_pm2_5_atm"].native_value == 7.5


def indoor_payload_dict() -> dict:
    """Minimal single-laser payload with a non-zero pm2_5 for the assertion."""
    return {
        "SensorId": "aa:bb:cc:dd:ee:01",
        "hardwareversion": "2.0",
        "hardwarediscovered": "2.0+BME280+PMSX003-A",
        "version": "7.02",
        "place": "inside",
        "pm1_0_atm": 0.0,
        "pm2_5_atm": 7.5,
        "pm10_0_atm": 0.0,
        "pm1_0_cf_1": 0.0,
        "pm2_5_cf_1": 7.5,
        "pm10_0_cf_1": 0.0,
        "p_0_3_um": 0.0,
        "p_0_5_um": 0.0,
        "p_1_0_um": 0.0,
        "p_2_5_um": 0.0,
        "p_5_0_um": 0.0,
        "p_10_0_um": 0.0,
    }


def test_device_info_includes_mac_connection_and_configuration_url(
    indoor_payload,
):
    coord = _coordinator(indoor_payload, host="10.0.0.5")
    entity = build_entities(coord, options={})[0]
    info = entity.device_info
    assert info["manufacturer"] == "PurpleAir"
    assert info["configuration_url"] == "http://10.0.0.5/"
    # MAC-based connection lets HA dedupe across integrations that
    # describe the same physical device.
    assert ("mac", indoor_payload["SensorId"]) in info["connections"]


# --- payload synthesis helpers --------------------------------------------


def _dual_payload(
    *,
    pm25_a: float,
    pm25_b: float,
    aqi_a: int | None = None,
    aqi_b: int | None = None,
    rh: float = 30.0,
    include_bme: bool = True,
) -> dict:
    """Build a minimal dual-laser payload with controllable A/B values.

    cf1 is set to 1.5 × atm to give the AQI correction tests a
    distinguishable input from atm. Particle bins and PM1/PM10 fields
    are filled with zeros to keep the parser happy.
    """
    payload: dict = {
        "SensorId": "11:22:33:44:55:66",
        "hardwareversion": "2.0",
        "hardwarediscovered": "2.0+BME280+PMSX003-B+PMSX003-A",
        "version": "7.02",
        "place": "outside",
        # channel A
        "pm1_0_atm": 0.0,
        "pm2_5_atm": pm25_a,
        "pm10_0_atm": 0.0,
        "pm1_0_cf_1": 0.0,
        "pm2_5_cf_1": pm25_a * 1.5,
        "pm10_0_cf_1": 0.0,
        "p_0_3_um": 0.0,
        "p_0_5_um": 0.0,
        "p_1_0_um": 0.0,
        "p_2_5_um": 0.0,
        "p_5_0_um": 0.0,
        "p_10_0_um": 0.0,
        # channel B
        "pm1_0_atm_b": 0.0,
        "pm2_5_atm_b": pm25_b,
        "pm10_0_atm_b": 0.0,
        "pm1_0_cf_1_b": 0.0,
        "pm2_5_cf_1_b": pm25_b * 1.5,
        "pm10_0_cf_1_b": 0.0,
        "p_0_3_um_b": 0.0,
        "p_0_5_um_b": 0.0,
        "p_1_0_um_b": 0.0,
        "p_2_5_um_b": 0.0,
        "p_5_0_um_b": 0.0,
        "p_10_0_um_b": 0.0,
    }
    if aqi_a is not None:
        payload["pm2.5_aqi"] = aqi_a
    if aqi_b is not None:
        payload["pm2.5_aqi_b"] = aqi_b
    if include_bme:
        payload["current_temp_f"] = 72.0
        payload["current_humidity"] = rh
        payload["current_dewpoint_f"] = 50.0
        payload["pressure"] = 1013.0
    return payload
