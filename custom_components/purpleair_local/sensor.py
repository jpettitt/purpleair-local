"""Sensor entities for PurpleAir Local.

Entity layout (per configured sensor):

  PM mass concentration (µg/m³, ATM):
    - "PM1.0", "PM2.5", "PM10" — primary (single ch: = channel A;
      dual ch: simple average of A + B)
    - "PM1.0 channel A/B", … — only created on dual-laser units

  PM2.5 AQI:
    - One entity per correction enabled in options (default: raw + EPA;
      AQandU and LRAPA available). Per-channel variants on dual.

  Particle counts (per dL, six size bins):
    - Primary only, all disabled by default. Noisy but useful for
      power users investigating sources.

  Environment (only when the sensor has a BME):
    - Temperature, humidity, dewpoint, pressure. Each is created
      only if its source field is present at setup, so a sensor
      without a BME gets no environment entities at all.
    - When both BME280 and BME680 fields are present in the payload
      the parser already prefers the 680 values, so we don't need to
      duplicate that logic here.

  Diagnostics (entity_category=diagnostic):
    - WiFi signal strength, uptime, free heap, firmware version,
      last-seen timestamp. Created unconditionally; the firmware-
      version one is a string entity for visibility in the device
      page.

Design choices worth knowing
----------------------------
Primary value for dual-channel sensors is the simple A/B average for
v0.1. If the channel-disagreement binary sensor (built in a later
step) flips on, the primary is suspect — the user can react via
automation. A smarter "fall back to the healthy channel" path is in
the design doc but out of scope here.

We construct the entity list once at setup time based on what the
first coordinator refresh returned. Fields that *appear* later won't
spawn new entities mid-life; fields that *disappear* later make their
entity report `unknown` (HA's default for `native_value = None`).
This matches what users expect for a long-lived integration: the
device card doesn't grow rows on its own.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .aqi import aqi_aqandu, aqi_epa, aqi_lrapa, aqi_raw
from .const import (
    AQI_CORRECTION_AQANDU,
    AQI_CORRECTION_EPA,
    AQI_CORRECTION_LRAPA,
    AQI_CORRECTION_RAW,
    CONF_AQI_CORRECTIONS,
    DEFAULT_AQI_CORRECTIONS,
    DOMAIN,
)
from .coordinator import PurpleAirCoordinator
from .models import SensorReading

_LOGGER = logging.getLogger(__name__)

# Concentration unit string — HA's CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
# constant exists but is a bare string anyway; using the literal keeps the
# imports tidy.
_UGM3 = "µg/m³"

# Particle counts are per deciliter (0.1 L) of air; HA has no built-in
# unit constant for that, so we ship the literal.
_PER_DL = "particles/dL"


# ---------------------------------------------------------------------------
# Channel-value helpers
# ---------------------------------------------------------------------------

_CHANNEL_PRIMARY = "primary"
_CHANNEL_A = "a"
_CHANNEL_B = "b"


def _channel_mass(
    reading: SensorReading, channel: str, field: str
) -> float | None:
    """Return one PM mass field from the requested channel context.

    Primary on single-laser sensors is channel A. Primary on dual is
    the simple A/B average (with sensible fallback when one side
    is None for some reason).
    """
    if channel == _CHANNEL_A:
        return getattr(reading.channel_a, field)
    if channel == _CHANNEL_B:
        return (
            getattr(reading.channel_b, field)
            if reading.channel_b is not None
            else None
        )
    # primary
    a = getattr(reading.channel_a, field)
    if reading.channel_b is None:
        return a
    b = getattr(reading.channel_b, field)
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2.0


def _channel_cf1(reading: SensorReading, channel: str) -> float | None:
    return _channel_mass(reading, channel, "pm2_5_cf_1")


def _channel_atm(reading: SensorReading, channel: str) -> float | None:
    return _channel_mass(reading, channel, "pm2_5_atm")


def _channel_aqi_onboard(
    reading: SensorReading, channel: str
) -> int | None:
    """The AQI value the sensor itself computed, before any correction."""
    if channel == _CHANNEL_A:
        return reading.channel_a.pm2_5_aqi
    if channel == _CHANNEL_B and reading.channel_b is not None:
        return reading.channel_b.pm2_5_aqi
    # primary uses our own raw-AQI conversion of the averaged ATM
    return aqi_raw(_channel_atm(reading, _CHANNEL_PRIMARY))


# ---------------------------------------------------------------------------
# Base entity
# ---------------------------------------------------------------------------


class PurpleAirEntity(CoordinatorEntity[PurpleAirCoordinator]):
    """Common device wiring shared by every PurpleAir entity.

    `_attr_has_entity_name = True` lets HA render entities as
    "<device name> · <entity name>" in the UI, so we only need to
    set the short entity-specific name on each subclass.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: PurpleAirCoordinator) -> None:
        super().__init__(coordinator)
        reading = coordinator.data
        self._sensor_id = reading.sensor_id
        clean_mac = reading.sensor_id.replace(":", "")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, reading.sensor_id)},
            connections={(dr.CONNECTION_NETWORK_MAC, reading.sensor_id)},
            manufacturer="PurpleAir",
            name=f"PurpleAir {clean_mac[-4:]}",
            model=_model_label(reading),
            sw_version=reading.firmware_version,
            configuration_url=f"http://{coordinator.client.host}/",
        )


