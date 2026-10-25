"""Her board in a live session: on start, from her tools, from the page's edits, and in
what she is told each turn (SUR-1, D3, D7)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page
from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import ContextBlock
from trillion.secrets.store import MemoryStore

STAT = {"type": "stat", "label": "Total", "value": "$41.20"}
LIST = {"type": "list", "title": "Biggest", "items": [{"title": "Rent"}]}


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={}
    )
    yield runtime
    await runtime.close()


def show(*widgets: dict[str, Any]) -> list[Any]:
    return use("workspace_show", {"title": "October spend", "widgets": list(widgets)})


def context_of(provider: ScriptedProvider, call: int) -> str:
    """The context the newest user message of one model call carried."""
    _, messages = provider.calls[call]
    user = [m for m in messages if m.role == "user" and isinstance(m.blocks[0], ContextBlock)]
    block = user[-1].blocks[0]
    assert isinstance(block, ContextBlock)
    return block.text


async def turn(session: Session, page: Page, text: str, count: int) -> None:
    await session.handle({"type": "turn.text", "text": text})
    await page.until("turn.complete", count)


async def test_her_board_reaches_the_page_and_comes_back_on_reopen(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(show(STAT, LIST), say("It's up."))
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    assert page.all("board") == [{"type": "board", "board": None}]
    await turn(s, page, "show me october", 1)
    board = page.all("board")[-1]["board"]
    assert board["title"] == "October spend" and board["open"] is True
    assert [w["id"] for w in board["widgets"]] == ["w1", "w2"]
    assert "next_id" not in board  # the counter is the server's own

    # A second page opening the conversation gets it as it was left.
    again = Page(rt)
    s2 = await Session.start(rt, again.send, s.id)
    types = [m["type"] for m in again.got]
    assert types.index("board") == types.index("history") + 1
    assert again.all("board")[0]["board"] == board
    await s2.close()
    await s.close()


async def test_the_pages_edits_change_the_saved_board_and_her_context(rt: Runtime) -> None:
    provider = ScriptedProvider(show(STAT, LIST), say("It's up."), say("Done."), say("Ok."))
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await turn(s, page, "show me october", 1)

    await s.handle({"type": "board.remove", "id": "w2"})
    assert rt.boards is not None
    board = await rt.boards.current(s.id)
    assert board is not None and [w["id"] for w in board["widgets"]] == ["w1"]
    assert [w["id"] for w in page.all("board")[-1]["board"]["widgets"]] == ["w1"]
    await turn(s, page, "thanks", 2)
    line = context_of(provider, 2)
    assert '<untrusted_tool_result tool="workspace">' in line
    assert 'Board (open): "October spend": w1 stat "Total".' in line
    assert "Biggest" not in line

    await s.handle({"type": "board.close"})
    assert page.all("board")[-1]["board"]["open"] is False
    await turn(s, page, "bye", 3)
    assert "Board (closed)" in context_of(provider, 3)
    await s.close()


async def test_no_board_no_line(rt: Runtime) -> None:
    provider = ScriptedProvider(say("Hello."))
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await turn(s, page, "hi", 1)
    assert "workspace" not in context_of(provider, 0)
    await s.close()


async def test_an_unknown_widget_or_an_empty_board_is_ignored(rt: Runtime) -> None:
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "board.close"})
    await s.handle({"type": "board.remove", "id": "w9"})
    assert page.all("board") == [{"type": "board", "board": None}]
    assert page.all("error") == []
    await s.handle({"type": "board.remove"})
    assert page.all("error")[-1]["code"] == "bad_message"
    await s.close()
