"""Async HTTP client for a PurpleAir sensor's local /json endpoint.

The sensor exposes plain HTTP on port 80 with no auth. Two query shapes
are supported:

  GET /json              — two-minute averaged reading (matches what the
                           sensor uploads to the PurpleAir map).
  GET /json?live=true    — latest raw reading, no averaging. Noisier.

PurpleAir's docs ask callers not to poll faster than once every 10 s.

The client surfaces three error classes so callers (coordinator, config
flow) can distinguish transient from persistent problems:

  - PurpleAirConnectionError: network-layer failure (DNS, refused, reset).
  - PurpleAirTimeoutError:    no response within the request timeout.
  - PurpleAirInvalidResponseError: HTTP status >=400, or body wasn't JSON.

Connection and timeout errors are retried once inside the client because
they're commonly transient on a residential LAN; an invalid response is
not retried because it almost always reflects a persistent state (wrong
host, wrong port, sensor in a weird mode).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from .const import DEFAULT_REQUEST_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)

# How many bytes of the response body to include in error messages when
# JSON parsing fails. Enough to identify what came back without flooding
# logs if a sensor decides to return a multi-KB error page.
_BODY_PEEK_BYTES = 200


class PurpleAirError(Exception):
    """Base exception for all PurpleAir client errors."""


class PurpleAirConnectionError(PurpleAirError):
    """The sensor could not be reached (network error, refused, reset)."""


class PurpleAirTimeoutError(PurpleAirError):
    """The sensor did not respond within the request timeout."""


class PurpleAirInvalidResponseError(PurpleAirError):
    """The sensor responded with a non-2xx status or non-JSON body."""


class PurpleAirClient:
    """Thin async client for a single sensor's local HTTP API.

    The client does not own the aiohttp session. In Home Assistant we
    pass in the shared session from `aiohttp_client.async_get_clientsession`
    so that lifecycle, connection pooling, and proxy config are handled
    by the platform.
    """

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        self._host = host
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def host(self) -> str:
        """The host (IP or hostname) this client talks to."""
        return self._host

    async def get_reading(self, *, live: bool = False) -> dict[str, Any]:
        """Fetch one reading from the sensor.

        Args:
            live: when True, request `/json?live=true` (raw latest reading).
                Defaults to False (two-minute averaged).

        Returns:
            The parsed JSON payload as a dict.

        Raises:
            PurpleAirConnectionError: network error reaching the sensor,
                after one retry.
            PurpleAirTimeoutError: request exceeded the configured timeout,
                after one retry.
            PurpleAirInvalidResponseError: HTTP status was >=400 or body
                wasn't valid JSON. Not retried.
        """
        last_transient: PurpleAirError | None = None
        for attempt in (1, 2):
            try:
                return await self._fetch_once(live=live)
            except (PurpleAirConnectionError, PurpleAirTimeoutError) as err:
                last_transient = err
                if attempt == 1:
                    _LOGGER.debug(
                        "purpleair %s: transient error on attempt 1, retrying: %s",
                        self._host,
                        err,
                    )
                    continue
                raise
        # Loop always either returns or raises; this is here so type-checkers
        # don't worry about a missing return.
        assert last_transient is not None
        raise last_transient

    async def _fetch_once(self, *, live: bool) -> dict[str, Any]:
        url = f"http://{self._host}/json"
        params = {"live": "true"} if live else None

        try:
            async with self._session.get(
                url, params=params, timeout=self._timeout
            ) as resp:
                if resp.status >= 400:
                    raise PurpleAirInvalidResponseError(
                        f"{self._host} returned HTTP {resp.status}"
                    )
                # Some firmware sends Content-Type: text/html for /json,
                # so we read as text and parse manually rather than trusting
                # resp.json()'s content-type check.
                body = await resp.text()
        except asyncio.TimeoutError as err:
            raise PurpleAirTimeoutError(
                f"timed out waiting for {self._host}"
            ) from err
        except aiohttp.ClientError as err:
            raise PurpleAirConnectionError(
                f"could not reach {self._host}: {err}"
            ) from err

        try:
            payload = json.loads(body)
        except ValueError as err:
            peek = body[:_BODY_PEEK_BYTES]
            raise PurpleAirInvalidResponseError(
                f"non-JSON response from {self._host}: {peek!r}"
            ) from err

        if not isinstance(payload, dict):
            raise PurpleAirInvalidResponseError(
                f"unexpected JSON shape from {self._host}: "
                f"got {type(payload).__name__}, expected object"
            )
        return payload