def _model_label(reading: SensorReading) -> str:
    """Build a human-readable model string from hardware_discovered.

    E.g. "2.0+BME280+PMSX003-A" → "PA-II (BME280, PMSX003-A)".
    Falls back to "PurpleAir" if hardware metadata is missing.
    """
    hw = reading.hardware_discovered or ""
    parts = [p for p in hw.split("+") if p]
    if not parts:
        return "PurpleAir"
    # First part is the board version ("1.0", "2.0", …); the rest are
    # detected sub-modules.
    board = parts[0]
    extras = parts[1:]
    label = f"PA-II ({', '.join(extras)})" if extras else "PA-II"
    return f"{label} v{board}"


# ---------------------------------------------------------------------------
# PM mass entities
# ---------------------------------------------------------------------------

_PM_MASS_FIELDS: tuple[tuple[str, str, SensorDeviceClass], ...] = (
    # (short_name, atm_field, device_class)
    ("PM1.0", "pm1_0_atm", SensorDeviceClass.PM1),
    ("PM2.5", "pm2_5_atm", SensorDeviceClass.PM25),
    ("PM10", "pm10_0_atm", SensorDeviceClass.PM10),
)


class _PmMassEntity(PurpleAirEntity, SensorEntity):
    _attr_native_unit_of_measurement = _UGM3
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: PurpleAirCoordinator,
        *,
        channel: str,
        short_name: str,
        atm_field: str,
        device_class: SensorDeviceClass,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._atm_field = atm_field
        self._attr_device_class = device_class
        self._attr_unique_id = f"{self._sensor_id}_{channel}_{atm_field}"
        self._attr_name = _label_with_channel(short_name, channel)

    @property
    def native_value(self) -> float | None:
        return _channel_mass(
            self.coordinator.data, self._channel, self._atm_field
        )


# ---------------------------------------------------------------------------
# AQI entities
# ---------------------------------------------------------------------------

_AQI_LABELS: dict[str, str] = {
    AQI_CORRECTION_RAW: "AQI (raw)",
    AQI_CORRECTION_EPA: "AQI (EPA)",
    AQI_CORRECTION_AQANDU: "AQI (AQandU)",
    AQI_CORRECTION_LRAPA: "AQI (LRAPA)",
}


def _aqi_value(
    reading: SensorReading, channel: str, correction: str
) -> int | None:
    """Compute the AQI for one channel-context under one correction.

    "raw" uses what the sensor put in pm2.5_aqi for the per-channel
    entities and our own conversion of the averaged ATM for primary;
    everything else feeds cf_1 (and humidity, for EPA) through the
    published correction formula.
    """
    if correction == AQI_CORRECTION_RAW:
        return _channel_aqi_onboard(reading, channel)
    cf1 = _channel_cf1(reading, channel)
    if correction == AQI_CORRECTION_AQANDU:
        return aqi_aqandu(cf1)
    if correction == AQI_CORRECTION_LRAPA:
        return aqi_lrapa(cf1)
    if correction == AQI_CORRECTION_EPA:
        rh = (
            reading.environment.humidity_pct
            if reading.environment is not None
            else None
        )
        return aqi_epa(cf1, rh)
    return None


