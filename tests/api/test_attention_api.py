"""Settings → Speaking up through /api/attention, and the page's host from its hello."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from typing import Any

from tests.api.test_server import ORIGIN, App, Chat, home, make  # noqa: F401
from trillion.attention.probes import Answer
from trillion.attention.settings import DEFAULT

PATH = "/api/attention"


def probes(app: App, mic: Answer, focus: Answer) -> None:
    assert app.rt.quiet is not None
    app.rt.quiet.mic = lambda host: mic
    app.rt.quiet.focus = lambda: focus


def call(app: App, fn: Any, *args: Any) -> Any:
    assert app.client.portal is not None
    return app.client.portal.call(fn, *args)


def test_it_needs_the_session_token(make: Any) -> None:
    app: App = make()
    assert app.client.get(f"{PATH}/settings").status_code == 401
    assert app.client.put(f"{PATH}/settings", json={}).status_code == 401
    assert app.client.get(f"{PATH}/finds").status_code == 401
    assert app.client.post(f"{PATH}/finds/email:1/dismiss").status_code == 401
    assert app.client.delete(f"{PATH}/notes/email").status_code == 401


def test_settings_read_with_their_status(make: Any) -> None:
    app: App = make()
    probes(app, mic=False, focus=None)
    body = app.client.get(f"{PATH}/settings", headers=app.auth).json()
    assert body["settings"] == DEFAULT.to_json()
    watchers = body["status"]["watchers"]
    assert set(watchers) == {"meetings", "email", "payments"}
    assert watchers["email"] == {"last_check_ms": None, "last_error": None, "notes": ""}
    assert body["status"]["probes"]["mic"]["status"] == "works"
    focus = body["status"]["probes"]["focus"]
    assert focus["status"] == "can't tell" and focus["why"]


def test_settings_are_saved_and_kept(make: Any) -> None:
    app: App = make()
    probes(app, mic=False, focus=False)
    r = app.client.put(
        f"{PATH}/settings",
        headers=app.auth,
        json={"watchers": {"email": True}, "quiet_hours": {"from": "23:00"}},
    )
    assert r.status_code == 200
    saved = r.json()["settings"]
    assert saved["watchers"]["email"] is True and saved["quiet_hours"]["from"] == "23:00"
    assert app.rt.attention_settings.on("email")
    again = app.client.get(f"{PATH}/settings", headers=app.auth).json()["settings"]
    assert again == saved
    assert call(app, app.rt.preferences.get, "local", "attention") == saved


def test_a_bad_setting_is_refused_with_why(make: Any) -> None:
    app: App = make()
    r = app.client.put(f"{PATH}/settings", headers=app.auth, json={"meeting_lead_min": 60})
    assert r.status_code == 422
    assert r.json()["error"] == "bad_setting" and "meeting_lead_min" in r.json()["why"]
    assert app.rt.attention_settings == DEFAULT


def test_finds_are_listed_and_dismissed(make: Any) -> None:
    app: App = make()
    store = app.rt.attention_store
    assert store is not None
    call(app, store.claim, "local", "email", ["email:m1"], app.rt.clock())
    (row,) = app.client.get(f"{PATH}/finds", headers=app.auth).json()["finds"]
    assert row["key"] == "email:m1" and row["status"] == "silent" and row["watcher"] == "email"
    r = app.client.post(f"{PATH}/finds/email:m1/dismiss", headers=app.auth, json={})
    assert r.status_code == 200
    assert r.json()["finds"][0]["status"] == "dismissed"
    assert (
        app.client.post(f"{PATH}/finds/email:zz/dismiss", headers=app.auth, json={}).status_code
        == 404
    )


def test_a_watchers_notes_are_cleared(make: Any) -> None:
    app: App = make()
    store = app.rt.attention_store
    assert store is not None
    call(app, store.set_notes, "local", "email", "Not the newsletters.")
    probes(app, mic=None, focus=None)
    r = app.client.request("DELETE", f"{PATH}/notes/email", headers=app.auth, json={})
    assert r.status_code == 200
    assert r.json()["status"]["watchers"]["email"]["notes"] == ""
    assert (
        app.client.request("DELETE", f"{PATH}/notes/slack", headers=app.auth, json={}).status_code
        == 404
    )


def test_the_quiet_check_is_the_doors(make: Any) -> None:
    app: App = make()
    assert app.rt.door is not None and app.rt.door.quiet is app.rt.quiet
    assert app.rt.attention is not None


def _hello(app: App, **extra: Any) -> str:
    assert app.rt.door is not None
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": app.rt.token, "protocol": 1, **extra})
        Chat(ws).until("state")
        live = app.rt.door.live()
        assert live is not None and app.rt.quiet is not None
        # The quiet check asks the mic probe about the live page's host.
        assert app.rt.quiet.host() == live.facts().host
        return live.facts().host


def test_the_page_says_where_it_runs_in_its_hello(make: Any) -> None:
    app: App = make()
    assert _hello(app, host="desktop") == "desktop"
    assert _hello(app, host="Firefox") == "firefox"
    assert _hello(app, host="netscape") == "other"
    assert _hello(app, host=7) == "other"
    assert _hello(app) == "other"
