"""Tests for the SensorReading parser.

Two flavors of test:

  - "Fixture" tests parse the redacted real captures and assert the
    resulting structure matches what those sensors actually report.
  - "Synthetic" tests exercise corner cases (docs-style field spelling,
    missing subsystems, malformed values) using minimal payloads.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.purpleair_local.models import (
    ChannelReading,
    Diagnostics,
    Environment,
    ParticleCounts,
    Place,
    SensorReading,
    channels_disagree,
)


# --- fixture-driven tests -------------------------------------------------


def test_indoor_single_laser_fixture(indoor_payload):
    r = SensorReading.from_payload(indoor_payload)

    assert r.sensor_id == "00:00:00:00:00:01"
    assert r.firmware_version == "7.02"
    assert r.hardware_version == "2.0"
    assert r.hardware_discovered == "2.0+BME280+PMSX003-A"
    assert r.place is Place.INSIDE
    assert r.lat == 0.0
    assert r.lon == 0.0
    assert r.device_time == datetime(2026, 5, 17, 22, 23, 4, tzinfo=timezone.utc)

    assert r.is_dual_channel is False
    assert r.channel_b is None

    a = r.channel_a
    assert a.pm2_5_atm == 0.0
    assert a.pm2_5_cf_1 == 0.0
    # The dot-keyed firmware quirk must be picked up by _get_aqi.
    assert a.pm2_5_aqi == 0
    assert a.counts.p_0_3_um == 71.25
    assert a.counts.p_10_0_um == 0.0

    assert r.environment is not None
    assert r.environment.temp_f == 86.0
    assert r.environment.humidity_pct == 18.0
    assert r.environment.dewpoint_f == 38.0
    assert r.environment.pressure_mbar == 932.6
    # No BME680, so no VOC reading.
    assert r.environment.voc_resistance is None

    assert r.diagnostics.rssi_dbm == -42
    assert r.diagnostics.uptime_s == 272410
    assert r.diagnostics.free_heap_bytes == 17464
    assert r.diagnostics.wifi_state == "Connected"
    assert r.diagnostics.ssid == "REDACTED"


def test_outdoor_dual_laser_fixture(outdoor_payload):
    r = SensorReading.from_payload(outdoor_payload)

    assert r.sensor_id == "00:00:00:00:00:02"
    assert r.place is Place.OUTSIDE
    assert r.hardware_discovered == "2.0+BME280+PMSX003-B+PMSX003-A"

    assert r.is_dual_channel is True
    assert r.channel_b is not None

    a, b = r.channel_a, r.channel_b
    # In our redacted fixture both channels read all-zero; the structural
    # assertion is that both channels parsed and the b-side fields
    # populated from the `_b` keys, including the dotted AQI key.
    assert a.pm2_5_atm == 0.0
    assert b.pm2_5_atm == 0.0
    assert a.pm2_5_aqi == 0
    assert b.pm2_5_aqi == 0
    # Particle-count bins are different between channels in the capture,
    # which confirms the suffix routing.
    assert a.counts.p_0_3_um == 50.89
    assert b.counts.p_0_3_um == 49.09


# --- AQI field-name fallback ----------------------------------------------


def test_aqi_falls_back_to_underscore_spelling():
    """Future firmware (or the doc-spec spelling) uses pm2_5_aqi."""
    payload = _minimal_payload(
        {
            "pm2_5_atm": 7.5,
            "pm2_5_cf_1": 8.5,
            "pm2_5_aqi": 31,
        }
    )
    r = SensorReading.from_payload(payload)
    assert r.channel_a.pm2_5_aqi == 31


def test_aqi_prefers_dot_spelling_when_both_present():
    """The dot spelling is what real firmware sends; it wins."""
    payload = _minimal_payload(
        {
            "pm2_5_atm": 7.5,
            "pm2.5_aqi": 42,
            "pm2_5_aqi": 99,
        }
    )
    r = SensorReading.from_payload(payload)
    assert r.channel_a.pm2_5_aqi == 42


def test_aqi_missing_returns_none():
    payload = _minimal_payload({"pm2_5_atm": 1.0})
    r = SensorReading.from_payload(payload)
    assert r.channel_a.pm2_5_aqi is None


# --- Place normalization --------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("inside", Place.INSIDE),
        ("indoor", Place.INSIDE),
        ("INSIDE", Place.INSIDE),
        (" indoor ", Place.INSIDE),
        ("outside", Place.OUTSIDE),
        ("outdoor", Place.OUTSIDE),
        ("OUTSIDE", Place.OUTSIDE),
        ("unknown", Place.UNKNOWN),
        ("", Place.UNKNOWN),
        (None, Place.UNKNOWN),
        (42, Place.UNKNOWN),
    ],
)
def test_place_normalization(raw, expected):
    assert Place.from_raw(raw) is expected


# --- missing-subsystem handling -------------------------------------------


def test_no_bme_means_no_environment():
    payload = _minimal_payload({"pm2_5_atm": 0.0}, drop_env=True)
    r = SensorReading.from_payload(payload)
    assert r.environment is None


def test_bme680_fields_preferred_over_bme280():
    payload = _minimal_payload(
        {
            "pm2_5_atm": 0.0,
            "current_temp_f": 70.0,
            "current_temp_f_680": 71.5,
            "current_humidity": 40.0,
            "current_humidity_680": 41.5,
            "pressure": 1010.0,
            "pressure_680": 1011.5,
            "gas_680": 12345.0,
        },
        drop_env=True,
    )
    r = SensorReading.from_payload(payload)
    assert r.environment is not None
    assert r.environment.temp_f == 71.5
    assert r.environment.humidity_pct == 41.5
    assert r.environment.pressure_mbar == 1011.5
    assert r.environment.voc_resistance == 12345.0


def test_gas_680_nan_becomes_none():
    payload = _minimal_payload(
        {"pm2_5_atm": 0.0, "current_temp_f": 70.0, "gas_680": float("nan")},
        drop_env=True,
    )
    r = SensorReading.from_payload(payload)
    assert r.environment is not None
    assert r.environment.voc_resistance is None


def test_diagnostics_missing_fields_are_none():
    payload = _minimal_payload({"pm2_5_atm": 0.0}, drop_diag=True)
    r = SensorReading.from_payload(payload)
    assert r.diagnostics == Diagnostics(
        rssi_dbm=None,
        uptime_s=None,
        free_heap_bytes=None,
        wifi_state=None,
        ssid=None,
    )


# --- datetime parsing -----------------------------------------------------


def test_datetime_parses_purpleair_format():
    payload = _minimal_payload(
        {"pm2_5_atm": 0.0, "DateTime": "2026/05/17T22:23:04z"}
    )
    r = SensorReading.from_payload(payload)
    assert r.device_time == datetime(
        2026, 5, 17, 22, 23, 4, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    "bad",
    [
        "2026-05-17T22:23:04Z",  # ISO 8601, wrong format for this firmware
        "not a date",
        "",
        None,
        12345,
    ],
)
def test_datetime_invalid_or_missing_returns_none(bad):
    payload = _minimal_payload({"pm2_5_atm": 0.0, "DateTime": bad})
    r = SensorReading.from_payload(payload)
    assert r.device_time is None


# --- required-field enforcement -------------------------------------------


def test_missing_sensor_id_raises():
    payload = _minimal_payload({"pm2_5_atm": 0.0})
    payload.pop("SensorId")
    with pytest.raises(ValueError, match="SensorId"):
        SensorReading.from_payload(payload)


def test_blank_sensor_id_raises():
    payload = _minimal_payload({"pm2_5_atm": 0.0, "SensorId": ""})
    with pytest.raises(ValueError, match="SensorId"):
        SensorReading.from_payload(payload)


# --- numeric coercion -----------------------------------------------------


def test_booleans_are_not_silently_coerced_to_numbers():
    """A bool in a numeric field is broken telemetry, not 1.0/0.0."""
    payload = _minimal_payload(
        {"pm2_5_atm": True, "pm2_5_cf_1": False, "rssi": True}
    )
    r = SensorReading.from_payload(payload)
    assert r.channel_a.pm2_5_atm is None
    assert r.channel_a.pm2_5_cf_1 is None
    assert r.diagnostics.rssi_dbm is None


def test_float_rssi_rounds_to_nearest_int_not_truncated():
    """`_int_or_none` rounds rather than truncates.

    Before the fix, `int(-42.7)` truncated toward zero (→ -42) which
    is the wrong direction for negative values; round gives -43, which
    is what a user reading the value out loud would say.
    """
    payload = _minimal_payload({"pm2_5_atm": 0.0, "rssi": -42.7})
    r = SensorReading.from_payload(payload)
    assert r.diagnostics.rssi_dbm == -43


# --- channels_disagree helper --------------------------------------------


@pytest.mark.parametrize(
    "a,b,min_diff,min_pct,expected",
    [
        # Default PurpleAir thresholds (5 µg/m³ AND 70%): both must cross
        (0.0, 0.0, 5.0, 70.0, False),  # identical
        (10.0, 10.0, 5.0, 70.0, False),
        (10.0, 11.0, 5.0, 70.0, False),  # diff 1, rel 9% — neither
        (10.0, 15.0, 5.0, 70.0, False),  # diff 5, rel 33% — only abs
        (1.0, 4.0, 5.0, 70.0, False),  # diff 3, rel 75% — only rel
        (5.0, 50.0, 5.0, 70.0, True),  # diff 45, rel 90% — both
        # User-set tighter thresholds change what counts
        (10.0, 15.0, 3.0, 30.0, True),  # both crossed under tighter rules
        # Both zero: helper must not divide by zero
        (0.0, 0.0, 0.0, 0.0, False),
    ],
)
def test_channels_disagree(a, b, min_diff, min_pct, expected):
    assert (
        channels_disagree(a, b, min_diff_ugm3=min_diff, min_pct=min_pct)
        is expected
    )


def test_particle_counts_partial_presence():
    """Only some bins reported — others should land as None, not 0."""
    payload = _minimal_payload(
        {
            "pm2_5_atm": 0.0,
            "p_0_3_um": 100.0,
            "p_2_5_um": 5.5,
        }
    )
    r = SensorReading.from_payload(payload)
    assert r.channel_a.counts == ParticleCounts(
        p_0_3_um=100.0,
        p_0_5_um=None,
        p_1_0_um=None,
        p_2_5_um=5.5,
        p_5_0_um=None,
        p_10_0_um=None,
    )


# --- channel A always present, channel B detected by field presence -------


def test_channel_b_only_when_b_fields_present():
    """Adding a single `_b` field must flip the sensor to dual-channel."""
    payload = _minimal_payload({"pm2_5_atm": 0.0, "pm2_5_atm_b": 1.5})
    r = SensorReading.from_payload(payload)
    assert r.is_dual_channel is True
    assert isinstance(r.channel_b, ChannelReading)
    assert r.channel_b.pm2_5_atm == 1.5


# --- helpers --------------------------------------------------------------


def _minimal_payload(
    extras: dict, *, drop_env: bool = False, drop_diag: bool = False
) -> dict:
    """Build a small valid payload, layering in extras.

    The base has just enough to satisfy required fields and the channel-A
    presence check. Tests pass `drop_env=True` when they want to control
    environment fields themselves, or `drop_diag=True` for diagnostics.
    """
    base: dict = {
        "SensorId": "00:00:00:00:00:99",
        "version": "7.02",
        "hardwareversion": "2.0",
        "hardwarediscovered": "2.0+BME280+PMSX003-A",
        "place": "inside",
    }
    if not drop_env:
        base.update(
            {
                "current_temp_f": 72.0,
                "current_humidity": 40.0,
                "current_dewpoint_f": 45.0,
                "pressure": 1013.25,
            }
        )
    if not drop_diag:
        base.update(
            {
                "rssi": -55,
                "uptime": 1000,
                "Mem": 20000,
                "wlstate": "Connected",
                "ssid": "TestNet",
            }
        )
    base.update(extras)
    return base
