"""PurpleAir Local — Home Assistant custom integration.

On `async_setup_entry`:
  1. Build a `PurpleAirClient` over HA's shared aiohttp session.
  2. Wrap it in a `PurpleAirCoordinator` with the user's configured
     scan interval (or the default).
  3. Run an initial refresh so platforms have data to build entities
     from. If that fails we raise `ConfigEntryNotReady` and HA
     retries setup later.
  4. Stash the coordinator under `hass.data[DOMAIN][entry_id]` so
     `sensor.py`'s setup can pick it up.
  5. Forward setup to the sensor platform.
  6. Register an options-update listener so reconfiguration via the
     options flow reloads the entry and the coordinator is rebuilt
     with the new host / interval.

Entity unique_ids are derived from the sensor's MAC (SensorId) — the
host can change (DHCP) without breaking entity identity.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .api import PurpleAirClient
from .const import (
    CONF_SCAN_INTERVAL_S,
    DEFAULT_SCAN_INTERVAL_S,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import PurpleAirCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = aiohttp_client.async_get_clientsession(hass)
    client = PurpleAirClient(entry.data[CONF_HOST], session)

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL_S, DEFAULT_SCAN_INTERVAL_S
    )
    coordinator = PurpleAirCoordinator(
        hass, client, config_entry=entry, scan_interval_s=scan_interval
    )

    # Surfaces a ConfigEntryNotReady (HA will retry) on any failure,
    # rather than entering the loaded state with no data.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rebuild the integration after host / interval / threshold edits."""
    await hass.config_entries.async_reload(entry.entry_id)