class _AqiEntity(PurpleAirEntity, SensorEntity):
    # HA has SensorDeviceClass.AQI; native unit is "None" for AQI
    # because the index is unitless. State class is MEASUREMENT so
    # it gets graphed nicely.
    _attr_device_class = SensorDeviceClass.AQI
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: PurpleAirCoordinator,
        *,
        channel: str,
        correction: str,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._correction = correction
        self._attr_unique_id = (
            f"{self._sensor_id}_{channel}_aqi_{correction}"
        )
        self._attr_name = _label_with_channel(
            _AQI_LABELS[correction], channel
        )

    @property
    def native_value(self) -> int | None:
        return _aqi_value(
            self.coordinator.data, self._channel, self._correction
        )


# ---------------------------------------------------------------------------
# Particle counts (primary, disabled by default)
# ---------------------------------------------------------------------------

_PARTICLE_BINS: tuple[tuple[str, str], ...] = (
    # (short_name, field_on_ParticleCounts)
    ("Particles ≥0.3μm", "p_0_3_um"),
    ("Particles ≥0.5μm", "p_0_5_um"),
    ("Particles ≥1.0μm", "p_1_0_um"),
    ("Particles ≥2.5μm", "p_2_5_um"),
    ("Particles ≥5.0μm", "p_5_0_um"),
    ("Particles ≥10μm", "p_10_0_um"),
)


def _primary_count(reading: SensorReading, field: str) -> float | None:
    a = getattr(reading.channel_a.counts, field)
    if reading.channel_b is None:
        return a
    b = getattr(reading.channel_b.counts, field)
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2.0


class _ParticleCountEntity(PurpleAirEntity, SensorEntity):
    _attr_native_unit_of_measurement = _PER_DL
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False  # noisy; opt-in
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: PurpleAirCoordinator,
        *,
        short_name: str,
        field: str,
    ) -> None:
        super().__init__(coordinator)
        self._field = field
        self._attr_unique_id = f"{self._sensor_id}_primary_count_{field}"
        self._attr_name = short_name

    @property
    def native_value(self) -> float | None:
        return _primary_count(self.coordinator.data, self._field)


# ---------------------------------------------------------------------------
# Environment entities (only when the source field is present at setup)
# ---------------------------------------------------------------------------


class _EnvironmentEntity(PurpleAirEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: PurpleAirCoordinator,
        *,
        short_name: str,
        env_field: str,
        unique_key: str,
        device_class: SensorDeviceClass,
        unit: str,
        precision: int = 1,
    ) -> None:
        super().__init__(coordinator)
        self._env_field = env_field
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_suggested_display_precision = precision
        self._attr_unique_id = f"{self._sensor_id}_env_{unique_key}"
        self._attr_name = short_name

    @property
    def native_value(self) -> float | None:
        env = self.coordinator.data.environment
        return getattr(env, self._env_field) if env is not None else None


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


class _RssiEntity(PurpleAirEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PurpleAirCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._sensor_id}_diag_rssi"
        self._attr_name = "WiFi signal"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.diagnostics.rssi_dbm


class _UptimeEntity(PurpleAirEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PurpleAirCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._sensor_id}_diag_uptime"
        self._attr_name = "Uptime"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.diagnostics.uptime_s


