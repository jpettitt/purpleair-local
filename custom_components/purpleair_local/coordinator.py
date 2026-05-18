"""DataUpdateCoordinator for a single PurpleAir sensor.

One coordinator per sensor IP. Keeping them separate means a failing
sensor never stalls reads on the healthy ones, and each can have its
own scan interval if the options flow ever exposes per-sensor tuning.

Errors from the HTTP layer (`PurpleAirError` subclasses) and parse
errors (`ValueError` from `SensorReading.from_payload`) both get
translated to HA's standard `UpdateFailed` so entities transition to
`unavailable` after one missed cycle without us reinventing that
machinery.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import PurpleAirClient, PurpleAirError
from .const import DEFAULT_SCAN_INTERVAL_S, DOMAIN
from .models import SensorReading

_LOGGER = logging.getLogger(__name__)


class PurpleAirCoordinator(DataUpdateCoordinator[SensorReading]):
    """Polls one PurpleAir sensor and surfaces parsed SensorReadings."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: PurpleAirClient,
        *,
        config_entry: ConfigEntry | None = None,
        scan_interval_s: int = DEFAULT_SCAN_INTERVAL_S,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=timedelta(seconds=scan_interval_s),
        )
        self.client = client

    async def _async_update_data(self) -> SensorReading:
        try:
            payload = await self.client.get_reading()
        except PurpleAirError as err:
            # PurpleAirError is the union of connection / timeout /
            # invalid-response; the specific type is already in `err`'s
            # message, no need to re-classify here.
            raise UpdateFailed(
                f"could not fetch reading from {self.client.host}: {err}"
            ) from err
        try:
            return SensorReading.from_payload(payload)
        except ValueError as err:
            # Reached when the sensor responds but the JSON lacks a
            # SensorId — almost certainly a firmware bug, not a network
            # issue, so logging the host helps narrow it down.
            raise UpdateFailed(
                f"malformed payload from {self.client.host}: {err}"
            ) from err
