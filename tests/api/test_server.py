"""The local server end to end: the socket, the session, the spine and a scripted model."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import struct
from collections.abc import AsyncIterator, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from tests.brain.fakes import ScriptedProvider, say, use
from trillion import paths
from trillion.api.app import CLOSE_FORBIDDEN, CLOSE_UNAUTHENTICATED, create_app
from trillion.api.runtime import Runtime, open_runtime
from trillion.brain import config
from trillion.brain.types import (
    ContextBlock,
    ModelChoice,
    ModelEvent,
    ModelProvider,
    ProviderError,
    TextBlock,
    TextDelta,
)
from trillion.persona.prompt import degraded
from trillion.secrets.store import MemoryStore

HOSTS = frozenset({"testserver"})
ORIGIN = {"origin": "http://testserver"}
MARKER = "zebra-marker-7731"
# Far longer than any reply takes. A message that never comes fails the test here
# instead of holding the whole run.
RECEIVE_TIMEOUT_S = 20.0


class App:
    def __init__(
        self,
        tmp_path: Path,
        provider: ModelProvider | None,
        *,
        keychain: bool = True,
        env: dict[str, str] | None = None,
    ) -> None:
        self.granted = tmp_path / "granted"
        self.granted.mkdir(exist_ok=True)
        self.provider = provider
        # Never the real keychain or the real environment.
        self.secrets = MemoryStore(usable=keychain)
        self.rt: Runtime = asyncio.run(
            open_runtime(
                granted_roots=(self.granted,),
                persona=tmp_path,
                secrets=self.secrets,
                env=env or {},
            )
        )
        if provider is not None:
            self.rt.provider = provider
        self.client = TestClient(create_app(self.rt, hosts=HOSTS))
        # Entered, the client runs every socket on one event loop, as the real server
        # does. Left to itself it gives each socket its own loop and closes it when the
        # socket goes; a database call still in flight then has no loop to answer to,
        # which kills the connection's worker thread and hangs the next call forever.
        self.client.__enter__()
        self.auth = {"authorization": f"Bearer {self.rt.token}"}

    @contextmanager
    def connect(
        self, *, token: str | None = None, resume: str | None = None, **kw: Any
    ) -> Iterator[Chat]:
        with self.client.websocket_connect("/ws", headers=kw.pop("headers", ORIGIN)) as ws:
            hello = {"type": "hello", "token": token or self.rt.token, "protocol": 1}
            ws.send_json(hello | ({"resume": resume} if resume else {}))
            chat = Chat(ws)
            chat.session = chat.until("state")[0]
            yield chat

    def close(self) -> None:
        portal = self.client.portal
        assert portal is not None
        try:
            # On the sockets' loop, so calls still in flight finish before the close.
            portal.call(self.rt.close)
        finally:
            self.client.__exit__(None, None, None)

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        # closing(): sqlite3's own context manager commits but leaves the file open.
        with closing(sqlite3.connect(paths.db_path())) as db:
            return db.execute(sql).fetchall()


async def _within(ws: WebSocketTestSession) -> Any:
    with anyio.fail_after(RECEIVE_TIMEOUT_S):
        return await ws._send_rx.receive()


def receive(ws: WebSocketTestSession) -> Any:
    """The next frame from the server, or a TimeoutError if none comes."""
    return ws.portal.call(_within, ws)


class Chat:
    def __init__(self, ws: WebSocketTestSession) -> None:
        self.ws = ws
        self.session: dict[str, Any] = {}

    def until(self, *kinds: str) -> list[dict[str, Any]]:
        seen = []
        while True:
            message = receive(self.ws)
            self.ws._raise_on_close(message)
            seen.append(json.loads(message["text"]))
            if seen[-1]["type"] == "confirm.request":
                # As the page does once a card is on screen: only then can it be approved.
                self.ws.send_json({"type": "confirm.shown", "pending_id": seen[-1]["pending_id"]})
            if seen[-1]["type"] in kinds:
                return seen

    def say(self, text: str) -> list[dict[str, Any]]:
        self.ws.send_json({"type": "turn.text", "text": text})
        return self.until("turn.complete", "error", "interrupted")

    def tap(self, kind: str, pending_id: str) -> dict[str, Any]:
        self.ws.send_json({"type": kind, "pending_id": pending_id})
        return self.until("confirm.resolved")[-1]


def only(msgs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    (found,) = [m for m in msgs if m["type"] == kind]
    return found


def reply(msgs: list[dict[str, Any]]) -> str:
    return "".join(m["text"] for m in msgs if m["type"] == "reply.delta")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture
def make(home: Path) -> Iterator[Any]:
    apps: list[App] = []

    def build(*replies: list[ModelEvent] | Exception, model: bool = True, **kw: Any) -> App:
        apps.append(App(home, ScriptedProvider(*replies) if model else None, **kw))
        return apps[-1]

    yield build
    for app in apps:
        app.close()


# ── Who may connect (SPN-11, SPN-11a) ────────────────────────────────────


@pytest.mark.parametrize(
    "first",
    [
        {"type": "hello", "token": "wrong", "protocol": 1},
        {"type": "hello", "protocol": 1},
        {"type": "hello", "token": 7},
        {"type": "turn.text", "text": "hi"},
        {"type": "confirm", "pending_id": "x"},
    ],
)
def test_an_unauthenticated_socket_is_told_so_and_nothing_else(make: Any, first: dict) -> None:
    app = make(say("never"))
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(first)
        assert ws.receive_json() == {"type": "error", "code": "unauthenticated"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert app.provider.calls == []  # no model call
    assert app.rows("SELECT count(*) FROM conversations") == [(0,)]  # no state
    assert app.rows("SELECT count(*) FROM tool_audit") == [(0,)]  # no tool


def test_a_newer_protocol_is_refused_rather_than_guessed(make: Any) -> None:
    app = make()
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": app.rt.token, "protocol": 2})
        assert ws.receive_json()["code"] == "unauthenticated"


def test_the_right_token_from_another_site_is_refused(make: Any) -> None:
    app = make()
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        app.client.websocket_connect("/ws", headers={"origin": "https://evil.example"}),
    ):
        pass
    assert closed.value.code == CLOSE_FORBIDDEN


def test_a_rebound_hostname_is_refused(make: Any) -> None:
    app = make()
    bad = {"host": "evil.example:7777"}
    assert app.client.get("/", headers=bad).status_code == 403
    with pytest.raises(WebSocketDisconnect), app.client.websocket_connect("/ws", headers=bad):
        pass


def test_a_local_client_with_no_origin_still_needs_the_token(make: Any) -> None:
    app = make()
    with app.connect(headers={}) as chat:
        assert chat.session["protocol"] == 1
    with app.client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "token": "nope"})
        assert ws.receive_json()["code"] == "unauthenticated"


def test_every_launch_has_its_own_token(make: Any) -> None:
    a, b = make(), make()
    assert a.rt.token != b.rt.token and len(a.rt.token) >= 32


def test_pages_carry_the_security_headers_and_say_when_the_ui_is_missing(make: Any) -> None:
    r = make().client.get("/")
    assert r.status_code == 200 and "has not been built" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_the_built_ui_is_served(make: Any, home: Path) -> None:
    app = make()
    web = home / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>ui</title>", encoding="utf-8")
    client = TestClient(create_app(app.rt, hosts=HOSTS, static=web))
    assert "<title>ui</title>" in client.get("/").text
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


# ── A conversation ───────────────────────────────────────────────────────


def test_hello_opens_a_session(make: Any) -> None:
    app = make()
    with app.connect() as chat:
        s = chat.session
        assert s["agent_name"] == "Trillion" and s["model"] == {
            "provider": "fake",
            "model": "fake-1",
        }
        assert s["granted_roots"] == [str(app.granted.resolve())]


def test_a_typed_turn_streams_a_reply_and_is_stored(make: Any) -> None:
    app = make(say("Short answer: yes."))
    with app.connect() as chat:
        msgs = chat.say("is it working?")
        assert reply(msgs) == "Short answer: yes."
        orbs = [m["orb"] for m in msgs if m["type"] == "state"]
        assert orbs == ["processing", "speaking"]
        assert only(msgs, "turn.complete")["usage"]["input_tokens"] == 10
        assert chat.until("state")[-1]["orb"] == "idle"
    assert [r[0] for r in app.rows("SELECT kind FROM messages ORDER BY id")] == [
        "user",
        "assistant",
    ]
    assert app.rows("SELECT provider, model, input_tokens, cost_usd FROM usage") == [
        ("fake", "fake-1", 10, None)  # an unknown price is recorded as unknown, not zero
    ]


def test_the_model_sees_the_soul_once_and_context_on_the_newest_turn_only(make: Any) -> None:
    app = make(say("One."), say("Two."))
    with app.connect() as chat:
        chat.say("first")
        chat.until("state")
        chat.say("second")
    system, messages = app.provider.calls[1]
    assert len(system) == 1 and system[0].cache
    first, _, second = messages
    assert isinstance(first.blocks[0], ContextBlock) and first.blocks[0].text.startswith(
        "<context>"
    )
    assert first.blocks[1] == TextBlock(text="first")
    assert second.blocks[1] == TextBlock(text="second")
    # History is only ever appended to: the first turn is replayed byte for byte, which
    # is what keeps the model's own reasoning (and the prompt cache) valid.
    assert app.provider.calls[0][1] == [first]


def test_the_time_she_is_given_is_the_users_and_names_its_zone(make: Any) -> None:
    from trillion.persona import setup

    # No zone given at onboarding: the computer's own.
    app = make(say("One."), say("Two."), env={"TZ": "Europe/Paris"})
    with app.connect() as chat:
        chat.say("first")
        chat.until("state")
        app.rt.profile = setup.Profile(user_name="Sam", timezone="Asia/Tokyo")
        chat.say("second")
    first, _, second = app.provider.calls[1][1]
    assert first.blocks[0].text.startswith("<context>\nnow: ")
    assert "(Europe/Paris)" in first.blocks[0].text
    assert "JST (Asia/Tokyo)" in second.blocks[0].text  # the one they gave wins


def test_with_no_model_the_app_still_opens_and_says_why(make: Any) -> None:
    app = make(model=False)
    with app.connect() as chat:
        assert chat.session["model"] is None
        assert chat.say("hello?")[-1] == {"type": "error", "code": "no_model"}


@pytest.mark.parametrize("bad", [{"type": "turn.text"}, {"type": "turn.text", "text": "  "}])
def test_bad_messages_are_refused(make: Any, bad: dict) -> None:
    with make().connect() as chat:
        chat.ws.send_json(bad)
        assert chat.until("error")[-1]["code"] == "bad_message"
        chat.ws.send_text("not json")
        assert chat.until("error")[-1]["code"] == "bad_message"
        chat.ws.send_json({"type": "launch"})
        assert chat.until("error")[-1]["code"] == "unknown_type"


def test_a_provider_failure_is_named_and_the_orb_shows_it(make: Any) -> None:
    app = make(ProviderError("unauthorized", MARKER))
    with app.connect() as chat:
        msgs = chat.say("hi")
        assert msgs[-1] == {
            "type": "error",
            "code": "provider",
            "kind": "unauthorized",
            "sentence": degraded("model", "unauthorized"),
        }
        assert chat.until("state")[-1]["orb"] == "error"


def test_an_interrupt_stops_the_reply_and_keeps_what_was_said(make: Any) -> None:
    class Slow(ScriptedProvider):
        async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:
            self.calls.append((kw["system"], list(kw["messages"])))
            yield TextDelta(text="Well")
            await asyncio.sleep(30)

    app = make()
    app.rt.provider = Slow()
    with app.connect() as chat:
        chat.ws.send_json({"type": "turn.text", "text": "go on"})
        chat.until("reply.delta")
        chat.ws.send_json({"type": "turn.text", "text": "and another"})
        assert chat.until("error")[-1]["code"] == "busy"
        chat.ws.send_json({"type": "interrupt"})
        chat.until("interrupted")
    saved = app.rows("SELECT blocks_json FROM messages WHERE kind = 'assistant'")
    assert "Well [interrupted]" in saved[0][0]


# ── The confirmation card (SPN-2, SPN-14, SPN-17) ────────────────────────


def propose(app: App, name: str = "note.txt", content: str = "hi") -> list[ModelEvent]:
    return use("file_create", {"path": str(app.granted / name), "content": content})


def test_a_write_raises_a_card_and_a_tap_runs_exactly_what_it_showed(make: Any) -> None:
    app = make()
    app.provider._replies += [propose(app), say("Shall I?"), say("Done. It's there.")]
    with app.connect() as chat:
        msgs = chat.say("write a note")
        card = only(msgs, "confirm.request")
        assert card["tool"] == "file_create" and "note.txt" in card["summary"]
        assert card["tap_only"] is False
        assert only(msgs, "tool.end") == {
            "type": "tool.end",
            "call_id": "c1",
            "ok": True,
            "summary": "confirmation_required",
        }
        assert not (app.granted / "note.txt").exists()  # asked, not done
        chat.until("state")

        chat.ws.send_json({"type": "confirm.shown", "pending_id": card["pending_id"]})
        resolved = chat.tap("confirm", card["pending_id"])
        assert resolved["status"] == "succeeded"
        assert (app.granted / "note.txt").read_text(encoding="utf-8") == "hi"
        assert reply(chat.until("turn.complete")) == "Done. It's there."

    # She was told what the tap did by the host, not by guessing.
    note = app.provider.calls[2][1][-1].blocks[-1]
    assert isinstance(note, TextBlock) and note.text.startswith("[host] The user tapped Approve")
    assert '"status": "succeeded"' in note.text
    assert app.rows("SELECT surface FROM decision_deliveries") == [("web",)]
    assert [r[0] for r in app.rows("SELECT kind FROM messages ORDER BY id")] == [
        "user", "assistant", "tool_results", "assistant", "tap", "assistant",
    ]  # fmt: skip


def test_declining_a_card_runs_nothing(make: Any) -> None:
    app = make()
    app.provider._replies += [propose(app), say("Shall I?"), say("Left alone.")]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        chat.until("state")
        assert chat.tap("cancel", card["pending_id"])["status"] == "cancelled"
        chat.until("turn.complete")
    assert not (app.granted / "note.txt").exists()


def test_a_spoken_yes_lets_the_model_confirm_on_the_next_turn(make: Any) -> None:
    app = make()
    app.provider._replies += [propose(app), say("Shall I?")]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        chat.until("state")
        app.provider._replies += [
            use("confirm_pending_action", {"pending_id": card["pending_id"]}, "c2"),
            say("Done."),
        ]
        msgs = chat.say("yes")
        assert [m["summary"] for m in msgs if m["type"] == "tool.end"] == ["succeeded"]
    assert (app.granted / "note.txt").read_text(encoding="utf-8") == "hi"


def test_an_overwrite_can_only_be_approved_by_a_tap(make: Any) -> None:
    app = make()
    target = app.granted / "note.txt"
    target.write_text("old", encoding="utf-8")
    overwrite = use("file_overwrite", {"path": str(target), "content": "new"})
    app.provider._replies += [overwrite, say("Shall I?")]
    with app.connect() as chat:
        card = only(chat.say("replace it"), "confirm.request")
        assert card["tap_only"] is True
        chat.until("state")
        app.provider._replies += [
            use("confirm_pending_action", {"pending_id": card["pending_id"]}, "c2"),
            say("Tap the card."),
        ]
        msgs = chat.say("yes do it")
        assert [m["summary"] for m in msgs if m["type"] == "tool.end"] == ["tap_required"]
        assert target.read_text(encoding="utf-8") == "old"
        chat.until("state")
        app.provider._replies += [say("Replaced.")]
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        chat.until("turn.complete")
    assert target.read_text(encoding="utf-8") == "new"


def test_only_the_session_that_raised_a_card_can_answer_it(make: Any) -> None:
    app = make()
    app.provider._replies += [propose(app), say("Shall I?")]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        chat.until("state")
        with app.connect() as other:
            refused = other.tap("confirm", card["pending_id"])
            assert (refused["status"], refused["reason"]) == ("refused", "wrong_session")
    assert not (app.granted / "note.txt").exists()


def test_a_reload_resumes_the_conversation_and_its_open_card(make: Any) -> None:
    app = make()
    app.provider._replies += [propose(app), say("Shall I?"), say("Done.")]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        conversation = chat.session["id"]
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": app.rt.token, "resume": conversation})
        chat = Chat(ws)
        opening = chat.until("state")
        assert opening[0]["id"] == conversation
        assert only(opening, "history")["messages"] == [
            {"role": "user", "text": "write a note"},
            {"role": "assistant", "text": "Shall I?"},
        ]
        assert only(opening, "confirm.request")["pending_id"] == card["pending_id"]
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        chat.until("turn.complete")
    assert (app.granted / "note.txt").exists()


def test_resuming_a_conversation_that_does_not_exist_starts_a_new_one(make: Any) -> None:
    with make().connect(resume="does-not-exist") as chat:
        assert chat.session["id"] != "does-not-exist"


# ── Privacy ──────────────────────────────────────────────────────────────


def test_conversation_text_never_reaches_a_log(make: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    app = make()
    app.provider._replies += [
        propose(app, content=f"file {MARKER}"),
        say(f"reply {MARKER}"),
        say("ok"),
    ]
    with app.connect() as chat:
        card = only(chat.say(f"user {MARKER}"), "confirm.request")
        chat.until("state")
        chat.tap("confirm", card["pending_id"])
        chat.until("turn.complete")
    records = [r for when in ("setup", "call") for r in caplog.get_records(when)]
    assert records and not [r.getMessage() for r in records if MARKER in r.getMessage()]


# ── Model settings: the key goes to the keychain and nowhere else (SEC-1, 4, 7) ──

# Built from pieces: a key's shape, not a key, and nothing a secret scanner should flag.
SAMPLE_KEY = "sk-" + "ant-" + "test" * 6 + "abcd"


@pytest.fixture
def offers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None, str | None]]:
    """Stand in for the provider's model list, and record what it was asked with."""
    asked: list[tuple[str, str | None, str | None]] = []

    async def fake(kind: str, base_url: str | None, api_key: str | None) -> list[ModelChoice]:
        asked.append((kind, base_url, api_key))
        if api_key == "wrong":
            raise ProviderError("unauthorized", f"echo {api_key}")
        return [ModelChoice(id="model-a", label="Model A", max_output=8000)]

    monkeypatch.setattr(config, "list_models", fake)
    return asked


