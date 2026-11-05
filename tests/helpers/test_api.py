"""The helpers panel's API: the list, each helper's runs, "Talk to it", the refusals,
and `helpers.changed` pushed to every open page when a helper changes."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import ORIGIN, App, home, make  # noqa: F401
from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say
from tests.scheduler.conftest import FakeClock, ms, schedule
from trillion.api.helpers import CONVERSATIONS_PREF, note_for
from trillion.api.runtime import OWNER, Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import ContextBlock
from trillion.helpers import live
from trillion.persona import day
from trillion.scheduler.store import Schedule
from trillion.scheduler.when import Repeat
from trillion.secrets.store import MemoryStore
from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.spine.store import DecisionIn

PATH = "/api/helpers"
RID, AID = "b1b2c3d4", "c1c2c3d4"
NOW = ms("2099-10-24T12:00")


def routine(**kw: Any) -> Schedule:
    base = schedule(
        id=RID,
        kind="ask",
        title="Monday revenue",
        body="Show me last week's revenue.",
        at_local="2099-10-26T08:00",
        repeat=Repeat(every="weekday"),
        next_due_ms=ms("2099-10-26T08:00"),
        target="own",
    )
    return dataclasses.replace(base, **kw)


def ask(**kw: Any) -> Schedule:
    base = schedule(
        id=AID,
        kind="ask",
        title="Weather at 4",
        body="Tell me the weather.",
        at_local="2099-10-24T16:00",
        next_due_ms=ms("2099-10-24T16:00"),
    )
    return dataclasses.replace(base, **kw)


# ── Through the server: the token, the origin and each route's shape ──


def ready(app: App) -> None:
    """Its own background stopped and the clock set: only this test moves anything."""
    assert app.client.portal is not None
    app.client.portal.call(app.rt.stop_background)
    app.rt.clock = FakeClock(NOW)


def call(app: App, fn: Any, *args: Any) -> Any:
    assert app.client.portal is not None
    return app.client.portal.call(fn, *args)


def post(app: App, path: str, **kw: Any) -> Any:
    # A body, so the request is JSON like every other change (SPN-11).
    return app.client.post(path, headers={**app.auth, **kw.pop("headers", {})}, json={}, **kw)


def test_every_route_needs_the_token_and_our_origin(make: Any) -> None:
    app: App = make()
    assert app.client.get(PATH).status_code == 401
    assert app.client.get(f"{PATH}/routine:{RID}/runs").status_code == 401
    assert app.client.post(f"{PATH}/watcher:email/conversation", json={}).status_code == 401
    elsewhere = {"origin": "https://evil.example"}
    r = post(app, f"{PATH}/watcher:email/conversation", headers=elsewhere)
    assert r.status_code == 403
    assert app.client.get(PATH, headers={**app.auth, **elsewhere}).status_code == 403


def test_the_list_holds_routines_asks_and_watchers(make: Any) -> None:
    app: App = make()
    ready(app)
    assert app.rt.schedules is not None
    call(app, app.rt.schedules.create, routine())
    call(app, app.rt.schedules.create, ask())
    body = app.client.get(PATH, headers=app.auth).json()
    assert body["needs_you"] == 0
    rows = {h["id"]: h for h in body["helpers"]}
    assert set(rows) == {
        f"routine:{RID}",
        f"ask:{AID}",
        "watcher:meetings",
        "watcher:email",
        "watcher:payments",
    }
    assert rows[f"routine:{RID}"]["name"] == "Monday revenue"
    assert rows[f"ask:{AID}"]["job"] == "Sat 24 Oct at 4:00 pm"
    assert rows["watcher:email"]["name"] == "Urgent email"
    assert set(rows["watcher:email"]) == {
        "id",
        "kind",
        "name",
        "job",
        "state",
        "doing",
        "since_ms",
        "last_run",
        "next_ms",
        "waiting",
        "conversation_id",
        "paused",
        "switched_on",
        "connected",
    }


def test_a_routines_runs_newest_first(make: Any) -> None:
    app: App = make()
    ready(app)
    store = app.rt.schedules
    assert store is not None
    call(app, store.create, routine())
    for i, (status, detail) in enumerate([("nothing", "nothing"), ("skipped", "capped")]):
        at = NOW + i * 1000
        call(app, store.enqueue, RID, at, at + 86_400_000, 300.0, at)
        run = call(app, store.claim, "w1", at, 0)
        call(app, partial(store.finish, detail=detail), run.id, status, at + 10)
    r = app.client.get(f"{PATH}/routine:{RID}/runs", headers=app.auth)
    assert r.json() == {
        "runs": [
            {
                "at_ms": NOW + 1010,
                "outcome": "skipped",
                "reason": "paused at the spend cap",
                "conversation_id": None,
            },
            {"at_ms": NOW + 10, "outcome": "nothing", "reason": None, "conversation_id": None},
        ]
    }
    # Its kind must match: a routine is not an ask.
    assert app.client.get(f"{PATH}/ask:{RID}/runs", headers=app.auth).status_code == 404
    assert app.client.get(f"{PATH}/watcher:nobody/runs", headers=app.auth).status_code == 404
    assert app.client.get(f"{PATH}/nonsense/runs", headers=app.auth).status_code == 422


def test_a_watchers_runs_are_its_finds(make: Any) -> None:
    app: App = make()
    ready(app)
    store = app.rt.attention_store
    assert store is not None
    call(app, store.claim, OWNER, "email", ["email:1"], NOW - 1000)
    call(app, store.silent, OWNER, ["email:1"])
    call(app, store.claim, OWNER, "meetings", ["meeting:1"], NOW - 500)
    r = app.client.get(f"{PATH}/watcher:email/runs", headers=app.auth)
    assert r.json() == {
        "runs": [
            {"at_ms": NOW - 1000, "outcome": "nothing", "reason": None, "conversation_id": None}
        ]
    }


def test_talk_to_a_routine_opens_its_conversation(make: Any) -> None:
    app: App = make()
    ready(app)
    store = app.rt.schedules
    assert store is not None
    call(app, store.create, routine())
    # None yet: made as its first run would make it, and kept.
    first = post(app, f"{PATH}/routine:{RID}/conversation").json()["conversation_id"]
    kept = call(app, store.get, RID)
    assert kept.conversation_id == first
    again = post(app, f"{PATH}/routine:{RID}/conversation").json()["conversation_id"]
    assert again == first
    call(app, store.create, routine(id="d1d2d3d4", target="day"))
    got = post(app, f"{PATH}/routine:d1d2d3d4/conversation").json()["conversation_id"]
    date = day.today(NOW, app.rt.zone())
    found = call(app, app.rt.conversations.find_day, OWNER, date.isoformat())
    assert got == found


def test_talk_to_an_ask_opens_where_it_was_set(make: Any) -> None:
    app: App = make()
    ready(app)
    store = app.rt.schedules
    assert store is not None
    call(app, app.rt.conversations.create, "conv-set", OWNER, NOW)
    call(app, store.create, ask(created_in="conv-set"))
    r = post(app, f"{PATH}/ask:{AID}/conversation")
    assert r.json() == {"conversation_id": "conv-set"}
    call(app, store.create, ask(id="e1e2e3e4", created_in="gone"))
    assert post(app, f"{PATH}/ask:e1e2e3e4/conversation").status_code == 404
    assert post(app, f"{PATH}/ask:f1f2f3f4/conversation").status_code == 404


def test_talk_to_a_watcher_makes_one_conversation_and_reuses_it(make: Any) -> None:
    app: App = make()
    ready(app)
    first = post(app, f"{PATH}/watcher:email/conversation").json()["conversation_id"]
    again = post(app, f"{PATH}/watcher:email/conversation").json()["conversation_id"]
    assert first == again
    assert call(app, app.rt.preferences.get, OWNER, CONVERSATIONS_PREF) == {"watcher:email": first}
    title, _source = call(app, app.rt.conversations.title_of, first, OWNER)
    assert title == "Urgent email"
    rows = {h["id"]: h for h in app.client.get(PATH, headers=app.auth).json()["helpers"]}
    assert rows["watcher:email"]["conversation_id"] == first
    assert rows["watcher:meetings"]["conversation_id"] is None
    # Deleted since: a fresh one takes its place.
    call(app, app.rt.conversations.delete, first, OWNER, NOW)
    fresh = post(app, f"{PATH}/watcher:email/conversation").json()["conversation_id"]
    assert fresh != first
    assert post(app, f"{PATH}/watcher:nobody/conversation").status_code == 404


# ── In the runtime: the host note and the pushes ──


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(live, "DELAY_S", 0.01)
    runtime = await open_runtime(
        share_rules=lambda _p: None, persona=tmp_path, secrets=MemoryStore(), env={}
    )
    assert runtime.helpers_live is not None
    runtime.helpers_live._delay_s = 0.01
    yield runtime
    await runtime.close()


def notes_in(provider: ScriptedProvider, call_index: int) -> list[str]:
    _system, messages = provider.calls[call_index]
    return [b.text for b in messages[-1].blocks if isinstance(b, ContextBlock)]


async def test_a_watchers_conversation_carries_its_note_once_and_there_only(
    rt: Runtime,
) -> None:
    from trillion.api.helpers import create_helpers_router

    router = create_helpers_router(rt)
    (route,) = [r for r in router.routes if getattr(r, "path", "").endswith("/conversation")]
    made = (await route.endpoint(helper_id="watcher:meetings"))["conversation_id"]  # type: ignore[attr-defined]
    note = note_for("meetings")
    assert "Meetings watcher (meetings)" in note and "speak_up_widen" in note
    provider = ScriptedProvider(say("Noted."), say("Sure."), say("Hi."))
    rt.provider = provider
    elsewhere = await Session.start(rt, Page(rt).send, None)
    await elsewhere.handle({"type": "turn.text", "text": "hello"})
    await idle(elsewhere)
    await elsewhere.close()
    session = await Session.start(rt, Page(rt).send, made)
    for text in ("stop telling me about standups", "and lunch"):
        await session.handle({"type": "turn.text", "text": text})
        await idle(session)
    assert note not in notes_in(provider, 0)
    assert note in notes_in(provider, 1)
    assert note not in notes_in(provider, 2)
    await session.close()


async def pushed(page: Page, count: int) -> list[dict[str, Any]]:
    await page.until("helpers.changed", count)
    return page.all("helpers.changed")


async def test_a_run_starting_and_ending_is_pushed(rt: Runtime) -> None:
    await rt.stop_background()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    store = rt.schedules
    assert store is not None
    await store.create(routine())
    seen = await pushed(page, 1)
    assert seen[-1]["needs_you"] == 0 and any(
        h["id"] == f"routine:{RID}" for h in seen[-1]["helpers"]
    )
    now = rt.clock()
    await store.enqueue(RID, now, now + 86_400_000, 300.0, now)
    run = await store.claim("w1", now, 0)
    assert run is not None
    seen = await pushed(page, 2)
    (h,) = [h for h in seen[-1]["helpers"] if h["id"] == f"routine:{RID}"]
    assert h["state"] == "working"
    await store.finish(run.id, "nothing", rt.clock(), detail="nothing")
    seen = await pushed(page, 3)
    (h,) = [h for h in seen[-1]["helpers"] if h["id"] == f"routine:{RID}"]
    assert (h["state"], h["last_run"]["outcome"]) == ("idle", "nothing")
    await store.pause(OWNER, RID, rt.clock())
    seen = await pushed(page, 4)
    assert [h["state"] for h in seen[-1]["helpers"] if h["id"] == f"routine:{RID}"] == ["paused"]
    await store.resume(OWNER, RID, rt.clock() + 1000)
    await pushed(page, 5)
    await store.cancel(OWNER, RID, rt.clock())
    seen = await pushed(page, 6)
    assert not any(h["id"] == f"routine:{RID}" for h in seen[-1]["helpers"])
    await session.close()


async def test_a_burst_of_changes_is_one_push(rt: Runtime) -> None:
    await rt.stop_background()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    for _ in range(10):
        rt.helpers_changed()
    await pushed(page, 1)
    await asyncio.sleep(0.1)
    assert len(page.all("helpers.changed")) == 1
    await session.close()


async def test_a_card_raised_and_answered_is_pushed(rt: Runtime) -> None:
    await rt.stop_background()
    store = rt.schedules
    assert store is not None
    await store.create(routine(conversation_id="conv-r"))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await pushed(page, 1)
    now = rt.clock()
    card = PendingAction(
        id="p1",
        owner_id=OWNER,
        tool="file_create",
        args={"path": "/x"},
        args_hash="h",
        summary="Create the revenue sheet",
        target="/x",
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        session_id="conv-r",
        created_turn=1,
        status=Status.PROPOSED,
        created_at=now,
        expires_at=now + 600_000,
        represented_from=None,
    )
    await rt.spine_store.insert_pending(
        card, DecisionIn(event="proposed", decided_by="s", via="text")
    )
    seen = await pushed(page, 2)
    assert seen[-1]["needs_you"] == 1
    (h,) = [h for h in seen[-1]["helpers"] if h["id"] == f"routine:{RID}"]
    assert h["waiting"] == [
        {"type": "approval", "id": "p1", "text": "Approve a change", "at_ms": now}
    ]
    await rt.spine_store.transition(
        "p1",
        Status.PROPOSED,
        Status.REJECTED,
        now_ms=now + 1,
        decision=DecisionIn(event="rejected", decided_by="s", via="tap"),
    )
    seen = await pushed(page, 3)
    assert seen[-1]["needs_you"] == 0
    await session.close()


async def test_a_card_lapsing_clears_needs_you_on_time(rt: Runtime) -> None:
    await rt.stop_background()
    store = rt.schedules
    assert store is not None
    await store.create(routine(conversation_id="conv-r"))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    now = rt.clock()
    card = PendingAction(
        id="p1",
        owner_id=OWNER,
        tool="file_create",
        args={"path": "/x"},
        args_hash="h",
        summary="Create it",
        target="/x",
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        session_id="conv-r",
        created_turn=1,
        status=Status.PROPOSED,
        created_at=now,
        expires_at=now + 300,
        represented_from=None,
    )
    await rt.spine_store.insert_pending(
        card, DecisionIn(event="proposed", decided_by="s", via="text")
    )
    seen = await pushed(page, 1)
    assert seen[-1]["needs_you"] == 1
    seen = await pushed(page, 2)
    assert seen[-1]["needs_you"] == 0
    await session.close()


async def test_a_watcher_turned_on_or_checking_is_pushed(rt: Runtime) -> None:
    await rt.stop_background()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    assert rt.attention is not None
    await rt.attention.tick()
    # Meetings can't check with no Google connected; nothing started, nothing pushed.
    await asyncio.sleep(0.05)
    before = len(page.all("helpers.changed"))
    from trillion.api.attention import create_attention_router

    class Body:
        async def json(self) -> Any:
            return {"watchers": {"email": True}}

    router = create_attention_router(rt)
    (save,) = [
        r
        for r in router.routes
        if getattr(r, "path", "") == "/api/attention/settings"
        and "PUT" in getattr(r, "methods", ())
    ]
    await save.endpoint(Body())  # type: ignore[attr-defined]
    seen = await pushed(page, before + 1)
    (h,) = [h for h in seen[-1]["helpers"] if h["id"] == "watcher:email"]
    # On, with no Google connected: a step only the user can take.
    assert h["state"] == "needs_you"
    assert h["waiting"][0]["text"] == "Connect Google to use Urgent email"
    await session.close()


# ── Review fixes: connect and disconnect, locks, cleanup, caps ──


def counted(app: App) -> list[int]:
    told: list[int] = []
    app.rt.helpers_changed = lambda: told.append(1)  # type: ignore[method-assign]
    return told


def test_forgetting_the_google_client_is_pushed(make: Any) -> None:
    app: App = make()
    told = counted(app)
    r = post(app, "/api/google/client/forget")
    assert r.status_code == 200 and told == [1]


def test_removing_a_connected_server_is_pushed(make: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from trillion.connectors.mcp.config import parse_server
    from trillion.connectors.mcp.servers import ServerState

    app: App = make()
    mcp = app.rt.mcp
    assert mcp is not None
    mcp.states["stripe"] = ServerState(
        config=parse_server("stripe", {"url": "https://stripe.example/mcp"}),
        status="failed",
        tools=[],
    )

    async def gone(name: str) -> None:
        mcp.states.pop(name, None)

    monkeypatch.setattr(mcp, "remove", gone)
    told = counted(app)
    json_body = {"content-type": "application/json"}
    r = app.client.delete("/api/mcp/servers/stripe", headers={**app.auth, **json_body})
    assert r.status_code == 200, r.text
    assert told == [1]


def test_a_routine_conversation_not_linked_is_not_left_behind(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    app: App = make()
    ready(app)
    store = app.rt.schedules
    assert store is not None
    call(app, store.create, routine())

    async def broken(schedule_id: str, conversation_id: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "set_conversation", broken)
    with pytest.raises(OSError):
        post(app, f"{PATH}/routine:{RID}/conversation")
    live = app.rows("SELECT id FROM conversations WHERE kind = 'routine' AND deleted_at IS NULL")
    assert live == []


def test_a_watcher_conversation_not_remembered_is_not_left_behind(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    app: App = make()
    ready(app)

    async def broken(conversation_id: str, note: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(app.rt, "helper_note_keep", broken)
    with pytest.raises(OSError):
        post(app, f"{PATH}/watcher:email/conversation")
    titled = app.rows(
        "SELECT id FROM conversations WHERE title = 'Urgent email' AND deleted_at IS NULL"
    )
    assert titled == []


async def endpoint(rt: Runtime) -> Any:
    from trillion.api.helpers import create_helpers_router

    router = create_helpers_router(rt)
    (route,) = [r for r in router.routes if getattr(r, "path", "").endswith("/conversation")]
    return route.endpoint  # type: ignore[attr-defined]


async def test_two_taps_at_once_make_one_routine_conversation(rt: Runtime) -> None:
    await rt.stop_background()
    store = rt.schedules
    assert store is not None
    await store.create(routine())
    talk = await endpoint(rt)
    a, b = await asyncio.gather(talk(helper_id=f"routine:{RID}"), talk(helper_id=f"routine:{RID}"))
    assert a == b


async def test_notes_kept_and_said_at_once_all_hold(rt: Runtime) -> None:
    for c in ("c0", "c1", "c2"):
        await rt.conversations.create(c, OWNER, rt.clock())
    await rt.helper_note_keep("c0", "zero")
    await asyncio.gather(
        rt.helper_note_keep("c1", "one"),
        rt.helper_note_said("c0"),
        rt.helper_note_keep("c2", "two"),
    )
    assert await rt.helper_note("c0") is None
    assert (await rt.helper_note("c1"), await rt.helper_note("c2")) == ("one", "two")


async def test_a_cap_pausing_and_releasing_work_is_pushed(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[int] = []
    monkeypatch.setattr(rt, "helpers_changed", lambda: told.append(1))
    capped = [False]

    async def held(work: Any, provider_id: Any) -> bool:
        return capped[0]

    monkeypatch.setattr(rt.spend, "held", held)
    assert not await rt.held("routines")
    assert told == []
    capped[0] = True
    assert await rt.held("routines")
    assert await rt.held("routines")  # still paused: told once
    assert told == [1]
    capped[0] = False
    assert not await rt.held("routines")
    assert told == [1, 1]


async def test_a_page_opening_builds_the_list_for_it(rt: Runtime) -> None:
    await rt.stop_background()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await pushed(page, 1)
    await session.close()


def test_a_deleted_helper_conversation_is_forgotten_when_read(make: Any) -> None:
    app: App = make()
    ready(app)
    made = post(app, f"{PATH}/watcher:email/conversation").json()["conversation_id"]
    call(app, app.rt.conversations.delete, made, OWNER, NOW)
    rows = {h["id"]: h for h in app.client.get(PATH, headers=app.auth).json()["helpers"]}
    assert rows["watcher:email"]["conversation_id"] is None
    assert call(app, app.rt.preferences.get, OWNER, CONVERSATIONS_PREF) == {}
    assert call(app, app.rt.helper_note, made) is None
    assert call(app, app.rt.preferences.get, OWNER, "helper_notes") == {}


async def test_connections_changing_by_themselves_reach_the_panel(rt: Runtime) -> None:
    assert rt.google is not None and rt.mcp is not None
    told: list[int] = []
    rt.helpers_live = None
    original = rt.helpers_changed
    assert rt.google.changed == original and rt.mcp.moved == original
    rt.google.changed = rt.mcp.moved = lambda: told.append(1)
    rt.google.refresh_tools()
    assert told == [1]


async def test_a_note_for_a_deleted_conversation_is_forgotten_when_read(rt: Runtime) -> None:
    for c in ("c1", "c2"):
        await rt.conversations.create(c, OWNER, rt.clock())
        await rt.helper_note_keep(c, f"note {c}")
    await rt.conversations.delete("c2", OWNER, rt.clock())
    assert await rt.helper_note("c1") == "note c1"
    assert await rt.preferences.get(OWNER, "helper_notes") == {"c1": "note c1"}
