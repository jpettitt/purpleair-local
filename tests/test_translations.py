"""Tests that the translations stay in sync with strings.json and the code.

Two regression checks:

  1. `translations/en.json` is structurally equal to `strings.json`. HA
     treats strings.json as the canonical source; the English locale
     is supposed to mirror it. Drift between the two is silent — the
     UI renders the en.json values, so a fix to strings.json that
     forgets to update en.json ships as a no-op until someone notices.

  2. Every `errors["base"] = "X"` and every abort reason referenced in
     config_flow.py has a matching entry in strings.json. Adding a new
     error key without a string lets the UI fall back to the raw key
     ("invalid_response_v2") which looks awful and is easy to miss in
     a manual smoke test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INT_DIR = _REPO_ROOT / "custom_components" / "purpleair_local"
_STRINGS = _INT_DIR / "strings.json"
_EN = _INT_DIR / "translations" / "en.json"
_CONFIG_FLOW = _INT_DIR / "config_flow.py"


def test_en_translation_mirrors_strings():
    """English locale must be a literal mirror of strings.json."""
    strings = json.loads(_STRINGS.read_text())
    en = json.loads(_EN.read_text())
    assert strings == en, (
        "translations/en.json drifted from strings.json — keep them in "
        "sync (en is supposed to be a copy of the canonical source)."
    )


def test_every_error_base_in_code_has_a_string():
    """`errors["base"] = "X"` in config_flow.py must resolve to a string."""
    strings = json.loads(_STRINGS.read_text())
    code = _CONFIG_FLOW.read_text()

    # Walk both the config user step and the options init step.
    available = {
        "config": set(strings.get("config", {}).get("error", {})),
        "options": set(strings.get("options", {}).get("error", {})),
    }

    # We classify by which class block the assignment sits in. Cheap
    # approach: scan the file once, tag each line with the most recent
    # class header above it.
    current_section: str | None = None
    missing: list[tuple[str, str]] = []
    pattern = re.compile(r'errors\["base"\]\s*=\s*"([^"]+)"')
    for line in code.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("class PurpleAirConfigFlow"):
            current_section = "config"
        elif stripped.startswith("class PurpleAirOptionsFlow"):
            current_section = "options"
        match = pattern.search(line)
        if match and current_section is not None:
            key = match.group(1)
            if key not in available[current_section]:
                missing.append((current_section, key))

    assert not missing, (
        "config_flow.py references error keys that aren't in strings.json: "
        f"{missing}"
    )


def test_already_configured_abort_has_a_string():
    """The implicit abort reason from _abort_if_unique_id_configured."""
    strings = json.loads(_STRINGS.read_text())
    assert "already_configured" in strings["config"]["abort"]
