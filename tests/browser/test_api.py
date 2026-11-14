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
        "open_any": False,
    }
    assert "isn't a virtual machine" in body["about"]
    assert body["tasks"] == [] and body["sites"] == [] and body["free"] is None


def test_settings_take_only_known_values(make: Any) -> None:
    app = make(model=False)
    put = {**app.auth, "content-type": "application/json"}
    r = app.client.put(
        "/api/browser/settings",
        json={"act": True, "search": "nowhere", "stronger": "yes", "extra": 1, "open_any": "on"},
        headers=put,
    )
    assert r.status_code == 200
    assert r.json()["settings"] == {
        "act": True,
        "stronger": False,
        "search": "bing",
        "live": True,
        "open_any": False,
    }


def test_settings_save_and_read_back_the_engine_and_open_any(make: Any) -> None:
    app = make(model=False)
    put = {**app.auth, "content-type": "application/json"}
    r = app.client.put(
        "/api/browser/settings", json={"search": "google", "open_any": True}, headers=put
    )
    assert r.status_code == 200
    body = app.client.get("/api/browser", headers=app.auth).json()
    assert body["settings"]["search"] == "google" and body["settings"]["open_any"] is True
    r = app.client.put("/api/browser/settings", json={"open_any": False}, headers=put)
    assert r.json()["settings"]["open_any"] is False
    assert r.json()["settings"]["search"] == "google"


def test_always_allow_on_a_chip_turns_on_open_any(make: Any, monkeypatch: Any) -> None:
    app = make(model=False)
    svc = service_of(app.rt)
    assert svc is not None
    headers = {**app.auth, "content-type": "application/json"}
    path = "/api/browser/tasks/abcdef0123456789/asks/a1b2c3"
    # Nothing waiting: refused, and the setting stays off.
    r = app.client.post(path, headers=headers, json={"answer": "always"})
    assert r.status_code == 409
    got = app.client.get("/api/browser", headers=app.auth).json()
    assert got["settings"]["open_any"] is False
    answered: list[tuple[str, str, str]] = []

    async def answer(task_id: str, ask_id: str, word: str) -> bool:
        answered.append((task_id, ask_id, word))
        return True

    monkeypatch.setattr(svc, "answer", answer)
    r = app.client.post(path, headers=headers, json={"answer": "always"})
    assert r.status_code == 200
    assert answered == [("abcdef0123456789", "a1b2c3", "always")]
    assert r.json()["settings"]["open_any"] is True
    # A plain Allow allows the one site, and changes no setting.
    app.client.put("/api/browser/settings", json={"open_any": False}, headers=headers)
    r = app.client.post(path, headers=headers, json={"answer": "allowed"})
    assert r.status_code == 200 and r.json()["settings"]["open_any"] is False
    # Only the known answers.
    r = app.client.post(path, headers=headers, json={"answer": "forever"})
    assert r.status_code == 422


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


def test_a_reason_reads_as_a_sentence() -> None:
    from trillion.api.browser import sentence

    assert sentence("only the standard web ports are opened") == (
        "Only the standard web ports are opened."
    )
    assert sentence("that is on this computer or its network") == (
        "That is on this computer or your network."
    )
    assert sentence("Already a sentence.") == "Already a sentence."


def test_a_tap_in_her_conversation_on_a_helpers_card_is_settled_by_the_helper(make: Any) -> None:
    from trillion.api import taps
    from trillion.spine.router import RouterResponse

    seen: list[tuple[str, str]] = []

    async def mine(rt: Any, pending_id: str, decision: Any) -> RouterResponse | None:
        if pending_id != "helper-card":
            return None
        seen.append((pending_id, decision))
        return RouterResponse(status="cancelled", pending_id=pending_id)

    taps.register(mine)
    try:
        app = make(model=False)
        with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json({"type": "hello", "token": app.rt.token, "protocol": 1, "client": "web"})
            while ws.receive_json()["type"] != "state":
                pass
            ws.send_json({"type": "cancel", "pending_id": "helper-card"})
            while True:
                msg = ws.receive_json()
                if msg["type"] == "confirm.resolved":
                    break
            assert msg["pending_id"] == "helper-card" and msg["status"] == "cancelled"
        assert seen == [("helper-card", "cancel")]
    finally:
        taps.HANDLERS.remove(mine)


async def test_her_conversation_gets_the_card_with_its_own_words_for_running_out() -> None:
    from types import SimpleNamespace

    from trillion.browser.browse import tell_settled, tell_waits
    from trillion.spine.contract import Risk

    sent: list[tuple[str, dict[str, Any]]] = []

    class Page:
        id = "conv"

        async def _send(self, kind: str, **body: Any) -> None:
            sent.append((kind, body))

    card = SimpleNamespace(
        id="p1", owner_id="local", tool="browser_press", risk=Risk.IRREVERSIBLE,
        recoverable=False, summary="Send to httpbin.org", detail="POST https://httpbin.org/post",
        expires_at=123,
    )  # fmt: skip

    async def get_pending(pid: str) -> Any:
        return card

    rt = SimpleNamespace(sessions={Page()}, spine_store=SimpleNamespace(get_pending=get_pending))
    await tell_waits(rt, "conv", "Before it sends the form to httpbin.org, it needs your OK.", "p1")
    kinds = [k for k, _ in sent]
    assert kinds == ["confirm.request", "notice"]
    request = sent[0][1]
    assert request["tool_label"] == "Send a form from her browser"
    assert request["tap_only"] is True
    assert request["expired_note"] == "It ran out of time, so nothing was sent."
    assert sent[1][1] == {
        "code": "browser",
        "text": "Before it sends the form to httpbin.org, it needs your OK.",
    }
    await tell_settled(rt, "conv", "p1", "refused", "It ran out of time, so nothing was sent.")
    assert sent[-1] == (
        "confirm.resolved",
        {
            "pending_id": "p1",
            "status": "refused",
            "reason": None,
            "detail": "It ran out of time, so nothing was sent.",
        },
    )