def save(app: App, **body: Any) -> Any:
    return app.client.put("/api/settings/model", json=body, headers=app.auth)


@pytest.mark.parametrize("method", ["get", "put", "post"])
def test_settings_need_the_session_token(make: Any, method: str) -> None:
    app = make(model=False)
    path = "/api/settings/model" + ("/models" if method == "post" else "")
    assert getattr(app.client, method)(path).status_code == 401
    wrong = {"authorization": "Bearer nope"}
    assert getattr(app.client, method)(path, headers=wrong).status_code == 401


def test_another_site_cannot_write_settings_even_with_the_token(make: Any, offers: list) -> None:
    app = make(model=False)
    evil = app.auth | {"origin": "https://evil.example"}
    r = app.client.put("/api/settings/model", json={"kind": "local", "model": "x"}, headers=evil)
    assert r.status_code == 403
    # A form post is a "simple" cross-site request; it is not JSON and is refused.
    form = app.client.put("/api/settings/model", data={"kind": "local"}, headers=app.auth)
    assert form.status_code == 415
    assert offers == [] and app.rt.settings is None


def test_first_run_has_nothing_configured(make: Any) -> None:
    app = make(model=False)
    state = app.client.get("/api/settings/model", headers=app.auth).json()
    assert state["source"] == "none" and state["active"] is None and state["saved"] is None
    assert state["keychain"] is True
    assert state["keys"] == {"anthropic": None, "custom": None, "openai": None}


