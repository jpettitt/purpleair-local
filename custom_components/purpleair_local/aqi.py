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
