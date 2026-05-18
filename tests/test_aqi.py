"""Tests for the AQI correction formulas and 2024 EPA breakpoint table.

The math is small and well-defined, so the tests are mostly point-checks
at boundaries and a couple of worked examples that match what the
published correction equations should output.
"""

from __future__ import annotations

import math

import pytest

from custom_components.purpleair_local.aqi import (
    AqiCategory,
    aqi_aqandu,
    aqi_category,
    aqi_epa,
    aqi_lrapa,
    aqi_raw,
    correct_aqandu,
    correct_epa,
    correct_lrapa,
    pm25_to_aqi,
)


# --- correction math -------------------------------------------------------


@pytest.mark.parametrize(
    "pm_cf1,rh,expected",
    [
        # Standard worked examples; coefficients from Barkjohn 2021.
        (0.0, 0.0, 5.75),
        (0.0, 50.0, 1.44),
        (10.0, 50.0, 6.68),
        (100.0, 30.0, 55.564),
        # High humidity at very low PM gives a negative raw result; clamp.
        (0.0, 100.0, 0.0),
    ],
)
def test_correct_epa(pm_cf1, rh, expected):
    assert math.isclose(correct_epa(pm_cf1, rh), expected, abs_tol=1e-6)


@pytest.mark.parametrize(
    "pm_cf1,expected",
    [
        (0.0, 2.65),
        (10.0, 10.43),
        (100.0, 80.45),
    ],
)
def test_correct_aqandu(pm_cf1, expected):
    assert math.isclose(correct_aqandu(pm_cf1), expected, abs_tol=1e-6)


@pytest.mark.parametrize(
    "pm_cf1,expected",
    [
        # Below ~1.32 µg/m³ the linear LRAPA formula goes negative; clamp.
        (0.0, 0.0),
        (1.0, 0.0),
        (2.0, 0.34),
        (10.0, 4.34),
        (100.0, 49.34),
    ],
)
def test_correct_lrapa(pm_cf1, expected):
    assert math.isclose(correct_lrapa(pm_cf1), expected, abs_tol=1e-6)


# --- AQI breakpoint table (2024) ------------------------------------------


@pytest.mark.parametrize(
    "pm,aqi",
    [
        # Bottom of each band — exact AQI low value.
        (0.0, 0),
        (9.1, 51),
        (35.5, 101),
        (55.5, 151),
        (125.5, 201),
        (225.5, 301),
        # Top of each band — exact AQI high value.
        (9.0, 50),
        (35.4, 100),
        (55.4, 150),
        (125.4, 200),
        (225.4, 300),
        (325.4, 500),
    ],
)
def test_pm25_to_aqi_band_boundaries(pm, aqi):
    assert pm25_to_aqi(pm) == aqi


def test_pm25_to_aqi_truncates_to_one_decimal():
    # AirNow TAD: input is truncated to 1 decimal before lookup, not
    # rounded. 9.07 must land in the lower band (AQI 50), not be
    # rounded up into 9.1 (AQI 51).
    assert pm25_to_aqi(9.07) == 50
    assert pm25_to_aqi(9.099) == 50
    assert pm25_to_aqi(9.10) == 51


def test_pm25_to_aqi_negative_returns_zero():
    # Corrections clamp to 0, but be defensive at this layer too.
    assert pm25_to_aqi(-1.0) == 0
    assert pm25_to_aqi(-1000.0) == 0


def test_pm25_to_aqi_above_top_band_extrapolates():
    # During wildfire conditions, PA sensors report >>325.4 µg/m³.
    # We continue the top band's slope rather than capping at 500 so
    # downstream alerts have a useful number to thresh against.
    high = pm25_to_aqi(500.0)
    assert high > 500
    # Slope of top band ≈ 199/99.9 ≈ 1.992; extrapolating from 225.5/301:
    #   round(1.992 * (500 - 225.5) + 301) ≈ 848
    assert 840 <= high <= 855


# --- category labels -------------------------------------------------------


@pytest.mark.parametrize(
    "aqi,expected",
    [
        (0, AqiCategory.GOOD),
        (50, AqiCategory.GOOD),
        (51, AqiCategory.MODERATE),
        (100, AqiCategory.MODERATE),
        (101, AqiCategory.UNHEALTHY_SENSITIVE),
        (150, AqiCategory.UNHEALTHY_SENSITIVE),
        (151, AqiCategory.UNHEALTHY),
        (200, AqiCategory.UNHEALTHY),
        (201, AqiCategory.VERY_UNHEALTHY),
        (300, AqiCategory.VERY_UNHEALTHY),
        (301, AqiCategory.HAZARDOUS),
        (500, AqiCategory.HAZARDOUS),
        (800, AqiCategory.HAZARDOUS),
    ],
)
def test_aqi_category(aqi, expected):
    assert aqi_category(aqi) is expected


# --- None-tolerant convenience entry points -------------------------------


def test_convenience_functions_propagate_none():
    assert aqi_raw(None) is None
    assert aqi_epa(None, 50.0) is None
    assert aqi_epa(10.0, None) is None
    assert aqi_aqandu(None) is None
    assert aqi_lrapa(None) is None


def test_convenience_functions_produce_expected_aqi():
    # Lightly smoky day: PA reports 100 µg/m³ CF=1, RH 30%, ATM 80.
    # EPA: correct_epa(100, 30) = 55.564 → AQI band 3 → 151 (top of band 3 is 150 at 55.4;
    #   55.5 → 151; 55.564 trunc to 55.5 → 151).
    assert aqi_epa(100.0, 30.0) == 151
    # AQandU: 80.45 → AQI band 4 (55.5-125.4 → 151-200).
    #   80.4 → slope (200-151)/(125.4-55.5) * (80.4-55.5) + 151 = 0.701 * 24.9 + 151 ≈ 168.5 → 168
    assert aqi_aqandu(100.0) == 168
    # LRAPA: 49.34 → band 3 → trunc to 49.3.
    #   slope (150-101)/(55.4-35.5) * (49.3-35.5) + 101 = 2.4623 * 13.8 + 101 ≈ 134.97 → 135
    assert aqi_lrapa(100.0) == 135
    # Raw ATM 80 → band 4. 80.0 → slope * (80-55.5) + 151 = 0.701 * 24.5 + 151 ≈ 168.2 → 168
    assert aqi_raw(80.0) == 168


def test_zero_input_yields_zero_aqi_across_methods():
    """A clean-air sensor (0 µg/m³) should report AQI 0 every way."""
    assert aqi_raw(0.0) == 0
    # EPA at 0/50% gives 1.44 µg/m³ → still AQI band 1.
    #   1.4 → slope (50-0)/(9.0-0.0) * (1.4-0.0) + 0 = 5.556 * 1.4 ≈ 7.78 → 8
    assert aqi_epa(0.0, 50.0) == 8
    # AQandU at 0 gives 2.65 → trunc 2.6 → AQI ≈ 14
    assert aqi_aqandu(0.0) == 14
    # LRAPA at 0 clamps to 0 → AQI 0
    assert aqi_lrapa(0.0) == 0
