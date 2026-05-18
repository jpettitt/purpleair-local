"""Shared base entity for all PurpleAir Local platforms.

`PurpleAirEntity` carries the bits every entity needs: the coordinator
reference (via `CoordinatorEntity`), the device-registry wiring keyed
on the sensor's MAC, and the human-readable device label. Splitting it
out of `sensor.py` lets `binary_sensor.py` (and any future platform)
inherit without a sibling-module import.
"""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PurpleAirCoordinator
from .models import SensorReading


class PurpleAirEntity(CoordinatorEntity[PurpleAirCoordinator]):
    """Common device wiring shared by every PurpleAir entity.

    `_attr_has_entity_name = True` lets HA render entities as
    "<device name> · <entity name>" in the UI, so subclasses only set
    the short entity-specific name on `_attr_name`.

    The MAC connection (`(CONNECTION_NETWORK_MAC, sensor_id)`) is what
    lets HA dedupe this device against any other integration describing
    the same physical PurpleAir.
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
    """Build a human-readable model string from `hardware_discovered`.

    E.g. "2.0+BME280+PMSX003-A" → "PA-II (BME280, PMSX003-A) v2.0".
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
