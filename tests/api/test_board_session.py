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
        share_rules=lambda _p: None,
        granted_roots=(granted,),
        persona=tmp_path,
        secrets=MemoryStore(),
        env={},
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
    # The page draws blocks; what she wrote stays on the server.
    assert board["widgets"][0]["blocks"] == [{"type": "value", "value": "$41.20", "label": "Total"}]
    assert board["widgets"][1]["blocks"][0]["type"] == "rows" and "items" not in board["widgets"][1]

    # A second page opening the conversation gets it as it was left.
    again = Page(rt)
    s2 = await Session.start(rt, again.send, s.id)
    types = [m["type"] for m in again.got]
    assert types.index("board") == types.index("history") + 1
    assert again.all("board")[0]["board"] == board
    await s2.close()
    await s.close()


async def test_the_pages_edits_change_the_saved_board_and_her_context(rt: Runtime) -> None:
    provider = ScriptedProvider(
        show(STAT, LIST), say("It's up."), say("Done."), say("Ok."), say("Back.")
    )
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

    # The message she was sent before the change is sent again byte for byte.
    assert provider.calls[2][1][0] == provider.calls[0][1][0]

    await s.handle({"type": "board.close"})
    assert page.all("board")[-1]["board"]["open"] is False
    await turn(s, page, "bye", 3)
    assert "Board (closed)" in context_of(provider, 3)
    assert provider.calls[3][1][:5] == provider.calls[2][1][:5]

    await s.handle({"type": "board.open"})
    assert page.all("board")[-1]["board"]["open"] is True
    await turn(s, page, "again", 4)
    assert 'Board (open): "October spend"' in context_of(provider, 4)
    await s.close()


async def test_no_board_no_line(rt: Runtime) -> None:
    provider = ScriptedProvider(say("Hello."))
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await turn(s, page, "hi", 1)
    assert "workspace" not in context_of(provider, 0)
    await s.close()


async def test_an_edit_that_changes_nothing_resends_the_board(rt: Runtime) -> None:
    """The page may have drawn the change already: it is told the board as it is."""
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    for kind in ("board.open", "board.close"):
        await s.handle({"type": kind})
    await s.handle({"type": "board.remove", "id": "w9"})
    assert page.all("board") == [{"type": "board", "board": None}] * 4
    assert page.all("error") == []
    await s.handle({"type": "board.remove"})
    assert page.all("error")[-1]["code"] == "bad_message"
    await s.close()


async def test_a_failed_edit_resends_the_saved_board(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(show(STAT, LIST), say("It's up."))
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await turn(s, page, "show me october", 1)
    shown = page.all("board")[-1]["board"]

    async def broken(*_: Any) -> None:
        raise OSError("disk full")

    rt.board_store.put = broken  # type: ignore[method-assign]
    await s.handle({"type": "board.remove", "id": "w2"})
    assert page.all("board")[-1]["board"] == shown

    assert rt.boards is not None

    async def worse(*_: Any) -> bool:
        raise RuntimeError("unexpected")

    rt.boards.user_close = worse  # type: ignore[method-assign]
    await s.handle({"type": "board.close"})
    assert page.all("board")[-1]["board"] == shown
    await s.close()


async def test_today_on_a_page_is_the_users_day_on_start_and_on_a_resend(rt: Runtime) -> None:
    """A day apart from the server's: the board a page is sent, outside her own
    changes, says "Today" on the user's day."""
    from datetime import date

    from trillion.workspace.blocks import day_in

    zone = next(z for z in ("Pacific/Kiritimati", "Pacific/Pago_Pago") if day_in(z) != date.today())
    rt.zone = lambda: zone  # type: ignore[method-assign]
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    assert rt.boards is not None
    due = {
        "type": "due_soon",
        "items": [{"title": "Send the deck", "due": day_in(zone).isoformat()}],
    }
    await rt.boards.show(s.id, None, [due])

    def label(board: dict[str, Any]) -> str:
        return str(board["widgets"][0]["blocks"][0]["label"])

    again = Page(rt)
    s2 = await Session.start(rt, again.send, s.id)
    assert label(again.all("board")[0]["board"]) == "Today"
    # Already open, so nothing changes and the page is sent the board as it is.
    await s2.handle({"type": "board.open"})
    assert len(again.all("board")) == 2 and label(again.all("board")[-1]["board"]) == "Today"
    await s2.close()
    await s.close()
