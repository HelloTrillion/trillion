"""Her Home tool (the Home spec, D13): `home_arrange` moves, removes and adds back Home's
tiles at once, never with a card, only while the user is there, and tells the page."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.runtime import OWNER, Runtime, open_runtime
from trillion.api.session import Session
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import Risk, ToolCall, ToolContext, Via
from trillion.spine.registry import ToolRegistry, runs_in_routine, runs_unattended
from trillion.tools.home import ARRANGE
from trillion.workspace.home import DEFAULT, TILES

IDS = [t.id for t in TILES]


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    runtime = await open_runtime(
        share_rules=lambda _p: None, persona=tmp_path, secrets=MemoryStore(), env={}
    )
    yield runtime
    await runtime.close()


def ctx(via: Via = "text", *, read_outside: bool = False) -> ToolContext:
    return ToolContext(
        owner_id=OWNER, session_id="s1", turn=1, call_id="c1", via=via, read_outside=read_outside
    )


async def call(rt: Runtime, args: dict[str, Any], c: ToolContext | None = None) -> Any:
    return await rt.router.dispatch(ToolCall(name=ARRANGE, args=args), c or ctx())


def told(r: Any) -> dict[str, Any]:
    assert r.status == "succeeded", r
    return json.loads(r.content)  # type: ignore[no-any-return]


def test_it_is_a_write_that_never_asks() -> None:
    d = ToolRegistry.discover().definition(ARRANGE)
    assert d is not None and d.risk is Risk.WRITE and d.recoverable
    # Never offered to a run nobody is watching, a routine's included.
    assert not runs_unattended(d) and not runs_in_routine(d)
    # She can map "the revenue one" to its id.
    for t in TILES:
        assert f"{t.id} ({t.title})" in d.description


async def test_no_arguments_changes_nothing_and_says_the_layout(rt: Runtime) -> None:
    out = told(await call(rt, {}))
    assert out["changed"] is False
    assert [t["id"] for t in out["shown"]] == IDS and out["removed"] == []
    assert out["shown"][6] == {"id": "kpis", "title": "Revenue"}
    assert await rt.preferences.get(OWNER, "home_layout") is None


async def test_remove_order_add_and_reset(rt: Runtime) -> None:
    out = told(await call(rt, {"remove": ["kpis", "checks"]}))
    assert out["changed"] is True
    assert out["removed"] == [
        {"id": "kpis", "title": "Revenue"},
        {"id": "checks", "title": "What's broken"},
    ]
    assert list((await rt.home_layout()).gone()) == ["kpis", "checks"]
    out = told(await call(rt, {"order": ["inbox", "mentions"]}))
    assert [t["id"] for t in out["shown"]][:3] == ["inbox", "mentions", "next_up"]
    out = told(await call(rt, {"add": ["kpis"]}))
    assert [t["id"] for t in out["removed"]] == ["checks"]
    out = told(await call(rt, {"reset": True}))
    assert [t["id"] for t in out["shown"]] == IDS and out["removed"] == []
    assert await rt.home_layout() == DEFAULT


async def test_it_refuses_an_unknown_tile_and_both_adding_and_removing_one(rt: Runtime) -> None:
    assert (await call(rt, {"remove": ["weather"]})).status == "refused"
    r = await call(rt, {"remove": ["kpis"], "add": ["kpis"]})
    assert r.status == "refused"
    assert await rt.home_layout() == DEFAULT


@pytest.mark.parametrize("via", ["schedule", "routine", "ask", "refresh", "channel"])
async def test_refused_when_nobody_is_watching_and_from_a_channel(rt: Runtime, via: Via) -> None:
    r = await call(rt, {"remove": ["kpis"]}, ctx(via))
    assert r.status == "refused"
    assert await rt.home_layout() == DEFAULT


@pytest.mark.parametrize("via", ["voice", "text", "tap"])
async def test_it_runs_at_once_even_after_a_read_from_outside(rt: Runtime, via: Via) -> None:
    r = await call(rt, {"remove": ["kpis"]}, ctx(via, read_outside=True))
    # Never a card: the change is on Home, undone with one tap.
    assert r.status == "succeeded" and r.pending_id is None
    assert list((await rt.home_layout()).gone()) == ["kpis"]


async def test_her_change_tells_every_page_and_never_raises_a_card(rt: Runtime) -> None:
    provider = ScriptedProvider(
        use("home_arrange", {"remove": ["checks"], "order": ["kpis"]}), say("Done.")
    )
    rt.provider = provider
    page, other = Page(rt), Page(rt)
    session = await Session.start(rt, page.send, None)
    await Session.start(rt, other.send, None)
    await session.handle({"type": "turn.text", "text": "take what's broken off my home"})
    await idle(session)
    assert page.all("confirm.request") == []
    for p in (page, other):
        assert p.all("home") == [{"type": "home", "by": "her"}]
    layout = await rt.home_layout()
    assert layout.shown()[0] == "kpis" and list(layout.gone()) == ["checks"]


async def test_nothing_changed_tells_no_page(rt: Runtime) -> None:
    page = Page(rt)
    await Session.start(rt, page.send, None)
    told(await call(rt, {"add": ["kpis"]}))
    told(await call(rt, {}))
    assert page.all("home") == []
