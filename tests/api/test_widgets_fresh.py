"""Settings: "Keep widgets fresh" (the live widgets spec), on unless turned off."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401
from trillion.api.runtime import OWNER, WIDGETS_FRESH_PREF, Runtime, open_runtime

PATH = "/api/settings/workspace"


def test_on_by_default_and_off_survives_a_restart(make: Any, home: Path) -> None:
    app: App = make(model=False)
    assert app.client.get(PATH, headers=app.auth).json() == {"widgets_fresh": True}
    off = app.client.put(PATH, json={"widgets_fresh": False}, headers=app.auth)
    assert off.status_code == 200 and off.json() == {"widgets_fresh": False}
    assert app.rt.widgets_fresh is False
    assert app.client.get(PATH, headers=app.auth).json() == {"widgets_fresh": False}

    again: Runtime = asyncio.run(open_runtime(secrets=app.secrets, env={}, persona=home))
    try:
        assert again.widgets_fresh is False
        # Anything that is not a saved "off" means on.
        asyncio.run(again.preferences.put(OWNER, WIDGETS_FRESH_PREF, "x", again.clock()))
    finally:
        asyncio.run(again.close())
    third: Runtime = asyncio.run(open_runtime(secrets=app.secrets, env={}, persona=home))
    try:
        assert third.widgets_fresh is True
    finally:
        asyncio.run(third.close())


def test_only_a_true_or_false_is_taken(make: Any) -> None:
    app: App = make(model=False)
    for body in ({"widgets_fresh": "no"}, {}, {"widgets_fresh": True, "x": 1}):
        r = app.client.put(PATH, json=body, headers=app.auth)
        assert r.status_code == 422, body
    assert app.client.get(PATH).status_code in (401, 403)
