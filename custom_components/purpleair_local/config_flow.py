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
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    PurpleAirClient,
    PurpleAirConnectionError,
    PurpleAirInvalidResponseError,
    PurpleAirTimeoutError,
)
from .aqi import (
    AQI_COLOR_SCHEME_EU_EAQI,
    AQI_COLOR_SCHEME_UK_DAQI,
    AQI_COLOR_SCHEME_US_EPA,
    AQI_COLOR_SCHEMES_ALL,
)
from .const import (
    AQI_CORRECTION_AQANDU,
    AQI_CORRECTION_EPA,
    AQI_CORRECTION_LRAPA,
    AQI_CORRECTION_RAW,
    AQI_CORRECTIONS_ALL,
    CONF_AQI_COLOR_SCHEME,
    CONF_AQI_CORRECTIONS,
    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
    CONF_SCAN_INTERVAL_S,
    DEFAULT_AQI_COLOR_SCHEME,
    DEFAULT_AQI_CORRECTIONS,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
    DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
    DEFAULT_SCAN_INTERVAL_S,
    DOMAIN,
    MAX_SCAN_INTERVAL_S,
    MIN_SCAN_INTERVAL_S,
)
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "PurpleAirOptionsFlow":
        return PurpleAirOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            try:
                reading = await _probe(self.hass, host)
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



async def _probe(hass: HomeAssistant, host: str) -> SensorReading:
    """Make one call to the sensor and parse the response.

    Maps the api layer's three exception classes onto the two
    user-facing error keys the flow displays. Parse errors (missing
    SensorId) are treated as invalid responses because from the user's
    perspective "the device at that address isn't a working PurpleAir"
    is the same outcome.
    """
    session = aiohttp_client.async_get_clientsession(hass)
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


# --- options flow ---------------------------------------------------------


_AQI_OPTION_LABELS: tuple[tuple[str, str], ...] = (
    (AQI_CORRECTION_RAW, "Raw (uncorrected)"),
    (AQI_CORRECTION_EPA, "US EPA (Barkjohn 2021)"),
    (AQI_CORRECTION_AQANDU, "AQandU (University of Utah)"),
    (AQI_CORRECTION_LRAPA, "LRAPA (wood-smoke tuned)"),
)


_COLOR_SCHEME_OPTION_LABELS: tuple[tuple[str, str], ...] = (
    (AQI_COLOR_SCHEME_US_EPA, "US EPA (AirNow)"),
    (AQI_COLOR_SCHEME_EU_EAQI, "EU EAQI (European Environment Agency)"),
    (AQI_COLOR_SCHEME_UK_DAQI, "UK DAQI (Defra)"),
)


class PurpleAirOptionsFlow(OptionsFlow):
    """Editable settings for a configured sensor.

    Single-step flow with four fields:
      - Host (validated against the entry's unique SensorId on change,
        so users can recover from DHCP IP changes without removing the
        entry and losing entity history).
      - Scan interval, bounded by [MIN, MAX]_SCAN_INTERVAL_S.
      - Which AQI corrections to expose as entities. Multi-select;
        empty selection is allowed (some users may not want AQI
        entities at all).
      - Channel-disagreement thresholds. A and B are flagged as
        disagreeing only when *both* the absolute and relative
        thresholds are crossed (matches PurpleAir's own rule).
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            new_host = _normalize_host(user_input[CONF_HOST])
            current_host = self.config_entry.data.get(CONF_HOST)
            host_changed = new_host != current_host

            if host_changed:
                try:
                    reading = await _probe(self.hass, new_host)
                except _CannotConnect:
                    errors["base"] = "cannot_connect"
                except _InvalidResponse:
                    errors["base"] = "invalid_response"
                else:
                    # Guard against pointing the entry at a *different*
                    # physical PurpleAir, which would silently rebind
                    # all the entities to wrong data.
                    if reading.sensor_id != self.config_entry.unique_id:
                        errors["base"] = "sensor_mismatch"

            if not errors:
                if host_changed:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_HOST: new_host,
                        },
                    )

                return self.async_create_entry(
                    title="",
                    data={
                        CONF_SCAN_INTERVAL_S: int(
                            user_input[CONF_SCAN_INTERVAL_S]
                        ),
                        CONF_AQI_CORRECTIONS: list(
                            user_input[CONF_AQI_CORRECTIONS]
                        ),
                        CONF_AQI_COLOR_SCHEME: user_input[
                            CONF_AQI_COLOR_SCHEME
                        ],
                        CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3: float(
                            user_input[CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3]
                        ),
                        CONF_CHANNEL_DISAGREEMENT_MIN_PCT: float(
                            user_input[CONF_CHANNEL_DISAGREEMENT_MIN_PCT]
                        ),
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self._schema(user_input),
            errors=errors,
        )

    def _schema(self, user_input: dict[str, Any] | None) -> vol.Schema:
        """Build the form schema with current values as defaults.

        Default resolution order for each field:
        1. The value the user just submitted (so an error doesn't
           wipe their pending edits).
        2. The value already in entry.options (or entry.data for host).
        3. The integration's hard-coded default.
        """
        opts = self.config_entry.options
        data = self.config_entry.data
        cur: dict[str, Any] = user_input or {}

        scan_default = cur.get(
            CONF_SCAN_INTERVAL_S,
            opts.get(CONF_SCAN_INTERVAL_S, DEFAULT_SCAN_INTERVAL_S),
        )
        aqi_default = list(
            cur.get(
                CONF_AQI_CORRECTIONS,
                opts.get(CONF_AQI_CORRECTIONS, list(DEFAULT_AQI_CORRECTIONS)),
            )
        )
        color_scheme_default = cur.get(
            CONF_AQI_COLOR_SCHEME,
            opts.get(CONF_AQI_COLOR_SCHEME, DEFAULT_AQI_COLOR_SCHEME),
        )
        # Defensive: if a future scheme rename leaves a stale value in
        # options, snap back to the default rather than serving an
        # unselectable form.
        if color_scheme_default not in AQI_COLOR_SCHEMES_ALL:
            color_scheme_default = DEFAULT_AQI_COLOR_SCHEME
        diff_default = cur.get(
            CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
            opts.get(
                CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
                DEFAULT_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
            ),
        )
        pct_default = cur.get(
            CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
            opts.get(
                CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
                DEFAULT_CHANNEL_DISAGREEMENT_MIN_PCT,
            ),
        )

        return vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=cur.get(CONF_HOST, data.get(CONF_HOST)),
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL_S,
                    default=scan_default,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_S,
                        max=MAX_SCAN_INTERVAL_S,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_AQI_CORRECTIONS,
                    default=aqi_default,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=v, label=lbl)
                            for v, lbl in _AQI_OPTION_LABELS
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_AQI_COLOR_SCHEME,
                    default=color_scheme_default,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=v, label=lbl)
                            for v, lbl in _COLOR_SCHEME_OPTION_LABELS
                        ],
                        multiple=False,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_CHANNEL_DISAGREEMENT_MIN_DIFF_UGM3,
                    default=diff_default,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=100.0,
                        step=0.1,
                        unit_of_measurement="µg/m³",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_CHANNEL_DISAGREEMENT_MIN_PCT,
                    default=pct_default,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=100.0,
                        step=0.1,
                        unit_of_measurement="%",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
