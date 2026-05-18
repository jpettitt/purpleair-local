"""PurpleAir Local — Home Assistant custom integration.

Setup is minimal until the entity platforms land (sensor, binary_sensor,
diagnostics). For now the integration accepts a config entry, marks it
loaded, registers an options-update listener that reloads the entry so
host / interval / threshold changes take effect immediately, and tears
down cleanly.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options or data change.

    The options flow updates `entry.data[CONF_HOST]` when the user
    changes the IP and `entry.options` for everything else; either
    needs the integration to pick up the new values, which a reload
    is the simplest way to do.
    """
    await hass.config_entries.async_reload(entry.entry_id)
