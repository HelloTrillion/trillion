"""The Living Mind through the server (GAL-10): `GET /api/mind`, a node's detail, and
the read-only `/ws/observe`, behind the same token and origin checks as everything."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.api.test_server import ORIGIN, App, home, make, receive  # noqa: F401
from tests.brain.fakes import say, use
from tests.helpers.test_api import AID, ask, call, ready
from trillion.api.app import CLOSE_FORBIDDEN, CLOSE_UNAUTHENTICATED
from trillion.mind.observe import EVENTS

COFFEE = {"kind": "personal", "text": "I take my coffee black.", "moves": False, "said_by": "user"}


def named(home: Path, name: str = "Nova") -> None:
    (home / "IDENTITY.md").write_text(f"---\nagent_name: {name}\n---\n", encoding="utf-8")


def remember(app: App, text: str) -> str:
    async def write() -> str:
        done = await app.rt.memory.write(
            type="user", text=text, source="user_statement", moves=False, now_ms=app.rt.clock()
        )
        return done.memory.id

    return call(app, write)  # type: ignore[no-any-return]


def mind(app: App) -> dict[str, Any]:
    r = app.client.get("/api/mind", headers=app.auth)
    assert r.status_code == 200
    body: dict[str, Any] = r.json()
    return body


def detail(app: App, node_id: str) -> Any:
    return app.client.get(f"/api/mind/node/{node_id}", headers=app.auth)


# ── The token ────────────────────────────────────────────────────────────


def test_both_routes_need_the_session_token(make: Any) -> None:
    app: App = make()
    assert app.client.get("/api/mind").status_code == 401
    assert app.client.get("/api/mind/node/core").status_code == 401
    wrong = {"authorization": "Bearer nope"}
    assert app.client.get("/api/mind", headers=wrong).status_code == 401
    elsewhere = {**app.auth, "origin": "https://evil.example"}
    assert app.client.get("/api/mind", headers=elsewhere).status_code == 403


# ── The map ──────────────────────────────────────────────────────────────


def test_the_map_is_her_by_her_settings_name_with_what_is_true(make: Any, home: Path) -> None:
    named(home)
    app: App = make()
    remember(app, "I take my coffee black.")
    body = mind(app)
    assert body["agent"] == "Nova"
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["core"]["label"] == "Nova"
    assert nodes["ring:stable"]["extra"]["chars"] > 100
    stats = body["stats"]
    # The meaning extra isn't installed in tests: no similarity, and it says so.
    assert stats["sources"]["memory"] == "no_meaning" and stats["memory_total"] == 1
    assert stats["sources"]["core"] == "ok" and stats["sources"]["rim"] == "ok"
    assert stats["sources"]["working"] == "empty"
    (m,) = [n for n in body["nodes"] if n["type"] == "memory"]
    assert m["label"] == "I take my coffee black." and m["extra"]["source"] == "user_statement"
    assert not any(e["kind"] == "similarity" for e in body["edges"])
    # Her own tools are on the rim, by category and risk.
    assert nodes["tool:file_read"]["extra"] == {"category": "files", "risk": "read"}
    # The soul and the prompts that make up the stable block feed it.
    assert nodes["know:SOUL.md"]["extra"] == {"kind": "persona"}
    assert nodes["know:IDENTITY.md"]["extra"] == {"kind": "persona"}
    assert nodes["know:prompts/rules.md"]["extra"] == {"kind": "prompt"}
    feeds = {e["source"] for e in body["edges"] if e["kind"] == "feeds"}
    assert {"know:SOUL.md", "know:prompts/rules.md"} <= feeds
    assert ("core", m["id"]) in {(e["source"], e["target"]) for e in body["edges"]}


def test_a_chat_is_on_the_map_by_title_and_counted_in_detail(make: Any) -> None:
    app: App = make(say("Black it is."))
    with app.connect() as chat:
        chat.say("Remember my coffee order please")
        conversation = chat.session["id"]
    body = mind(app)
    (c,) = [n for n in body["nodes"] if n["type"] == "chat"]
    assert c["id"] == f"chat:{conversation}"
    assert "Black it is" not in json.dumps(body)
    d = detail(app, c["id"]).json()
    assert d["messages"] == 2 and d["kind"] == "chat" and d["type"] == "chat"
    assert set(d) == {"id", "type", "title", "kind", "last_ms", "messages"}


def test_a_failing_source_costs_only_its_region(make: Any, monkeypatch: Any) -> None:
    app: App = make()

    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("no chats today")

    monkeypatch.setattr(app.rt.conversations, "list_chats", broken)
    body = mind(app)
    assert body["stats"]["sources"]["working"] == "error"
    assert body["stats"]["sources"]["core"] == "ok"
    assert any(n["type"] == "tool" for n in body["nodes"])


# ── A node's detail ──────────────────────────────────────────────────────


def test_detail_per_type(make: Any, home: Path) -> None:
    named(home)
    app: App = make()
    ready(app)
    assert app.rt.schedules is not None
    call(app, app.rt.schedules.create, ask())
    memory_id = remember(app, "I take my coffee black.")

    core = detail(app, "core").json()
    assert core["agent"] == "Nova" and core["blocks"]["stable"] > 100 and core["type"] == "core"
    stable = detail(app, "ring:stable").json()
    assert stable["parts"][:3] == ["soul", "rules", "shared folders"] and stable["chars"] > 100
    turn = detail(app, "ring:turn").json()
    assert turn["parts"][0] == "now" and turn["chars"] > 0

    m = detail(app, f"mem:{memory_id}").json()
    assert m["text"] == "I take my coffee black." and m["version"] == 1
    assert m["similar"] == [] and m["kind"] == "user" and m["type"] == "memory"

    h = detail(app, f"helper:ask:{AID}").json()
    assert h["name"] == "Weather at 4" and h["type"] == "helper"
    assert set(h) >= {"job", "state", "doing", "last_run", "next_ms", "connections"}

    t = detail(app, "tool:file_read").json()
    assert t["risk"] == "read" and t["category"] == "files" and t["type"] == "tool"
    assert {"label", "description", "recoverable"} <= set(t)

    k = detail(app, "know:prompts/rules.md").json()
    assert k["path"] == "prompts/rules.md" and k["kind"] == "prompt"
    assert 0 < len(k["text"]) <= 2000 and k["chars"] >= len(k["text"])
    soul = detail(app, "know:SOUL.md").json()
    assert soul["type"] == "knowledge" and soul["text"]


@pytest.mark.parametrize(
    "node_id",
    [
        "mem:mem_000000000000",
        "chat:nope",
        "helper:own:" + "0" * 32,
        "know:../../etc/passwd",
        "know:prompts/greeting.md",  # a prompt, but not one in the stable block
        "tool:no_such_tool",
        "conn:google",
        "ring:other",
        "bogus",
    ],
)
def test_an_unknown_node_is_404(make: Any, node_id: str) -> None:
    app: App = make()
    r = detail(app, node_id)
    assert r.status_code == 404 and r.json() == {"error": "not_found"}


def test_a_forgotten_memory_has_no_detail(make: Any) -> None:
    app: App = make()
    memory_id = remember(app, "I live in a flat.")
    call(app, app.rt.memory.forget, memory_id, app.rt.clock())
    assert detail(app, f"mem:{memory_id}").status_code == 404
    assert not any(n["type"] == "memory" for n in mind(app)["nodes"])


# ── /ws/observe ──────────────────────────────────────────────────────────


def publish(app: App, kind: str, **ids: Any) -> None:
    async def on_loop() -> None:
        app.rt.observe.publish(kind, **ids)

    call(app, on_loop)


def next_json(ws: Any) -> dict[str, Any]:
    message = receive(ws)
    ws._raise_on_close(message)
    body: dict[str, Any] = json.loads(message["text"])
    return body


def test_the_observe_socket_needs_the_hello(make: Any) -> None:
    app: App = make()
    with app.client.websocket_connect("/ws/observe", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": "wrong", "protocol": 1})
        assert ws.receive_json() == {"type": "error", "code": "unauthenticated"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    with app.client.websocket_connect("/ws/observe", headers=ORIGIN) as ws:
        ws.send_json({"type": "turn.text", "text": "hi"})
        assert ws.receive_json()["code"] == "unauthenticated"
    assert not app.rt.observe.watched


def test_the_observe_socket_refuses_another_site(make: Any) -> None:
    app: App = make()
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        app.client.websocket_connect("/ws/observe", headers={"origin": "https://evil.example"}),
    ):
        pass
    assert closed.value.code == CLOSE_FORBIDDEN


def test_events_arrive_and_what_the_page_sends_is_ignored(make: Any) -> None:
    app: App = make()
    with app.client.websocket_connect("/ws/observe", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": app.rt.token, "protocol": 1})
        assert next_json(ws) == {"type": "observe.ready"}
        # The page cannot start anything here.
        ws.send_json({"type": "turn.text", "text": "do something"})
        ws.send_text("not json")
        publish(app, "tool.start", name="file_read")
        assert next_json(ws) == {"type": "tool.start", "name": "file_read"}
        publish(app, "memory.recalled", ids=["mem_1"])
        assert next_json(ws) == {"type": "memory.recalled", "ids": ["mem_1"]}
        assert app.rt.observe.watched
    assert app.rows("SELECT count(*) FROM conversations") == [(0,)]


def test_a_turn_tells_names_and_ids_and_never_arguments_or_results(make: Any) -> None:
    app: App = make(use("memory_write", COFFEE, "c1"), say("Noted, black coffee."))
    watching = app.rt.observe.subscribe()
    with app.connect() as chat:
        chat.say("Remember: I take my coffee black.")
        conversation = chat.session["id"]
    events = []
    while watching.pending():
        events.append(watching._queue.popleft())
    kinds = [e["type"] for e in events]
    memory_id = app.rt.memory.current()[0].id
    assert {"type": "tool.start", "name": "memory_write"} in events
    assert {"type": "tool.end", "name": "memory_write", "ok": True} in events
    assert {"type": "memory.written", "id": memory_id} in events
    assert {"type": "turn", "conversation_id": conversation} in events
    assert kinds.index("tool.start") < kinds.index("memory.written") < kinds.index("tool.end")
    for e in events:
        assert set(e) - {"type"} <= EVENTS[e["type"]], e
    said = json.dumps(events)
    assert "coffee" not in said.lower() and "noted" not in said.lower()