class _FreeHeapEntity(PurpleAirEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # rarely useful; opt-in

    def __init__(self, coordinator: PurpleAirCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._sensor_id}_diag_free_heap"
        self._attr_name = "Free heap"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.diagnostics.free_heap_bytes


class _FirmwareEntity(PurpleAirEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # also on device page

    def __init__(self, coordinator: PurpleAirCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._sensor_id}_diag_firmware"
        self._attr_name = "Firmware version"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.firmware_version


class _LastSeenEntity(PurpleAirEntity, SensorEntity):
    """When the sensor itself reported its current data (UTC)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PurpleAirCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._sensor_id}_diag_last_seen"
        self._attr_name = "Last reported"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.device_time


# ---------------------------------------------------------------------------
# Naming helper
# ---------------------------------------------------------------------------


def _label_with_channel(base: str, channel: str) -> str:
    """Append " channel A/B" for per-channel entities; bare for primary."""
    if channel == _CHANNEL_A:
        return f"{base} channel A"
    if channel == _CHANNEL_B:
        return f"{base} channel B"
    return base


# ---------------------------------------------------------------------------
# Entity factory + platform setup
# ---------------------------------------------------------------------------


def build_entities(
    coordinator: PurpleAirCoordinator, options: dict[str, Any]
) -> list[SensorEntity]:
    """Build the full entity list for one configured sensor.

    Reads `coordinator.data` (which `async_setup_entry` guarantees is
    populated via `async_config_entry_first_refresh`) and the user's
    options dict to decide which AQI corrections to expose. Skips
    environment entities whose source field is absent.
    """
    reading = coordinator.data
    entities: list[SensorEntity] = []

    channels: list[str] = [_CHANNEL_PRIMARY]
    if reading.is_dual_channel:
        channels += [_CHANNEL_A, _CHANNEL_B]

    # PM mass
    for channel in channels:
        for short_name, field, device_class in _PM_MASS_FIELDS:
            entities.append(
                _PmMassEntity(
                    coordinator,
                    channel=channel,
                    short_name=short_name,
                    atm_field=field,
                    device_class=device_class,
                )
            )

    # AQI per enabled correction
    enabled_corrections: list[str] = list(
        options.get(CONF_AQI_CORRECTIONS, DEFAULT_AQI_CORRECTIONS)
    )
    for channel in channels:
        for correction in enabled_corrections:
            entities.append(
                _AqiEntity(
                    coordinator, channel=channel, correction=correction
                )
            )

    # Particle counts (primary only, disabled by default)
    for short_name, field in _PARTICLE_BINS:
        entities.append(
            _ParticleCountEntity(
                coordinator, short_name=short_name, field=field
            )
        )

    # Environment (each created only if its source field is present)
    if reading.environment is not None:
        env = reading.environment
        if env.temp_f is not None:
            entities.append(
                _EnvironmentEntity(
                    coordinator,
                    short_name="Temperature",
                    env_field="temp_f",
                    unique_key="temperature",
                    device_class=SensorDeviceClass.TEMPERATURE,
                    unit=UnitOfTemperature.FAHRENHEIT,
                )
            )
        if env.humidity_pct is not None:
            entities.append(
                _EnvironmentEntity(
                    coordinator,
                    short_name="Humidity",
                    env_field="humidity_pct",
                    unique_key="humidity",
                    device_class=SensorDeviceClass.HUMIDITY,
                    unit=PERCENTAGE,
                    precision=0,
                )
            )
        if env.dewpoint_f is not None:
            entities.append(
                _EnvironmentEntity(
                    coordinator,
                    short_name="Dewpoint",
                    env_field="dewpoint_f",
                    unique_key="dewpoint",
                    device_class=SensorDeviceClass.TEMPERATURE,
                    unit=UnitOfTemperature.FAHRENHEIT,
                )
            )
        if env.pressure_mbar is not None:
            entities.append(
                _EnvironmentEntity(
                    coordinator,
                    short_name="Pressure",
                    env_field="pressure_mbar",
                    unique_key="pressure",
                    device_class=SensorDeviceClass.PRESSURE,
                    unit=UnitOfPressure.MBAR,
                )
            )
        if env.voc_resistance is not None:
            entities.append(
                _EnvironmentEntity(
                    coordinator,
                    short_name="VOC resistance",
                    env_field="voc_resistance",
                    unique_key="voc",
                    device_class=SensorDeviceClass.AQI,  # closest match
                    unit="Ω",
                )
            )

    # Diagnostics — always created.
    entities.append(_RssiEntity(coordinator))
    entities.append(_UptimeEntity(coordinator))
    entities.append(_FreeHeapEntity(coordinator))
    entities.append(_FirmwareEntity(coordinator))
    entities.append(_LastSeenEntity(coordinator))

    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PurpleAirCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(build_entities(coordinator, dict(entry.options)))
