"""Tests for the PurpleAir local HTTP client."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from custom_components.purpleair_local.api import (
    PurpleAirClient,
    PurpleAirConnectionError,
    PurpleAirInvalidResponseError,
    PurpleAirTimeoutError,
)

# pytest-homeassistant-custom-component installs an autouse fixture that
# disables raw socket use. Our api tests spin up real aiohttp servers on
# localhost (the most faithful way to test an HTTP client), so we
# re-enable sockets for this whole module via the `socket_enabled`
# fixture from pytest-socket.
@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled):
    yield


# --- helpers ---------------------------------------------------------------


def _make_handler(
    payload: dict[str, Any],
    *,
    fail_first: int = 0,
    delay_first: float | None = None,
    status: int = 200,
    body_override: str | None = None,
    content_type: str = "text/html",
) -> tuple[Any, dict[str, int]]:
    """Build an aiohttp handler that returns `payload` as JSON-in-text.

    fail_first: drop the connection on the first N calls before responding
        normally. Used to exercise the client's single retry.
    delay_first: sleep this long on the first call before responding. Used
        to exercise the client's timeout behavior.
    status: HTTP status code to return.
    body_override: if set, return this string as the body instead of the
        serialized payload (used to test the non-JSON path).
    content_type: PurpleAir firmware actually sends text/html for /json,
        so we default to that to keep tests faithful to reality.
    """
    counters = {"calls": 0, "live_calls": 0}

    async def handler(request: web.Request) -> web.Response:
        counters["calls"] += 1
        if request.query.get("live") == "true":
            counters["live_calls"] += 1

        if counters["calls"] <= fail_first:
            # Hard-drop the connection so the client sees a ClientError.
            raise web.HTTPException()  # pragma: no cover - replaced below

        if delay_first is not None and counters["calls"] == 1:
            await asyncio.sleep(delay_first)

        body = body_override if body_override is not None else json.dumps(payload)
        return web.Response(status=status, text=body, content_type=content_type)

    return handler, counters


def _drop_handler(counters: dict[str, int], fail_first: int, payload: dict):
    """Variant handler that drops the connection on the first N calls.

    Raising a ClientError mid-handler doesn't surface as a connection
    error to the client, so we forcibly close the underlying transport
    instead. The client should see this as `ClientConnectionError`.
    """

    async def handler(request: web.Request) -> web.Response:
        counters["calls"] += 1
        if counters["calls"] <= fail_first:
            # Closing the transport before responding gives the client a
            # ClientConnectionError, which is what we want to test the
            # retry path.
            request.transport.close()
            return web.Response()  # never reached by client
        return web.Response(
            status=200, text=json.dumps(payload), content_type="text/html"
        )

    return handler


# --- tests -----------------------------------------------------------------


async def test_get_reading_happy_path(aiohttp_server, outdoor_payload):
    handler, counters = _make_handler(outdoor_payload)
    app = web.Application()
    app.router.add_get("/json", handler)
    server = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        client = PurpleAirClient(f"127.0.0.1:{server.port}", session)
        result = await client.get_reading()

    assert result["SensorId"] == outdoor_payload["SensorId"]
    # The firmware-quirk field name (literal dot) must round-trip.
    assert "pm2.5_aqi" in result
    assert counters["calls"] == 1
    assert counters["live_calls"] == 0


async def test_get_reading_live_sets_query(aiohttp_server, indoor_payload):
    handler, counters = _make_handler(indoor_payload)
    app = web.Application()
    app.router.add_get("/json", handler)
    server = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        client = PurpleAirClient(f"127.0.0.1:{server.port}", session)
        result = await client.get_reading(live=True)

    assert result["SensorId"] == indoor_payload["SensorId"]
    assert counters["live_calls"] == 1


async def test_http_error_raises_invalid_response_no_retry(
    aiohttp_server, indoor_payload
):
    handler, counters = _make_handler(indoor_payload, status=500)
    app = web.Application()
    app.router.add_get("/json", handler)
    server = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        client = PurpleAirClient(f"127.0.0.1:{server.port}", session)
        with pytest.raises(PurpleAirInvalidResponseError, match="HTTP 500"):
            await client.get_reading()

    # No retry on persistent errors.
    assert counters["calls"] == 1


async def test_non_json_body_raises_invalid_response(
    aiohttp_server, indoor_payload
):
    handler, _ = _make_handler(
        indoor_payload, body_override="<html>not json</html>"
    )
    app = web.Application()
    app.router.add_get("/json", handler)
    server = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        client = PurpleAirClient(f"127.0.0.1:{server.port}", session)
        with pytest.raises(PurpleAirInvalidResponseError, match="non-JSON"):
            await client.get_reading()


async def test_non_object_json_raises_invalid_response(aiohttp_server):
    # A bare JSON array is valid JSON but not what the sensor would ever
    # return; the client should reject it rather than hand a list to the
    # parser layer.
    handler, _ = _make_handler({}, body_override="[1, 2, 3]")
    app = web.Application()
    app.router.add_get("/json", handler)
    server = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        client = PurpleAirClient(f"127.0.0.1:{server.port}", session)
        with pytest.raises(PurpleAirInvalidResponseError, match="unexpected JSON"):
            await client.get_reading()


async def test_timeout_is_retried_then_raises(aiohttp_server, indoor_payload):
    handler, counters = _make_handler(
        indoor_payload, delay_first=0.5
    )
    app = web.Application()
    app.router.add_get("/json", handler)
    server = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        # 0.1s timeout against a 0.5s delay forces a timeout on attempt 1.
        # Attempt 2 succeeds because the handler only delays the first call.
        client = PurpleAirClient(
            f"127.0.0.1:{server.port}", session, timeout=0.1
        )
        result = await client.get_reading()

    assert result["SensorId"] == indoor_payload["SensorId"]
    assert counters["calls"] == 2


async def test_timeout_on_both_attempts_raises(aiohttp_server, indoor_payload):
    # No delay_first cap — every call sleeps long enough to time out.
    async def handler(_request: web.Request) -> web.Response:
        await asyncio.sleep(1.0)
        return web.Response(
            text=json.dumps(indoor_payload), content_type="text/html"
        )

    app = web.Application()
    app.router.add_get("/json", handler)
    server = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        client = PurpleAirClient(
            f"127.0.0.1:{server.port}", session, timeout=0.1
        )
        with pytest.raises(PurpleAirTimeoutError):
            await client.get_reading()


async def test_connection_refused_raises_connection_error():
    # Port 1 is reserved and not listening; aiohttp should raise
    # ClientConnectorError, which we wrap as PurpleAirConnectionError.
    async with aiohttp.ClientSession() as session:
        client = PurpleAirClient("127.0.0.1:1", session, timeout=2.0)
        with pytest.raises(PurpleAirConnectionError):
            await client.get_reading()


async def test_dropped_connection_is_retried(aiohttp_server, indoor_payload):
    counters = {"calls": 0}
    handler = _drop_handler(counters, fail_first=1, payload=indoor_payload)
    app = web.Application()
    app.router.add_get("/json", handler)
    server = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        client = PurpleAirClient(f"127.0.0.1:{server.port}", session)
        result = await client.get_reading()

    assert result["SensorId"] == indoor_payload["SensorId"]
    assert counters["calls"] == 2
