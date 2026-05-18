"""PurpleAir Local — Home Assistant custom integration.

Setup is minimal until the entity platforms land (sensor, binary_sensor,
diagnostics). For now the integration accepts a config entry, marks it
loaded, and tears down cleanly — enough for the config flow to round-trip
through HA's setup machinery in tests and in real installs.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
