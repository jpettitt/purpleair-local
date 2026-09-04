"""Diagnostics payload for the PurpleAir Local integration.

Returned when a user hits "Download diagnostics" on the integration's
device or config-entry page. Includes:

  - The config entry (data + options + unique_id), redacted.
  - The coordinator's current health (last_update_success,
    last_exception, scan interval).
  - The most recent **raw** /json payload (redacted) — more useful in
    bug reports than the parsed dataclass because it preserves any
    firmware fields we haven't accounted for.
  - The same two for the live (`?live=true`) coordinator, or null when
    live entities are disabled.

Redaction
---------
We strip fields that identify the user or their network: the host
(LAN IP), the SensorId (MAC), the device's `Geo` SSID-ish label,
lat/lon, and the WiFi SSID. Everything else stays so we can debug
firmware quirks and field-presence questions.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import PurpleAirCoordinator, PurpleAirRuntime

# Keys to scrub anywhere they appear in the diagnostics tree. The set
# spans both our own config-entry shape (`host`) and the firmware's
# field names in the raw payload (`SensorId`, `lat`, `lon`, `ssid`,
# `Geo` — the last includes the last-4 of MAC).
_TO_REDACT: frozenset[str] = frozenset(
    {CONF_HOST, "SensorId", "Geo", "lat", "lon", "ssid"}
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    runtime: PurpleAirRuntime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.averaged

    # The `coordinator` / `last_raw_payload` keys keep their original
    # meaning (the averaged series) so older bug reports stay
    # comparable; the live pair is null when live entities are off.
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), _TO_REDACT),
            "options": dict(entry.options),
            # The unique_id is the SensorId (MAC) — fully redact rather
            # than retaining structure, since just confirming presence
            # already leaks the fact that a given MAC is registered.
            "unique_id": "**REDACTED**" if entry.unique_id else None,
        },
        "coordinator": _coordinator_health(coordinator),
        "last_raw_payload": _redact_payload(coordinator.last_raw_payload),
        "live_coordinator": (
            _coordinator_health(runtime.live)
            if runtime.live is not None
            else None
        ),
        "live_last_raw_payload": (
            _redact_payload(runtime.live.last_raw_payload)
            if runtime.live is not None
            else None
        ),
    }


def _coordinator_health(
    coordinator: PurpleAirCoordinator,
) -> dict[str, Any]:
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": (
            str(coordinator.last_exception)
            if coordinator.last_exception is not None
            else None
        ),
        "update_interval_s": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval is not None
            else None
        ),
    }


def _redact_payload(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    return async_redact_data(payload, _TO_REDACT)
