"""Signing out every other page (SPN-11a, the token revocation spec): the old token is
refused everywhere at once, the other sockets are closed, and the page that asked
keeps working with the new one."""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.api.test_server import ORIGIN, App, Chat, home, make, receive
from trillion.api.app import CLOSE_UNAUTHENTICATED

# The server tests' fixtures, used here by name.
FIXTURES = (home, make)


def _rotate(chat: Chat) -> str:
    chat.ws.send_json({"type": "session.rotate"})
    (got,) = [m for m in chat.until("session.token") if m["type"] == "session.token"]
    token = got["token"]
    assert isinstance(token, str) and len(token) >= 32
    return token


def _told_signed_out(ws: Any) -> None:
    # Whatever it was already sent (a ready line, live events) comes first.
    while True:
        message = receive(ws)
        ws._raise_on_close(message)
        body = json.loads(message["text"])
        if body.get("type") == "error":
            break
    assert body == {"type": "error", "code": "signed_out"}
    with pytest.raises(WebSocketDisconnect) as closed:
        ws._raise_on_close(receive(ws))
    assert closed.value.code == CLOSE_UNAUTHENTICATED


def test_the_page_that_asks_gets_a_new_token_and_the_old_one_is_refused(make: Any) -> None:
    app: App = make()
    old = app.rt.token
    with app.connect() as chat:
        new = _rotate(chat)
        assert new != old and app.rt.token == new
        # The asking page's socket stays open and signed in.
        chat.ws.send_json({"type": "session.rotate"})
        newer = chat.until("session.token")[-1]["token"]
        assert newer == app.rt.token
    # The API: the old token is refused, the current one works.
    refused = app.client.get("/api/devices", headers={"authorization": f"Bearer {old}"})
    assert refused.status_code == 401
    ok = app.client.get("/api/devices", headers={"authorization": f"Bearer {app.rt.token}"})
    assert ok.status_code == 200
    # A socket's hello with the old token is refused.
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": old, "protocol": 1})
        assert ws.receive_json() == {"type": "error", "code": "unauthenticated"}


def test_every_other_socket_is_told_and_closed(make: Any) -> None:
    app: App = make()
    observe = app.client.websocket_connect("/ws/observe", headers=ORIGIN)
    with app.connect() as other, observe as watch:
        watch.send_json({"type": "hello", "token": app.rt.token, "protocol": 1})
        with app.connect() as asker:
            _rotate(asker)
            _told_signed_out(other.ws)
            _told_signed_out(watch)
            # The one that asked is still signed in.
            asker.ws.send_json({"type": "session.rotate"})
            asker.until("session.token")


def test_an_observe_socket_with_the_old_token_is_refused(make: Any) -> None:
    app: App = make()
    old = app.rt.token
    with app.connect() as chat:
        _rotate(chat)
    with app.client.websocket_connect("/ws/observe", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": old, "protocol": 1})
        assert ws.receive_json() == {"type": "error", "code": "unauthenticated"}


def test_two_sign_outs_leave_exactly_one_working_token(make: Any) -> None:
    app: App = make()
    with app.connect() as first, app.connect() as second:
        a = _rotate(first)
        _told_signed_out(second.ws)
        b = _rotate(first)
    assert a != b and app.rt.token == b
    for token, code in ((a, 401), (b, 200)):
        got = app.client.get("/api/devices", headers={"authorization": f"Bearer {token}"})
        assert got.status_code == code


def test_a_socket_that_never_signed_in_cannot_sign_anyone_out(make: Any) -> None:
    app: App = make()
    token = app.rt.token
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"type": "session.rotate"})
        assert ws.receive_json() == {"type": "error", "code": "unauthenticated"}
    assert app.rt.token == token


def test_hooks_hear_the_new_token_and_the_token_is_never_logged(
    make: Any, caplog: pytest.LogCaptureFixture
) -> None:
    app: App = make()
    heard: list[str] = []
    app.rt.token_hooks.append(heard.append)
    caplog.set_level("DEBUG")
    with app.connect() as chat:
        new = _rotate(chat)
    assert heard == [new]
    assert new not in caplog.text


def test_a_security_notice_says_every_other_page_was_signed_out(make: Any) -> None:
    app: App = make()
    told: list[int] = []
    assert app.rt.security is not None
    app.rt.security.signed_out_others = told.append  # type: ignore[method-assign]
    with app.connect() as chat, app.connect() as other:
        _rotate(chat)
        _told_signed_out(other.ws)
    assert told == [1]
