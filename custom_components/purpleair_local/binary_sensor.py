"""Binary-sensor entities for PurpleAir Local.

Two entities per configured sensor:

  - **Online** — `connectivity` device class. Reflects whether the
    coordinator's most recent poll succeeded. Always created.
  - **Channel disagreement** — `problem` device class. Only created
    for dual-laser units; flips on when the A and B PM2.5 ATM
    readings drift apart by *both* the absolute and relative
    thresholds set in the options flow.

The disagreement rule uses PurpleAir's own data-quality criterion:
``|A − B| ≥ min_diff_ugm3 AND |A − B| / max(A, B) × 100 ≥ min_pct``.
Defaults are 5 µg/m³ and 70 %. Both thresholds must be crossed so the
sensor doesn't flap on small absolute differences at very low
concentrations (a vs. b of 0.1 vs. 0.4 µg/m³ is "75 % different" but
not actually meaningful).
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    DOMAIN,
)
from .coordinator import PurpleAirCoordinator
from .entity import PurpleAirEntity


class _OnlineBinarySensor(PurpleAirEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PurpleAirCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._sensor_id}_diag_online"
        self._attr_name = "Online"

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class _ChannelDisagreementBinarySensor(PurpleAirEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: PurpleAirCoordinator,
        *,
        min_diff_ugm3: float,
        min_pct: float,
    ) -> None:
        super().__init__(coordinator)
        self._min_diff_ugm3 = min_diff_ugm3
        self._min_pct = min_pct
        self._attr_unique_id = f"{self._sensor_id}_channel_disagreement"
        self._attr_name = "Channel disagreement"

    @property
    def is_on(self) -> bool | None:
        reading = self.coordinator.data
        if reading.channel_b is None:
            # Single-laser sensor — shouldn't have this entity at all,
            # but be defensive in case channel B disappears at runtime.
            return None

        a = reading.channel_a.pm2_5_atm
        b = reading.channel_b.pm2_5_atm
        if a is None or b is None:
            return None

        diff = abs(a - b)
        if diff < self._min_diff_ugm3:
            return False

        denom = max(a, b)
        if denom <= 0:
            # diff >= min_diff_ugm3 (typically 5) while max(a,b) is 0
            # is mathematically impossible, but guard anyway.
            return False

        rel_pct = (diff / denom) * 100.0
        return rel_pct >= self._min_pct


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PurpleAirCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [_OnlineBinarySensor(coordinator)]

    if coordinator.data.is_dual_channel:
        options = entry.options
        entities.append(
            _ChannelDisagreementBinarySensor(
                coordinator,
                min_diff_ugm3=options.get(
                    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
                    DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
                ),
                min_pct=options.get(
                    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
                    DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
                ),
            )
        )

    async_add_entities(entities)
