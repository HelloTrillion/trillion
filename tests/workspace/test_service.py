"""The board service: apply, save, tell the pages; a refused change changes nothing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.board_store import SqliteBoardStore
from trillion.storage.sqlite.conversation_store import SqliteConversationStore
from trillion.workspace import service
from trillion.workspace.board import Board, BoardError
from trillion.workspace.service import Boards, BoardSaveError, for_page

OWNER = "local"
STAT = {"type": "stat", "label": "Total", "value": "$41.20"}
LIST = {"type": "list", "items": [{"title": "Milk"}]}


class Pages:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Board | None]] = []

    async def __call__(self, conversation_id: str, board: Board | None) -> None:
        self.sent.append((conversation_id, board))


class Setup:
    def __init__(self, store: SqliteBoardStore, chats: SqliteConversationStore) -> None:
        self.store, self.chats, self.pages = store, chats, Pages()
        self.boards = Boards(store, chats, self.pages, clock=lambda: 5000, owner=OWNER)

    async def saved(self, cid: str = "c1") -> Board | None:
        return await self.store.get(cid, OWNER)


@pytest.fixture
async def s(tmp_path: Path) -> AsyncIterator[Setup]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    chats = SqliteConversationStore(conn)
    await chats.create("c1", OWNER, 1000)
    yield Setup(SqliteBoardStore(conn), chats)
    await conn.close()


async def test_show_saves_and_tells_every_page(s: Setup) -> None:
    board = await s.boards.show("c1", "October", [STAT, LIST])
    assert await s.saved() == board == await s.boards.current("c1")
    assert [w["id"] for w in board["widgets"]] == ["w1", "w2"] and board["open"]
    # The page gets the board without the server's id counter.
    assert s.pages.sent == [("c1", for_page(board))]
    assert "next_id" not in (s.pages.sent[0][1] or {})


async def test_a_refused_change_changes_nothing(s: Setup) -> None:
    board = await s.boards.show("c1", "October", [STAT])
    for bad in (
        s.boards.show("c1", None, [{"type": "chart"}]),
        s.boards.update("c1", "w9", LIST),
        s.boards.remove("c1", ["w7"]),
    ):
        with pytest.raises(BoardError):
            await bad
    assert await s.saved() == board
    assert len(s.pages.sent) == 1


async def test_only_a_live_chat_gets_a_board(s: Setup) -> None:
    await s.chats.create("sched", OWNER, 1000, kind="scheduled")
    await s.chats.create("gone", OWNER, 1000)
    await s.chats.delete("gone", OWNER, 2000)
    for cid in ("nowhere", "sched", "gone"):
        with pytest.raises(BoardError, match="no open conversation"):
            await s.boards.show(cid, "x", [STAT])
        assert await s.saved(cid) is None
    assert s.pages.sent == []


async def test_remove_says_what_went_and_the_last_one_closes_it(s: Setup) -> None:
    await s.boards.show("c1", "October", [STAT, LIST])
    board, gone = await s.boards.remove("c1", ["w1"])
    assert [w["id"] for w in gone] == ["w1"] and board["open"]
    board, gone = await s.boards.remove("c1", "all")
    assert [w["id"] for w in gone] == ["w2"] and not board["open"]


async def test_close_keeps_the_board(s: Setup) -> None:
    await s.boards.show("c1", "October", [STAT])
    closed = await s.boards.close("c1")
    assert not closed["open"] and closed["widgets"] and await s.saved() == closed
    reopened = await s.boards.show("c1", None, [])
    assert reopened["open"] and reopened["widgets"] == closed["widgets"]


async def test_the_page_changes_the_saved_board(s: Setup) -> None:
    await s.boards.show("c1", "October", [STAT, LIST])
    await s.boards.user_remove("c1", "w1")
    saved = await s.saved()
    assert saved is not None and [w["id"] for w in saved["widgets"]] == ["w2"]
    await s.boards.user_close("c1")
    saved = await s.saved()
    assert saved is not None and not saved["open"]


async def test_the_page_naming_nothing_is_ignored(s: Setup) -> None:
    await s.boards.user_close("c1")  # no board yet
    await s.boards.user_remove("c1", "w1")
    assert await s.saved() is None
    await s.boards.show("c1", "October", [STAT])
    sent = len(s.pages.sent)
    await s.boards.user_remove("c1", "w9")  # already gone
    await s.boards.user_remove("nowhere", "w1")
    assert len(s.pages.sent) == sent


async def test_a_failed_save_shows_nothing(s: Setup) -> None:
    async def broken(*_: Any) -> None:
        raise OSError("disk full")

    s.store.put = broken  # type: ignore[method-assign]
    with pytest.raises(BoardSaveError, match="could not be saved"):
        await s.boards.show("c1", "October", [STAT])
    assert s.pages.sent == []


async def test_pages_get_boards_in_the_order_they_were_saved(s: Setup) -> None:
    await s.boards.show("c1", "October", [STAT, LIST])
    gate = asyncio.Event()
    first = True

    async def slow_then_fast(cid: str, board: Board | None) -> None:
        nonlocal first
        if first:
            first = False
            await gate.wait()
        s.pages.sent.append((cid, board))

    s.boards._send_to = slow_then_fast
    removing = asyncio.create_task(s.boards.remove("c1", ["w1"]))
    closing = asyncio.create_task(s.boards.close("c1"))
    await asyncio.sleep(0.01)
    gate.set()
    await asyncio.gather(removing, closing)
    last_two = [b for _, b in s.pages.sent[-2:]]
    assert last_two[0] is not None and last_two[0]["open"]
    assert last_two[1] is not None and not last_two[1]["open"]


async def test_a_slow_page_cannot_hold_the_board(s: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "SEND_S", 0.01)

    async def stuck(*_: Any) -> None:
        await asyncio.sleep(10)

    s.boards._send_to = stuck
    board = await s.boards.show("c1", "October", [STAT])
    assert await s.saved() == board
