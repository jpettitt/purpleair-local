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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .aqi import (
    aqi_band,
    correct_aqandu,
    correct_epa,
    correct_lrapa,
    pm25_to_aqi,
)
from .const import (
    AQI_CORRECTION_AQANDU,
    AQI_CORRECTION_EPA,
    AQI_CORRECTION_LRAPA,
    AQI_CORRECTION_RAW,
    CONF_AQI_COLOR_SCHEME,
    CONF_AQI_CORRECTIONS,
    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
    DEFAULT_AQI_COLOR_SCHEME,
    DEFAULT_AQI_CORRECTIONS,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    DOMAIN,
)
from .coordinator import PurpleAirCoordinator
from .entity import PurpleAirEntity
from .models import SensorReading, channels_disagree

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
    reading: SensorReading,
    channel: str,
    field: str,
    *,
    disagreement_min_diff_ugm3: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    disagreement_min_pct: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
) -> float | None:
    """Return one PM mass field from the requested channel context.

    Primary on single-laser sensors is channel A. Primary on dual:

      - When both channels agree (per the supplied disagreement
        thresholds, evaluated on `pm2_5_atm` as the canonical
        comparison signal), we return the A/B average.
      - When the channels disagree, we return the LOWER of the two
        values. The canonical PA failure mode is laser degradation
        (dust occlusion, EOL drift) which makes the affected channel
        read high, so the lower value is the more conservative — and
        usually more accurate — choice. The accompanying
        `binary_sensor.channel_disagreement` entity surfaces the
        condition so automations can react.

    Disagreement thresholds are passed in from the options flow
    (with the PurpleAir defaults applied as fallbacks at the call
    site / here). Same `channels_disagree` rule the binary sensor
    uses; the two stay in sync because they share the helper.
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

    # Disagreement is determined on PM2.5 ATM regardless of which
    # field we're returning, because PM2.5 is the signal that drives
    # the binary sensor's behaviour and we want consistency: when
    # the disagreement flag is set, ALL primary mass entities switch
    # to the lower-value path together.
    pm25_a = reading.channel_a.pm2_5_atm
    pm25_b = reading.channel_b.pm2_5_atm
    if pm25_a is not None and pm25_b is not None and channels_disagree(
        pm25_a,
        pm25_b,
        min_diff_ugm3=disagreement_min_diff_ugm3,
        min_pct=disagreement_min_pct,
    ):
        return min(a, b)
    return (a + b) / 2.0


def _channel_cf1(
    reading: SensorReading,
    channel: str,
    *,
    disagreement_min_diff_ugm3: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    disagreement_min_pct: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
) -> float | None:
    return _channel_mass(
        reading,
        channel,
        "pm2_5_cf_1",
        disagreement_min_diff_ugm3=disagreement_min_diff_ugm3,
        disagreement_min_pct=disagreement_min_pct,
    )


def _channel_atm(
    reading: SensorReading,
    channel: str,
    *,
    disagreement_min_diff_ugm3: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    disagreement_min_pct: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
) -> float | None:
    return _channel_mass(
        reading,
        channel,
        "pm2_5_atm",
        disagreement_min_diff_ugm3=disagreement_min_diff_ugm3,
        disagreement_min_pct=disagreement_min_pct,
    )




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
        disagreement_min_diff_ugm3: float,
        disagreement_min_pct: float,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._atm_field = atm_field
        self._disagreement_min_diff_ugm3 = disagreement_min_diff_ugm3
        self._disagreement_min_pct = disagreement_min_pct
        self._attr_device_class = device_class
        self._attr_unique_id = f"{self._sensor_id}_{channel}_{atm_field}"
        self._attr_name = _label_with_channel(short_name, channel)

    @property
    def native_value(self) -> float | None:
        return _channel_mass(
            self.coordinator.data,
            self._channel,
            self._atm_field,
            disagreement_min_diff_ugm3=self._disagreement_min_diff_ugm3,
            disagreement_min_pct=self._disagreement_min_pct,
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


def _aqi_corrected_pm(
    reading: SensorReading,
    channel: str,
    correction: str,
    *,
    disagreement_min_diff_ugm3: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    disagreement_min_pct: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
) -> float | None:
    """Return the µg/m³ value the AQI is derived from for this entity.

    - `raw`: the channel's `pm2_5_atm` (with the primary-disagreement
      fallback applied for the primary channel).
    - `aqandu` / `lrapa` / `epa`: the channel's `pm2_5_cf_1` fed
      through the respective correction formula. EPA additionally
      needs relative humidity; if no BME is present the entity has
      no input to correct and we return None (the entity reports
      `unknown`).

    Returning the µg/m³ intermediate (rather than going straight to
    the AQI integer) lets `extra_state_attributes` share the same
    derivation as `native_value`, so the colour and the number can't
    drift out of sync.
    """
    if correction == AQI_CORRECTION_RAW:
        return _channel_atm(
            reading,
            channel,
            disagreement_min_diff_ugm3=disagreement_min_diff_ugm3,
            disagreement_min_pct=disagreement_min_pct,
        )
    cf1 = _channel_cf1(
        reading,
        channel,
        disagreement_min_diff_ugm3=disagreement_min_diff_ugm3,
        disagreement_min_pct=disagreement_min_pct,
    )
    if cf1 is None:
        return None
    if correction == AQI_CORRECTION_AQANDU:
        return correct_aqandu(cf1)
    if correction == AQI_CORRECTION_LRAPA:
        return correct_lrapa(cf1)
    if correction == AQI_CORRECTION_EPA:
        rh = (
            reading.environment.humidity_pct
            if reading.environment is not None
            else None
        )
        if rh is None:
            return None
        return correct_epa(cf1, rh)
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
        disagreement_min_diff_ugm3: float,
        disagreement_min_pct: float,
        color_scheme: str,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._correction = correction
        self._disagreement_min_diff_ugm3 = disagreement_min_diff_ugm3
        self._disagreement_min_pct = disagreement_min_pct
        self._color_scheme = color_scheme
        self._attr_unique_id = (
            f"{self._sensor_id}_{channel}_aqi_{correction}"
        )
        self._attr_name = _label_with_channel(
            _AQI_LABELS[correction], channel
        )

    def _corrected_pm(self) -> float | None:
        return _aqi_corrected_pm(
            self.coordinator.data,
            self._channel,
            self._correction,
            disagreement_min_diff_ugm3=self._disagreement_min_diff_ugm3,
            disagreement_min_pct=self._disagreement_min_pct,
        )

    @property
    def native_value(self) -> int | None:
        pm = self._corrected_pm()
        return pm25_to_aqi(pm) if pm is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Per-poll category + colour for dashboard cards.

        Same `_corrected_pm()` the state value uses, so the colour
        and the AQI integer always describe the same air. Card
        templates can read `state_attr(entity, 'category_color')` to
        drive an icon colour with one line.
        """
        band = aqi_band(self._corrected_pm(), scheme=self._color_scheme)
        if band is None:
            return None
        category, color = band
        return {"category": category, "category_color": color}


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


