"""Settings → Her browser and the view's routes, through the real server, without
Chromium: behind the token, calm errors, and nothing approved but a shown card."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.api.test_server import ORIGIN, home, make
from trillion.browser.browse import service_of

__all__ = ["home", "make"]


def test_the_browse_tool_connects_itself(make: Any) -> None:
    app = make(model=False)
    assert service_of(app.rt) is not None
    assert app.rt.registry.definition("browse") is not None


def test_the_state_needs_the_token_and_starts_with_everything_off(make: Any) -> None:
    app = make(model=False)
    assert app.client.get("/api/browser").status_code == 401
    body = app.client.get("/api/browser", headers=app.auth).json()
    assert body["installed"] is False
    assert body["settings"] == {
        "act": False,
        "stronger": False,
        "search": "bing",
        "live": True,
    }
    assert "isn't a virtual machine" in body["about"]
    assert body["tasks"] == [] and body["sites"] == [] and body["free"] is None


def test_settings_take_only_known_values(make: Any) -> None:
    app = make(model=False)
    put = {**app.auth, "content-type": "application/json"}
    r = app.client.put(
        "/api/browser/settings",
        json={"act": True, "search": "nowhere", "stronger": "yes", "extra": 1},
        headers=put,
    )
    assert r.status_code == 200
    assert r.json()["settings"] == {
        "act": True,
        "stronger": False,
        "search": "bing",
        "live": True,
    }


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/browser/tasks/abcdef0123456789/asks/x", {"answer": "allowed"}),
        ("post", "/api/browser/tasks/abcdef0123456789/cards/p/shown", None),
        ("post", "/api/browser/tasks/abcdef0123456789/cards/p", {"decision": "confirm"}),
        ("post", "/api/browser/tasks/abcdef0123456789/control", {"take": True}),
        ("post", "/api/browser/tasks/abcdef0123456789/stop", None),
        ("post", "/api/browser/tasks/free/keep", None),
        ("delete", "/api/browser/sites/library.org", None),
    ],
)
def test_nothing_waiting_is_a_calm_refusal(make: Any, method: str, path: str, body: Any) -> None:
    app = make(model=False)
    headers = {**app.auth, "content-type": "application/json"}
    if method == "delete":
        r = app.client.delete(path, headers=headers)
    else:
        r = app.client.post(path, headers=headers, json=body or {})
    assert r.status_code in (404, 409)
    error = r.json()["error"]
    assert isinstance(error, str) and "Traceback" not in error and len(error) < 120


def test_opening_her_browser_before_setting_it_up_says_so(make: Any) -> None:
    app = make(model=False)
    r = app.client.post(
        "/api/browser/free", headers={**app.auth, "content-type": "application/json"}, json={}
    )
    assert r.status_code == 409
    error = r.json()["error"]
    assert "set up" in error or "--extra browser" in error


def test_the_live_picture_needs_the_token(make: Any) -> None:
    app = make(model=False)
    with app.client.websocket_connect("/ws/browser", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": "wrong", "protocol": 1})
        with pytest.raises(WebSocketDisconnect):
            for _ in range(3):
                ws.receive_json()
    with app.client.websocket_connect("/ws/browser", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": app.rt.token, "protocol": 1})
        assert ws.receive_json() == {"type": "browser.ready"}
        # Watching nothing that runs is nothing.
        ws.send_json({"type": "watch", "task": "abcdef0123456789"})
        ws.send_json({"type": "input", "task": "free", "kind": "text", "text": "x"})
