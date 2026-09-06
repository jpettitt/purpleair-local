"""DataUpdateCoordinator for a single PurpleAir sensor.

One coordinator per sensor IP. Keeping them separate means a failing
sensor never stalls reads on the healthy ones, and each can have its
own scan interval if the options flow ever exposes per-sensor tuning.

A config entry may run *two* of these against the same sensor: the
averaged one (`GET /json`, the canonical series) and, when the user
enables live entities, a second one on `?live=true` at a shorter
interval. They are deliberately independent — a live-endpoint failure
must not blank the averaged entities, so only the averaged coordinator
gates entry setup.

Errors from the HTTP layer (`PurpleAirError` subclasses) and parse
errors (`ValueError` from `SensorReading.from_payload`) both get
translated to HA's standard `UpdateFailed` so entities transition to
`unavailable` after one missed cycle without us reinventing that
machinery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    PurpleAirClient,
    PurpleAirConnectionError,
    PurpleAirError,
    PurpleAirTimeoutError,
)
from .const import DEFAULT_SCAN_INTERVAL_S, DOMAIN, LIVE_FAILURE_GRACE_S
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
        live: bool = False,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            # The suffix keeps the two coordinators for one sensor
            # distinguishable in debug logs and in HA's own scheduler
            # bookkeeping.
            name=f"{DOMAIN} {client.host}{' live' if live else ''}",
            update_interval=timedelta(seconds=scan_interval_s),
        )
        self.client = client
        self.live = live
        # How many consecutive transient failures the live coordinator
        # rides out before giving up and marking the update failed. Sized
        # so the grace period always exceeds LIVE_FAILURE_GRACE_S: at a
        # 15 s interval that's 5 polls (75 s), at 37 s it's 2 (74 s).
        # Zero for the averaged coordinator — it fails fast.
        self.max_consecutive_failures = (
            (LIVE_FAILURE_GRACE_S // scan_interval_s) + 1 if live else 0
        )
        self._consecutive_failures = 0
        # Kept around between polls so the diagnostics download can
        # include the exact bytes the sensor returned, not just the
        # parsed dataclass — useful when a bug report concerns a
        # firmware quirk the parser hasn't accounted for. None until
        # the first successful parse.
        self.last_raw_payload: dict[str, Any] | None = None

    async def _async_update_data(self) -> SensorReading:
        try:
            payload = await self.client.get_reading(live=self.live)
        except (PurpleAirConnectionError, PurpleAirTimeoutError) as err:
            # Transient by nature, and on the live endpoint frequently
            # just the sensor's periodic block window rather than a
            # fault. Serve the previous reading until the grace period
            # is exhausted so entities don't flap to `unavailable`
            # mid-automation. Requires a previous reading to serve;
            # without one there is nothing to fall back to.
            self._consecutive_failures += 1
            if (
                self._consecutive_failures <= self.max_consecutive_failures
                and self.data is not None
            ):
                _LOGGER.debug(
                    "purpleair %s: transient failure %d of %d, serving the "
                    "previous reading: %s",
                    self.name,
                    self._consecutive_failures,
                    self.max_consecutive_failures,
                    err,
                )
                return self.data
            raise UpdateFailed(
                f"could not fetch reading from {self.client.host}: {err}"
            ) from err
        except PurpleAirError as err:
            # Invalid response — a persistent state (wrong host, sensor
            # in a weird mode), so no point riding it out.
            self._consecutive_failures += 1
            raise UpdateFailed(
                f"could not fetch reading from {self.client.host}: {err}"
            ) from err
        try:
            reading = SensorReading.from_payload(payload)
        except ValueError as err:
            # Reached when the sensor responds but the JSON lacks a
            # SensorId — almost certainly a firmware bug, not a network
            # issue, so logging the host helps narrow it down.
            raise UpdateFailed(
                f"malformed payload from {self.client.host}: {err}"
            ) from err
        self._consecutive_failures = 0
        self.last_raw_payload = payload
        return reading


@dataclass(slots=True)
class PurpleAirRuntime:
    """Everything one config entry owns, stored in `hass.data`.

    `live` is None unless the user enabled live entities — and also
    when the live coordinator's first poll failed, since entity
    construction reads `coordinator.data`. Averaged is never None: its
    first refresh gates entry setup.
    """

    averaged: PurpleAirCoordinator
    live: PurpleAirCoordinator | None = None
