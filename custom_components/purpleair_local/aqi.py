"""PurpleAir mass-to-AQI conversions, with three published corrections.

Three corrections are implemented as pure functions. Each takes one
PM2.5 reading (and humidity, for the EPA one) and returns a corrected
mass concentration in µg/m³. A fourth function maps that corrected
mass to the US EPA Air Quality Index using the 2024-revised PM2.5
breakpoint table.

Why three corrections (and one "raw")
-------------------------------------
The Plantower PMS5003 inside a PurpleAir reads high in most ambient
conditions. Different agencies have published empirical corrections to
align it with regulatory monitors:

  - **EPA (Barkjohn 2021)** — the US-wide correction now used on the
    AirNow Fire and Smoke Map. Accurate to ~250 µg/m³; a piecewise
    extension exists for higher concentrations during heavy smoke and
    can be added later as a separate option.
  - **AQandU** — University of Utah's correction, popular among home
    users in the western US.
  - **LRAPA** — Lane Regional Air Protection Agency (Oregon). Tuned
    for wood-smoke aerosols; tends to under-correct in non-smoke
    conditions.

The "raw" option is the AQI of the uncorrected ATM density, for users
who want what the sensor itself reports without any post-processing.

Inputs
------
All corrections take `pm_cf1` (the `pm2_5_cf_1` field from the sensor).
This is the "CF=1" density estimate, which is what every published
correction was fit against. RH is in percent, matching the sensor's
`current_humidity` field. Negative corrected values are clamped to 0.

AQI breakpoints
---------------
The EPA revised the PM2.5 sub-index of the AQI on 2024-05-06. The
breakpoint table below uses the post-revision values. PM2.5 input is
truncated to one decimal place before lookup, per the AirNow Technical
Assistance Document for the Reporting of Daily Air Quality. Inputs
above the top breakpoint (325.4 µg/m³ = AQI 500) extrapolate using the
slope of the top band so that wildfire-era readings still produce a
useful number; AirNow caps display at "500+" but downstream
consumers (alerts, dashboards) usually want the underlying number.

Sources
-------
- Barkjohn et al. 2021: doi:10.5194/amt-14-4617-2021
- EPA AirNow Fire & Smoke Map technical approaches:
  https://www.epa.gov/air-sensor-toolbox/technical-approaches-sensor-data-airnow-fire-and-smoke-map
- AirNow Technical Assistance Document (AQI calculation method):
  https://document.airnow.gov/technical-assistance-document-for-the-reporting-of-daily-air-quailty.pdf
- 2024 PM NAAQS / AQI revision: 89 FR 16202 (effective 2024-05-06)
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple

# --- per-country color schemes --------------------------------------------
#
# Each entry maps a PM2.5 (24-h µg/m³) reading to one of the country's
# named bands plus its official band colour. Bands are defined by their
# upper inclusive PM2.5 bound. The topmost band uses `inf` so values
# outside the table still resolve to "the worst category we publish".
#
# These power the `category` / `category_color` attributes on each AQI
# entity and are completely independent of which AQI breakpoint table
# the integer state value uses (we always emit the US-EPA AQI number).
# Users in the UK / EU who look at their PM2.5 sensor next to a local
# news site can pick the matching scheme so the colour and label match
# what they expect, even when the AQI number itself reflects the US
# breakpoints.


class _ColorBand(NamedTuple):
    pm25_high_ugm3: float  # inclusive upper bound
    category: str  # stable snake_case, used as the entity attribute
    color: str  # `#rrggbb`


# US EPA — the 2024-revised PM2.5 sub-index colours from AirNow.
_US_EPA_BANDS: tuple[_ColorBand, ...] = (
    _ColorBand(9.0, "good", "#00e400"),
    _ColorBand(35.4, "moderate", "#ffff00"),
    _ColorBand(55.4, "unhealthy_for_sensitive_groups", "#ff7e00"),
    _ColorBand(125.4, "unhealthy", "#ff0000"),
    _ColorBand(225.4, "very_unhealthy", "#8f3f97"),
    _ColorBand(float("inf"), "hazardous", "#7e0023"),
)

# EU EAQI — European Environment Agency, PM2.5 24-h bands and the
# official EAQI palette.
_EU_EAQI_BANDS: tuple[_ColorBand, ...] = (
    _ColorBand(10.0, "good", "#50f0e6"),
    _ColorBand(20.0, "fair", "#50ccaa"),
    _ColorBand(25.0, "moderate", "#f0e641"),
    _ColorBand(50.0, "poor", "#ff5050"),
    _ColorBand(75.0, "very_poor", "#960032"),
    _ColorBand(float("inf"), "extremely_poor", "#7d2181"),
)

# UK DAQI — UK Defra Daily Air Quality Index, 10 numeric bands grouped
# into four risk levels (1-3 Low, 4-6 Moderate, 7-9 High, 10 Very High).
# Category strings encode the numeric band so users can tell adjacent
# bands apart in templates ("low_3" vs "moderate_4"). Colors are the
# Defra spec.
_UK_DAQI_BANDS: tuple[_ColorBand, ...] = (
    _ColorBand(11.0, "low_1", "#9cff9c"),
    _ColorBand(23.0, "low_2", "#31ff00"),
    _ColorBand(35.0, "low_3", "#31cf00"),
    _ColorBand(41.0, "moderate_4", "#ffff00"),
    _ColorBand(47.0, "moderate_5", "#ffcf00"),
    _ColorBand(53.0, "moderate_6", "#ff9a00"),
    _ColorBand(58.0, "high_7", "#ff6464"),
    _ColorBand(64.0, "high_8", "#ff0000"),
    _ColorBand(70.0, "high_9", "#990000"),
    _ColorBand(float("inf"), "very_high_10", "#ce30ff"),
)

AQI_COLOR_SCHEME_US_EPA = "us_epa"
AQI_COLOR_SCHEME_EU_EAQI = "eu_eaqi"
AQI_COLOR_SCHEME_UK_DAQI = "uk_daqi"

_SCHEMES: dict[str, tuple[_ColorBand, ...]] = {
    AQI_COLOR_SCHEME_US_EPA: _US_EPA_BANDS,
    AQI_COLOR_SCHEME_EU_EAQI: _EU_EAQI_BANDS,
    AQI_COLOR_SCHEME_UK_DAQI: _UK_DAQI_BANDS,
}

AQI_COLOR_SCHEMES_ALL: tuple[str, ...] = tuple(_SCHEMES.keys())


def aqi_band(
    pm25_ugm3: float | None, *, scheme: str = AQI_COLOR_SCHEME_US_EPA
) -> tuple[str, str] | None:
    """Return `(category, color_hex)` for a PM2.5 reading under one scheme.

    Returns `None` only when the input itself is `None` (so callers
    don't need a separate "missing reading" branch — they can pass the
    optional value straight through).

    Negative values are clamped to 0 so a humidity-overcorrected EPA
    reading (`-0.4 µg/m³` is a real possibility from Barkjohn at low
    PM and high RH) lands in the lowest band rather than slipping
    through a `<0` guard somewhere.

    Input is truncated to 1 decimal before lookup, mirroring the
    convention in `pm25_to_aqi()`.
    """
    if pm25_ugm3 is None:
        return None
    if pm25_ugm3 < 0:
        pm25_ugm3 = 0.0
    bands = _SCHEMES.get(scheme)
    if bands is None:
        # Unknown scheme — fall back to the default rather than
        # surfacing as `unknown` on the entity, since a typo in the
        # options shouldn't make the integration look broken.
        bands = _US_EPA_BANDS
    c = int(pm25_ugm3 * 10) / 10
    for band in bands:
        if c <= band.pm25_high_ugm3:
            return (band.category, band.color)
    # Unreachable: the last band always has pm25_high_ugm3 = inf.
    top = bands[-1]
    return (top.category, top.color)

# --- AQI category labels ---------------------------------------------------


class AqiCategory(str, Enum):
    GOOD = "good"
    MODERATE = "moderate"
    UNHEALTHY_SENSITIVE = "unhealthy_for_sensitive_groups"
    UNHEALTHY = "unhealthy"
    VERY_UNHEALTHY = "very_unhealthy"
    HAZARDOUS = "hazardous"


def aqi_category(aqi: int) -> AqiCategory:
    """Return the AQI category for an integer AQI value."""
    if aqi <= 50:
        return AqiCategory.GOOD
    if aqi <= 100:
        return AqiCategory.MODERATE
    if aqi <= 150:
        return AqiCategory.UNHEALTHY_SENSITIVE
    if aqi <= 200:
        return AqiCategory.UNHEALTHY
    if aqi <= 300:
        return AqiCategory.VERY_UNHEALTHY
    return AqiCategory.HAZARDOUS


# --- breakpoint table ------------------------------------------------------


class _Band(NamedTuple):
    pm_lo: float
    pm_hi: float
    aqi_lo: int
    aqi_hi: int


# 2024-revised US EPA PM2.5 → AQI breakpoints (24-hr basis).
# Effective 2024-05-06. Per AirNow TAD, input PM2.5 is truncated to one
# decimal place before lookup, so the gap between band-tops and the next
# band-low (e.g. 9.0 vs 9.1) is by construction never landed in.
_PM25_BANDS: tuple[_Band, ...] = (
    _Band(0.0, 9.0, 0, 50),
    _Band(9.1, 35.4, 51, 100),
    _Band(35.5, 55.4, 101, 150),
    _Band(55.5, 125.4, 151, 200),
    _Band(125.5, 225.4, 201, 300),
    _Band(225.5, 325.4, 301, 500),
)


# --- pure correction math --------------------------------------------------


def correct_epa(pm_cf1: float, rh_pct: float) -> float:
    """Barkjohn 2021 EPA correction.

    Validated to ~250 µg/m³ PM2.5; above that it underestimates and
    callers may prefer the (not-yet-implemented) piecewise extension.
    Negative outputs (which occur at very low PM and high humidity) are
    clamped to 0.
    """
    corrected = 0.524 * pm_cf1 - 0.0862 * rh_pct + 5.75
    return corrected if corrected > 0.0 else 0.0


def correct_aqandu(pm_cf1: float) -> float:
    """University of Utah AQandU correction."""
    corrected = 0.778 * pm_cf1 + 2.65
    return corrected if corrected > 0.0 else 0.0


def correct_lrapa(pm_cf1: float) -> float:
    """LRAPA (Oregon) wood-smoke-tuned correction.

    Published as valid below ~65 µg/m³; we still compute it above that
    and surface it, but consumers should be aware the formula's fit
    degrades at higher concentrations.
    """
    corrected = 0.5 * pm_cf1 - 0.66
    return corrected if corrected > 0.0 else 0.0


# --- mass → AQI ------------------------------------------------------------


def pm25_to_aqi(pm25_ugm3: float) -> int:
    """Convert a PM2.5 mass concentration (µg/m³) to integer AQI.

    Uses the 2024 EPA breakpoint table. Input is truncated to one
    decimal place per the AirNow TAD. Values above the top band
    (325.4 µg/m³ → AQI 500) are extrapolated using the top band's
    slope rather than capped at 500, because wildfire-era readings
    benefit from a continued numeric signal.
    """
    if pm25_ugm3 < 0:
        return 0

    # Truncate to one decimal (EPA convention). Using int(... * 10) / 10
    # rather than round() is intentional: 9.07 -> 9.0, not 9.1, so the
    # measurement maps into the lower band as EPA documents.
    c = int(pm25_ugm3 * 10) / 10

    for band in _PM25_BANDS:
        if band.pm_lo <= c <= band.pm_hi:
            return _interp(c, band)

    # Above the top band: extrapolate. Slope of the top band continues.
    top = _PM25_BANDS[-1]
    return _interp(c, top)


def _interp(c: float, band: _Band) -> int:
    slope = (band.aqi_hi - band.aqi_lo) / (band.pm_hi - band.pm_lo)
    return round(slope * (c - band.pm_lo) + band.aqi_lo)


# --- None-tolerant entry points used by the entity layer ------------------


def aqi_raw(pm2_5_atm: float | None) -> int | None:
    """AQI of the uncorrected ATM density (what the sensor reports)."""
    if pm2_5_atm is None:
        return None
    return pm25_to_aqi(pm2_5_atm)


def aqi_epa(pm_cf1: float | None, rh_pct: float | None) -> int | None:
    """AQI of the EPA-corrected density. Returns None if any input missing."""
    if pm_cf1 is None or rh_pct is None:
        return None
    return pm25_to_aqi(correct_epa(pm_cf1, rh_pct))


def aqi_aqandu(pm_cf1: float | None) -> int | None:
    """AQI of the AQandU-corrected density."""
    if pm_cf1 is None:
        return None
    return pm25_to_aqi(correct_aqandu(pm_cf1))


def aqi_lrapa(pm_cf1: float | None) -> int | None:
    """AQI of the LRAPA-corrected density."""
    if pm_cf1 is None:
        return None
    return pm25_to_aqi(correct_lrapa(pm_cf1))
