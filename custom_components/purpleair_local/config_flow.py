"""Config flow for PurpleAir Local.

Single-step user flow: collect a host, probe it once, derive a friendly
name from the sensor's own `place` field plus the last four characters
of its MAC. The MAC (returned as `SensorId`) is the config entry's
unique ID, so DHCP IP changes don't create duplicates — re-adding the
same physical sensor under a new IP updates the existing entry's host
in place (the options flow will offer this same recovery without
forcing a re-add).
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers import aiohttp_client

from .api import (
    PurpleAirClient,
    PurpleAirConnectionError,
    PurpleAirInvalidResponseError,
    PurpleAirTimeoutError,
)
from .const import DOMAIN
from .models import Place, SensorReading

_LOGGER = logging.getLogger(__name__)


STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)


class PurpleAirConfigFlow(ConfigFlow, domain=DOMAIN):
    """User-initiated config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            try:
                reading = await self._probe(host)
            except _CannotConnect:
                errors["base"] = "cannot_connect"
            except _InvalidResponse:
                errors["base"] = "invalid_response"
            else:
                # SensorId (MAC) is the canonical identifier for this
                # physical device. If the user re-adds the same sensor
                # after its DHCP IP changed, _abort_if_unique_id_configured
                # will update the existing entry's host instead of
                # creating a duplicate.
                await self.async_set_unique_id(reading.sensor_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})

                return self.async_create_entry(
                    title=_derive_title(reading),
                    data={CONF_HOST: host},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def _probe(self, host: str) -> SensorReading:
        """Make one call to the sensor and parse the response.

        Maps the api layer's three exception classes onto the two
        user-facing error keys the flow displays. Parse errors (missing
        SensorId) are treated as invalid responses because from the
        user's perspective "the device at that address isn't a working
        PurpleAir" is the same outcome.
        """
        session = aiohttp_client.async_get_clientsession(self.hass)
        client = PurpleAirClient(host, session)
        try:
            payload = await client.get_reading()
        except (PurpleAirConnectionError, PurpleAirTimeoutError) as err:
            _LOGGER.debug("config flow probe of %s failed: %s", host, err)
            raise _CannotConnect from err
        except PurpleAirInvalidResponseError as err:
            _LOGGER.debug("config flow probe of %s got bad response: %s", host, err)
            raise _InvalidResponse from err
        try:
            return SensorReading.from_payload(payload)
        except ValueError as err:
            _LOGGER.debug("config flow probe of %s parsed badly: %s", host, err)
            raise _InvalidResponse from err


def _normalize_host(raw: str) -> str:
    """Strip whitespace, scheme, and trailing slashes a user might paste.

    Users commonly paste the URL from their browser ("http://192.168.1.42/")
    rather than the bare host. Be forgiving so they don't get a
    cannot_connect error for a UX papercut.
    """
    host = raw.strip()
    for scheme in ("http://", "https://"):
        if host.lower().startswith(scheme):
            host = host[len(scheme) :]
            break
    return host.rstrip("/")


def _derive_title(reading: SensorReading) -> str:
    """Build a human-friendly entry title from sensor metadata.

    Format: "<Place> <last-4-of-MAC>" — e.g. "Indoor e7fc". Matches the
    naming convention the sensor uses for its own WiFi setup SSID
    ("PurpleAir-e7fc"), so the entry will look familiar to anyone who
    set the device up.
    """
    place_label = {
        Place.INSIDE: "Indoor",
        Place.OUTSIDE: "Outdoor",
        Place.UNKNOWN: "PurpleAir",
    }[reading.place]
    suffix = reading.sensor_id.replace(":", "")[-4:]
    return f"{place_label} {suffix}"


class _CannotConnect(Exception):
    """Internal sentinel for the config flow error mapping."""


class _InvalidResponse(Exception):
    """Internal sentinel for the config flow error mapping."""