def test_checking_a_key_lists_models_and_stores_nothing(make: Any, offers: list) -> None:
    app = make(model=False)
    body = {"kind": "anthropic", "api_key": SAMPLE_KEY}
    r = app.client.post("/api/settings/model/models", json=body, headers=app.auth)
    assert r.json() == {"models": [{"id": "model-a", "label": "Model A", "max_output": 8000}]}
    assert offers == [("anthropic", None, SAMPLE_KEY)]
    assert app.secrets.values() == [] and app.rt.provider is None


def test_saving_puts_the_key_in_the_keychain_and_nowhere_else(
    make: Any, offers: list, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    app = make(model=False)
    with app.connect() as chat:
        assert chat.session["model"] is None
        r = save(app, kind="anthropic", model="model-a", api_key=SAMPLE_KEY)
        assert r.status_code == 200
        # Every open tab learns there is now somebody to answer.
        assert chat.until("model")[-1]["model"] == {"provider": "anthropic", "model": "model-a"}

    assert app.secrets.get("anthropic/api_key") == SAMPLE_KEY
    state = r.json()
    assert state["source"] == "settings"
    assert state["saved"] == {"kind": "anthropic", "model": "model-a", "base_url": None}
    assert state["keys"]["anthropic"] == {"saved": True, "ends": "abcd"}
    # Not in the response, not in the database, not in a log.
    assert SAMPLE_KEY not in r.text
    assert app.rows("SELECT value_json FROM preferences") == [
        ('{"kind": "anthropic", "model": "model-a", "base_url": null, "max_output": 8000}',)
    ]
    for file in paths.home().iterdir():
        if file.is_file():
            assert SAMPLE_KEY.encode() not in file.read_bytes(), file.name
    assert SAMPLE_KEY not in caplog.text


def test_a_rejected_key_is_not_saved_and_is_not_echoed(
    make: Any, offers: list, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    app = make(model=False)
    r = save(app, kind="openai", model="model-a", api_key="wrong")
    assert (r.status_code, r.json()) == (502, {"error": "unauthorized"})
    assert app.secrets.values() == [] and app.rt.settings is None
    assert "echo" not in caplog.text


def test_a_key_is_required_where_one_is_needed(make: Any, offers: list) -> None:
    r = save(make(model=False), kind="anthropic", model="model-a")
    assert (r.status_code, r.json()) == (400, {"error": "key_required"})
    assert offers == []


def test_a_model_that_is_not_offered_is_refused(make: Any, offers: list) -> None:
    app = make(model=False)
    r = save(app, kind="anthropic", model="made-up", api_key=SAMPLE_KEY)
    assert (r.status_code, r.json()) == (400, {"error": "unknown_model"})
    assert app.secrets.values() == []


def test_a_local_model_needs_no_key_and_must_be_local(make: Any, offers: list) -> None:
    app = make(model=False)
    r = save(app, kind="local", model="model-a")
    assert r.status_code == 200 and r.json()["saved"]["base_url"] == config.LOCAL_URL
    assert offers == [("local", None, None)]
    assert app.rt.provider is not None and app.rt.provider.model == "model-a"


def test_unknown_fields_and_kinds_are_refused(make: Any, offers: list) -> None:
    app = make(model=False)
    assert save(app, kind="local", model="model-a", extra=1).status_code == 422
    assert save(app, kind="nonsense", model="model-a").status_code == 422
    assert save(app, kind="local", model="").status_code == 422


def test_a_saved_key_is_reused_and_can_be_removed_at_once(make: Any, offers: list) -> None:
    app = make(model=False)
    save(app, kind="anthropic", model="model-a", api_key=SAMPLE_KEY)
    # Saving again with no key keeps the one in the keychain.
    assert save(app, kind="anthropic", model="model-a").status_code == 200
    assert offers[-1] == ("anthropic", None, SAMPLE_KEY)

    body = {"kind": "anthropic"}
    r = app.client.post("/api/settings/model/forget-key", json=body, headers=app.auth)
    assert r.json()["keys"]["anthropic"] is None and r.json()["active"] is None
    assert app.secrets.values() == [] and app.rt.provider is None


def test_settings_survive_a_restart(make: Any, offers: list, home: Path) -> None:
    app = make(model=False)
    save(app, kind="anthropic", model="model-a", api_key=SAMPLE_KEY)
    again: Runtime = asyncio.run(open_runtime(secrets=app.secrets, env={}, persona=home))
    try:
        assert again.source == "settings"
        assert again.provider is not None and again.provider.model == "model-a"
    finally:
        asyncio.run(again.close())


def test_the_environment_wins_and_cannot_be_changed_from_the_page(make: Any, offers: list) -> None:
    app = make(model=False, env={"TRILLION_ANTHROPIC_API_KEY": "from-env-" + "x" * 12})
    state = app.client.get("/api/settings/model", headers=app.auth).json()
    assert state["source"] == "env" and state["active"]["provider"] == "anthropic"
    assert "from-env" not in str(state)
    r = save(app, kind="local", model="model-a")
    assert (r.status_code, r.json()) == (409, {"error": "set_by_environment"})


def test_without_a_keychain_a_key_is_refused_rather_than_written_to_disk(
    make: Any, offers: list
) -> None:
    app = make(model=False, keychain=False)
    assert app.client.get("/api/settings/model", headers=app.auth).json()["keychain"] is False
    r = save(app, kind="anthropic", model="model-a", api_key=SAMPLE_KEY)
    assert (r.status_code, r.json()) == (409, {"error": "no_keychain"})
    assert save(app, kind="local", model="model-a").status_code == 200  # no key, no problem


def test_a_rejected_request_never_echoes_the_key_it_carried(make: Any) -> None:
    app = make(model=False)
    for body in (
        {"kind": "anthropic", "api_key": SAMPLE_KEY},  # no model
        {"kind": "anthropic", "model": "m", "api_key": SAMPLE_KEY, "extra": 1},
        {"kind": "anthropic", "model": "m", "api_key": SAMPLE_KEY * 40},  # too long
    ):
        r = app.client.put("/api/settings/model", json=body, headers=app.auth)
        assert r.status_code == 422 and r.json()["error"] == "bad_request"
        assert SAMPLE_KEY not in r.text


@pytest.mark.parametrize("url", ["http://[::1", "http://localhost:11434:evil.example/v1"])
def test_a_malformed_address_is_a_plain_refusal(make: Any, url: str) -> None:
    app = make(model=False)
    body = {"kind": "custom", "base_url": url}
    r = app.client.post("/api/settings/model/models", json=body, headers=app.auth)
    assert (r.status_code, r.json()) == (400, {"error": "bad_url"})


def test_a_key_for_a_plain_http_address_is_refused_before_anything_is_sent(make: Any) -> None:
    app = make(model=False)
    body = {"kind": "custom", "base_url": "http://192.168.1.20:8090/v1", "api_key": SAMPLE_KEY}
    r = app.client.post("/api/settings/model/models", json=body, headers=app.auth)
    assert (r.status_code, r.json()) == (400, {"error": "key_needs_https"})


# ── Voice: chosen in the app, generated from the catalog (VOI-11, VOI-11a) ──


def speech(app: App, **body: Any) -> Any:
    return app.client.put("/api/settings/speech", json=body, headers=app.auth)


def test_speech_settings_need_the_token_and_list_the_catalog(make: Any) -> None:
    app = make(model=False)
    assert app.client.get("/api/settings/speech").status_code == 401
    state = app.client.get("/api/settings/speech", headers=app.auth).json()
    kinds = {o["kind"]: o for o in state["options"]}
    assert state["saved"] is None and state["active"] is False
    assert kinds["system"]["free"] and not kinds["system"]["needs_key"]
    assert kinds["elevenlabs"]["needs_key"] and kinds["elevenlabs"]["key"] is None
    assert any(o["free"] for o in state["options"])  # nobody needs a paid account


def test_the_free_voice_needs_no_key_and_is_spoken_by_the_page(make: Any) -> None:
    app = make(say("**Done.** The file is there. Check `hello.txt` now."))
    with app.connect() as chat:
        assert chat.session["speech"] is None
        assert speech(app, kind="system", voice="uri:Alex").status_code == 200
        told = chat.until("speech")[-1]["speech"]
        assert told == {
            "kind": "system",
            "where": "client",
            "voice": "uri:Alex",
            "sample_rate": None,
        }
        msgs = chat.say("is it done?")
        said = [m["text"] for m in msgs if m["type"] == "reply.sentence"]
        assert said == ["Done. The file is there.", "Check hello.txt now."]  # no markup aloud
        assert only(msgs, "audio.end")["sentences"] == 2
        assert reply(msgs) == "**Done.** The file is there. Check `hello.txt` now."  # text intact
    assert app.secrets.values() == []


def test_a_muted_page_is_sent_no_speech(make: Any) -> None:
    app = make(say("Short answer: yes."))
    speech(app, kind="system")
    with app.connect() as chat:
        chat.ws.send_json({"type": "speech.mute", "muted": True})
        assert not [m for m in chat.say("well?") if m["type"] == "reply.sentence"]


def test_a_paid_voice_needs_a_key_that_works_and_a_voice_it_offers(
    make: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from trillion.voice.providers.elevenlabs import ElevenLabs
    from trillion.voice.types import Voice, VoiceError

    caplog.set_level(logging.DEBUG)
    used: list[str] = []

    async def voices(self: ElevenLabs) -> list[Voice]:
        used.append(self._api_key())
        if used[-1] != SAMPLE_KEY:
            raise VoiceError("unauthorized", f"echo {used[-1]}")
        return [Voice(id="v1", name="Ada")]

    monkeypatch.setattr(ElevenLabs, "voices", voices)
    app = make(model=False)

    assert speech(app, kind="elevenlabs", voice="v1").json() == {"error": "key_required"}
    bad = speech(app, kind="elevenlabs", voice="v1", api_key="wrong-key-0000")
    assert (bad.status_code, bad.json()) == (502, {"error": "unauthorized"})
    assert app.secrets.values() == []  # a rejected key is never stored

    assert speech(app, kind="elevenlabs", voice="nope", api_key=SAMPLE_KEY).json() == {
        "error": "unknown_voice"
    }
    ok = speech(app, kind="elevenlabs", voice="v1", api_key=SAMPLE_KEY)
    assert ok.status_code == 200 and ok.json()["active"] is True
    assert app.secrets.get("elevenlabs/api_key") == SAMPLE_KEY
    assert SAMPLE_KEY not in ok.text and SAMPLE_KEY not in caplog.text
    option = next(o for o in ok.json()["options"] if o["kind"] == "elevenlabs")
    assert option["key"] == {"saved": True, "ends": "abcd"}
    assert app.rows("SELECT value_json FROM preferences WHERE key = 'speech'") == [
        ('{"kind": "elevenlabs", "voice": "v1"}',)
    ]

    body = {"kind": "elevenlabs"}
    gone = app.client.post("/api/settings/speech/forget-key", json=body, headers=app.auth)
    assert gone.json()["active"] is False and app.secrets.values() == []


def test_speech_can_be_turned_off_and_unknown_providers_are_refused(make: Any) -> None:
    app = make(model=False)
    speech(app, kind="system")
    assert speech(app, kind=None).json()["saved"] is None and app.rt.tts is None
    assert speech(app, kind="made-up").json() == {"error": "unknown_provider"}


def test_a_compatible_voice_needs_an_address_on_this_network_or_https(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.voice.providers.openai_compat import CompatibleSpeech
    from trillion.voice.types import Voice

    asked: list[str] = []

    async def voices(self: CompatibleSpeech) -> list[Voice]:
        asked.append(self._base)
        return [Voice(id="af_heart", name="Heart")]

    monkeypatch.setattr(CompatibleSpeech, "voices", voices)
    app = make(model=False)
    option = next(
        o
        for o in app.client.get("/api/settings/speech", headers=app.auth).json()["options"]
        if o["kind"] == "custom"
    )
    assert option["takes_url"] and not option["needs_key"]

    assert speech(app, kind="custom", voice="af_heart").json() == {"error": "bad_url"}
    public = speech(app, kind="custom", voice="af_heart", base_url="http://203.0.113.5/v1")
    assert public.json() == {"error": "insecure_url"}
    named = speech(app, kind="custom", voice="af_heart", base_url="http://voices.example:8880/v1")
    assert named.json() == {"error": "insecure_url"}  # a name can resolve anywhere
    assert asked == []  # nothing is sent to an address that fails the check

    url = "http://192.168.1.20:8880/v1/"
    assert speech(app, kind="custom", voice="nope", base_url=url).json() == {
        "error": "unknown_voice"
    }
    ok = speech(app, kind="custom", voice="af_heart", base_url=url)
    assert ok.status_code == 200 and ok.json()["active"] is True
    assert ok.json()["saved"]["base_url"] == "http://192.168.1.20:8880/v1"
    assert app.rows("SELECT value_json FROM preferences WHERE key = 'speech'") == [
        ('{"kind": "custom", "voice": "af_heart", "base_url": "http://192.168.1.20:8880/v1"}',)
    ]
    assert app.secrets.values() == []


def test_audio_from_a_core_voice_reaches_the_page_in_order(make: Any) -> None:
    from tests.voice.test_speaker import FakeVoice
    from trillion.api.runtime import SpeechSettings

    app = make(say("The first sentence is here. The second one follows."))
    app.rt.tts, app.rt.speech = FakeVoice(), SpeechSettings(kind="elevenlabs", voice="v1")
    with app.connect() as chat:
        chat.ws.send_json({"type": "turn.text", "text": "speak"})
        frames: list[tuple[int, int]] = []
        while True:
            message = receive(chat.ws)
            if message.get("bytes") is not None:
                kind, turn, seq = struct.unpack_from("<BII", message["bytes"])
                assert kind == 1
                frames.append((turn, seq))
            elif '"audio.end"' in (message.get("text") or ""):
                break
    assert {t for t, _ in frames} == {1}
    assert [s for _, s in frames] == sorted(s for _, s in frames) and {1, 2} == {
        s for _, s in frames
    }


# ── Listening: chosen in the app, generated from its catalog (VOI-11a, VOI-11b) ──


def listening(app: App, **body: Any) -> Any:
    return app.client.put("/api/settings/listening", json=body, headers=app.auth)


def test_listening_settings_need_the_token_and_list_the_catalog(make: Any) -> None:
    app = make(model=False)
    assert app.client.get("/api/settings/listening").status_code == 401
    body = app.client.get("/api/settings/listening", headers=app.auth).json()
    assert [o["kind"] for o in body["options"]] == ["whisper", "deepgram"]
    assert body["saved"] is None and body["active"] is False
    free = body["options"][0]
    assert free["free"] and not free["needs_key"] and free["install_hint"]
    assert listening(app, kind="nope", choice="x").json() == {"error": "unknown_provider"}


def test_a_hosted_recognizer_needs_a_key_that_works(
    make: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from trillion.voice.providers.deepgram import CHOICES, Deepgram
    from trillion.voice.types import Choice, VoiceError

    caplog.set_level(logging.DEBUG)

    async def choices(self: Deepgram) -> list[Choice]:
        if self._api_key() != SAMPLE_KEY:
            raise VoiceError("unauthorized", f"echo {self._api_key()}")
        return list(CHOICES)

    monkeypatch.setattr(Deepgram, "choices", choices)
    app = make(model=False)
    assert listening(app, kind="deepgram", choice="en").json() == {"error": "key_required"}
    bad = listening(app, kind="deepgram", choice="en", api_key="wrong-key-0000")
    assert (bad.status_code, bad.json()) == (502, {"error": "unauthorized"})
    assert app.secrets.values() == []  # a rejected key is never stored
    assert listening(app, kind="deepgram", choice="xx", api_key=SAMPLE_KEY).json() == {
        "error": "unknown_choice"
    }

    probe = {"kind": "deepgram", "api_key": SAMPLE_KEY}
    found = app.client.post("/api/settings/listening/choices", json=probe, headers=app.auth)
    assert [c["id"] for c in found.json()["choices"]] == ["en", "multi"]
    assert app.secrets.values() == []  # checking a key does not save it

    ok = listening(app, kind="deepgram", choice="multi", api_key=SAMPLE_KEY)
    assert ok.status_code == 200 and ok.json()["active"] is True
    assert ok.json()["saved"] == {"kind": "deepgram", "choice": "multi"}
    assert app.secrets.get("deepgram/api_key") == SAMPLE_KEY
    assert SAMPLE_KEY not in ok.text and SAMPLE_KEY not in caplog.text
    assert app.rows("SELECT value_json FROM preferences WHERE key = 'listening'") == [
        ('{"kind": "deepgram", "choice": "multi"}',)
    ]
    with app.connect() as chat:
        assert chat.session["listening"] == {
            "kind": "deepgram",
            "choice": "multi",
            "streaming": True,
        }
    assert app.rt.stt is not None and app.rt.stt.name == "deepgram"

    body = {"kind": "deepgram"}
    gone = app.client.post("/api/settings/listening/forget-key", json=body, headers=app.auth)
    assert gone.json()["active"] is False and app.secrets.values() == []
    assert listening(app).json()["saved"] is None  # and it can be turned off


def test_the_free_recognizer_says_what_it_needs_before_it_can_be_chosen(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.voice.providers.whisper import LocalWhisper
    from trillion.voice.types import Choice, VoiceError

    state = {"installed": False, "downloaded": False, "prepared": []}

    async def choices(self: LocalWhisper) -> list[Choice]:
        if not state["installed"]:
            raise VoiceError("not_installed")
        size = None if state["downloaded"] else 1000
        return [Choice(id="base.en", name="English, fast", download=size)]

    async def prepare(self: LocalWhisper, choice: str) -> None:
        state["prepared"].append(choice)  # type: ignore[union-attr]
        state["downloaded"] = True

    monkeypatch.setattr(LocalWhisper, "choices", choices)
    monkeypatch.setattr(LocalWhisper, "prepare", prepare)
    app = make(model=False)

    missing = listening(app, kind="whisper", choice="base.en")
    assert (missing.status_code, missing.json()) == (400, {"error": "not_installed"})
    state["installed"] = True
    assert listening(app, kind="whisper", choice="base.en").json() == {"error": "needs_download"}

    body = {"kind": "whisper", "choice": "base.en"}
    done = app.client.post("/api/settings/listening/prepare", json=body, headers=app.auth)
    assert done.json() == {
        "choices": [{"id": "base.en", "name": "English, fast", "download": None}]
    }
    assert state["prepared"] == ["base.en"]
    ok = listening(app, kind="whisper", choice="base.en")
    assert ok.json()["active"] is True and app.secrets.values() == []  # no key involved


def test_microphone_audio_arrives_as_binary_frames_and_never_breaks_the_socket(make: Any) -> None:
    app = make(say("hi"))
    with app.connect() as chat:
        chat.ws.send_bytes(b"\x00\x01" * 480)  # audio nobody asked for is dropped
        chat.ws.send_bytes(b"\x00" * 200_000)
        assert reply(chat.say("still there?")) == "hi"


# ── Reminders (AGT-4) ────────────────────────────────────────────────────


def test_the_scheduler_runs_while_the_server_does(make: Any) -> None:
    app = make()
    scheduler = app.rt.scheduler
    assert scheduler is not None and scheduler.running
    app.client.portal.call(app.rt.close)  # the runtime stops it first, before closing
    assert not scheduler.running
