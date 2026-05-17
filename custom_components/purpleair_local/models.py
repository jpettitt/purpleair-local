"""Structured representation of one /json reading from a PurpleAir sensor.

The parser tolerates the firmware-vs-docs quirks observed on PA-II 7.02
(see DESIGN.md and the fixture README for the full list):

  - `pm2.5_aqi` (literal dot) is what the firmware sends; the docs say
    `pm2_5_aqi` (underscore). We accept either, preferring whichever is
    present.
  - `place` is `inside`/`outside` in firmware, `indoor`/`outdoor` in docs.
    Both normalize to the same `Place` enum.
  - Many optional subsystems may be absent entirely (single-laser unit,
    no BME, no Data Processor configured). The parser populates None /
    skips channels rather than guessing.

Channel-B detection is by *field presence*, not by parsing the
`hardwarediscovered` string. If `pm2_5_atm_b` is in the payload we build
a `ChannelReading` for B; otherwise we set `channel_b = None`. That keeps
us correct even if firmware ever reports a B laser in the hardware
string without populating its data fields (or vice versa).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Place(str, Enum):
    """Normalized placement of the sensor.

    Firmware sends `inside` / `outside`; community docs document
    `indoor` / `outdoor`. We normalize both to the firmware spelling
    so downstream code only ever switches on two values.
    """

    INSIDE = "inside"
    OUTSIDE = "outside"
    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, value: Any) -> "Place":
        if not isinstance(value, str):
            return cls.UNKNOWN
        v = value.strip().lower()
        if v in ("inside", "indoor"):
            return cls.INSIDE
        if v in ("outside", "outdoor"):
            return cls.OUTSIDE
        return cls.UNKNOWN


@dataclass(frozen=True)
class ParticleCounts:
    """Per-deciliter cumulative counts in six size bins (>=Nμm)."""

    p_0_3_um: float | None
    p_0_5_um: float | None
    p_1_0_um: float | None
    p_2_5_um: float | None
    p_5_0_um: float | None
    p_10_0_um: float | None


@dataclass(frozen=True)
class ChannelReading:
    """One Plantower laser channel's reading.

    Both density estimates are kept because AQI corrections all take
    `cf_1` as input while the mass entities are reported as `atm`.
    """

    pm1_0_cf_1: float | None
    pm2_5_cf_1: float | None
    pm10_0_cf_1: float | None
    pm1_0_atm: float | None
    pm2_5_atm: float | None
    pm10_0_atm: float | None
    pm2_5_aqi: int | None
    counts: ParticleCounts


@dataclass(frozen=True)
class Environment:
    """BME-detected environmental readings.

    A sensor that lacks any BME reports no environment data at all
    (parser returns `None`). When both BME280 and BME680 fields are
    present we prefer the BME680 values because it's the newer, more
    accurate part.
    """

    temp_f: float | None
    humidity_pct: float | None
    dewpoint_f: float | None
    pressure_mbar: float | None
    # BME680 only. Per docs, `gas_680` may be NaN — we coerce to None.
    voc_resistance: float | None


@dataclass(frozen=True)
class Diagnostics:
    """Per-poll health signals."""

    rssi_dbm: int | None
    uptime_s: int | None
    free_heap_bytes: int | None
    wifi_state: str | None
    ssid: str | None


@dataclass(frozen=True)
class SensorReading:
    """Structured view of one /json poll.

    Construct via `SensorReading.from_payload(json_dict)`.
    """

    sensor_id: str
    firmware_version: str | None
    hardware_version: str | None
    hardware_discovered: str | None
    place: Place
    lat: float | None
    lon: float | None
    device_time: datetime | None

    channel_a: ChannelReading
    channel_b: ChannelReading | None

    environment: Environment | None
    diagnostics: Diagnostics

    @property
    def is_dual_channel(self) -> bool:
        return self.channel_b is not None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SensorReading":
        """Build a SensorReading from a raw /json dict.

        Raises:
            ValueError: if the payload has no `SensorId`. Every other
                field is optional and missing values become None.
        """
        sensor_id = payload.get("SensorId")
        if not isinstance(sensor_id, str) or not sensor_id:
            raise ValueError("payload missing required SensorId")

        return cls(
            sensor_id=sensor_id,
            firmware_version=_str_or_none(payload.get("version")),
            hardware_version=_str_or_none(payload.get("hardwareversion")),
            hardware_discovered=_str_or_none(payload.get("hardwarediscovered")),
            place=Place.from_raw(payload.get("place")),
            lat=_float_or_none(payload.get("lat")),
            lon=_float_or_none(payload.get("lon")),
            device_time=_parse_datetime(payload.get("DateTime")),
            channel_a=_parse_channel(payload, suffix=""),
            channel_b=_parse_channel_optional(payload, suffix="_b"),
            environment=_parse_environment(payload),
            diagnostics=_parse_diagnostics(payload),
        )


# --- internal parsing helpers ---------------------------------------------


def _parse_channel(payload: dict[str, Any], *, suffix: str) -> ChannelReading:
    return ChannelReading(
        pm1_0_cf_1=_float_or_none(payload.get(f"pm1_0_cf_1{suffix}")),
        pm2_5_cf_1=_float_or_none(payload.get(f"pm2_5_cf_1{suffix}")),
        pm10_0_cf_1=_float_or_none(payload.get(f"pm10_0_cf_1{suffix}")),
        pm1_0_atm=_float_or_none(payload.get(f"pm1_0_atm{suffix}")),
        pm2_5_atm=_float_or_none(payload.get(f"pm2_5_atm{suffix}")),
        pm10_0_atm=_float_or_none(payload.get(f"pm10_0_atm{suffix}")),
        pm2_5_aqi=_get_aqi(payload, suffix=suffix),
        counts=ParticleCounts(
            p_0_3_um=_float_or_none(payload.get(f"p_0_3_um{suffix}")),
            p_0_5_um=_float_or_none(payload.get(f"p_0_5_um{suffix}")),
            p_1_0_um=_float_or_none(payload.get(f"p_1_0_um{suffix}")),
            p_2_5_um=_float_or_none(payload.get(f"p_2_5_um{suffix}")),
            p_5_0_um=_float_or_none(payload.get(f"p_5_0_um{suffix}")),
            p_10_0_um=_float_or_none(payload.get(f"p_10_0_um{suffix}")),
        ),
    )


# Fields that, when *all* absent, mean this channel doesn't exist on
# this sensor (e.g. single-laser indoor PA-II has no _b anything).
_CHANNEL_PRESENCE_KEYS = (
    "pm2_5_atm",
    "pm2_5_cf_1",
    "pm1_0_atm",
)


def _parse_channel_optional(
    payload: dict[str, Any], *, suffix: str
) -> ChannelReading | None:
    if not any(f"{k}{suffix}" in payload for k in _CHANNEL_PRESENCE_KEYS):
        return None
    return _parse_channel(payload, suffix=suffix)


# BME680 first, BME280 second. We use `or` semantics carefully so a
# present-but-None value still falls through to the fallback.
def _bme_field(
    payload: dict[str, Any], base: str
) -> tuple[Any, bool]:
    """Return (value, present) for a BME field, preferring the 680 variant."""
    if f"{base}_680" in payload:
        return payload[f"{base}_680"], True
    if base in payload:
        return payload[base], True
    return None, False


def _parse_environment(payload: dict[str, Any]) -> Environment | None:
    temp, t_present = _bme_field(payload, "current_temp_f")
    hum, h_present = _bme_field(payload, "current_humidity")
    dew, d_present = _bme_field(payload, "current_dewpoint_f")
    pres, p_present = _bme_field(payload, "pressure")
    # gas_680 is the only BME680-exclusive field; absence is fine.
    gas = payload.get("gas_680")

    if not any([t_present, h_present, d_present, p_present, gas is not None]):
        return None

    return Environment(
        temp_f=_float_or_none(temp),
        humidity_pct=_float_or_none(hum),
        dewpoint_f=_float_or_none(dew),
        pressure_mbar=_float_or_none(pres),
        voc_resistance=_float_or_none(gas),
    )


def _parse_diagnostics(payload: dict[str, Any]) -> Diagnostics:
    return Diagnostics(
        rssi_dbm=_int_or_none(payload.get("rssi")),
        uptime_s=_int_or_none(payload.get("uptime")),
        free_heap_bytes=_int_or_none(payload.get("Mem")),
        wifi_state=_str_or_none(payload.get("wlstate")),
        ssid=_str_or_none(payload.get("ssid")),
    )


def _get_aqi(payload: dict[str, Any], *, suffix: str) -> int | None:
    """Return the AQI for this channel, accepting either firmware spelling.

    Firmware 7.02 uses `pm2.5_aqi` (literal dot). Community docs (and a
    plausible future firmware) use `pm2_5_aqi`. We try the firmware
    spelling first because it's what we actually observe in the wild.
    """
    for key in (f"pm2.5_aqi{suffix}", f"pm2_5_aqi{suffix}"):
        if key in payload:
            return _int_or_none(payload[key])
    return None


def _parse_datetime(value: Any) -> datetime | None:
    """Parse the sensor's non-standard timestamp into an aware UTC datetime.

    The format observed in 7.02 is `YYYY/MM/DDTHH:MM:SSz` — slashes
    rather than ISO-8601 dashes, and a lowercase `z`. We parse manually
    rather than rely on `datetime.fromisoformat`.
    """
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.strptime(value, "%Y/%m/%dT%H:%M:%Sz")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def _float_or_none(value: Any) -> float | None:
    """Coerce numeric-ish JSON values to float; treat NaN as missing.

    The `gas_680` docs explicitly call out NaN as a no-reading marker.
    Booleans are rejected because `True`/`False` will silently coerce to
    1.0/0.0 otherwise and that's a bug, not a measurement.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if f == f else None  # NaN != NaN
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value else None
    return None


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