def _primary_count(
    reading: SensorReading,
    field: str,
    *,
    disagreement_min_diff_ugm3: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    disagreement_min_pct: float = DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
) -> float | None:
    """Primary particle-count value with the same A/B-disagreement
    fallback as `_channel_mass`. When disagreement is signalled by
    PM2.5 ATM the laser is unreliable across all six size bins, not
    just the PM2.5 bucket, so the same lower-of-two preference applies.
    """
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
    pm25_a = reading.channel_a.pm2_5_atm
    pm25_b = reading.channel_b.pm2_5_atm
    if pm25_a is not None and pm25_b is not None and channels_disagree(
        pm25_a,
        pm25_b,
        min_diff_ugm3=disagreement_min_diff_ugm3,
        min_pct=disagreement_min_pct,
    ):
        return min(a, b)
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
        disagreement_min_diff_ugm3: float,
        disagreement_min_pct: float,
    ) -> None:
        super().__init__(coordinator)
        self._field = field
        self._disagreement_min_diff_ugm3 = disagreement_min_diff_ugm3
        self._disagreement_min_pct = disagreement_min_pct
        self._attr_unique_id = f"{self._sensor_id}_primary_count_{field}"
        self._attr_name = short_name

    @property
    def native_value(self) -> float | None:
        return _primary_count(
            self.coordinator.data,
            self._field,
            disagreement_min_diff_ugm3=self._disagreement_min_diff_ugm3,
            disagreement_min_pct=self._disagreement_min_pct,
        )


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
        device_class: SensorDeviceClass | None,
        unit: str,
        precision: int = 1,
    ) -> None:
        super().__init__(coordinator)
        self._env_field = env_field
        if device_class is not None:
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

    # Pull disagreement thresholds once. The per-channel A / B
    # entities ignore these (they always return their own value),
    # but we pass uniformly to all PM / AQI / particle entities so
    # the constructor signature is the same regardless of channel.
    disagreement_min_diff_ugm3 = options.get(
        CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
        DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    )
    disagreement_min_pct = options.get(
        CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
        DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    )
    color_scheme = options.get(
        CONF_AQI_COLOR_SCHEME, DEFAULT_AQI_COLOR_SCHEME
    )

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
                    disagreement_min_diff_ugm3=disagreement_min_diff_ugm3,
                    disagreement_min_pct=disagreement_min_pct,
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
                    coordinator,
                    channel=channel,
                    correction=correction,
                    disagreement_min_diff_ugm3=disagreement_min_diff_ugm3,
                    disagreement_min_pct=disagreement_min_pct,
                    color_scheme=color_scheme,
                )
            )

    # Particle counts (primary only, disabled by default)
    for short_name, field in _PARTICLE_BINS:
        entities.append(
            _ParticleCountEntity(
                coordinator,
                short_name=short_name,
                field=field,
                disagreement_min_diff_ugm3=disagreement_min_diff_ugm3,
                disagreement_min_pct=disagreement_min_pct,
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
                    # No HA device class fits gas-sensor resistance (Ω);
                    # leave it unset so HA doesn't try to convert units.
                    device_class=None,
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
