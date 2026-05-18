"""Shared test fixtures.

Adds the repo root to sys.path so tests can import the integration via
`from custom_components.purpleair_local import ...` without installing the
package. This is the same trick HA core uses for its own custom-component
tests.

Loads `pytest_homeassistant_custom_component` as a pytest plugin so
coordinator tests can use the `hass` fixture (a real HomeAssistant
instance running on a test event loop).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest_plugins = ("pytest_homeassistant_custom_component",)

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """HA test harness refuses to load custom integrations without this."""
    yield


@pytest.fixture
def indoor_payload() -> dict:
    """The redacted single-laser indoor capture, as a dict."""
    return json.loads((_FIXTURE_DIR / "pa2_indoor_single_laser.json").read_text())


@pytest.fixture
def outdoor_payload() -> dict:
    """The redacted dual-laser outdoor capture, as a dict."""
    return json.loads((_FIXTURE_DIR / "pa2_outdoor_dual_laser.json").read_text())
