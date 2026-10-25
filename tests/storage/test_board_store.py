"""Saving her board per conversation (SUR-1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.board_store import BoardNotSaved, SqliteBoardStore
from trillion.storage.sqlite.conversation_store import SqliteConversationStore

OWNER = "local"
BOARD = {
    "title": "October spend",
    "open": True,
    "next_id": 2,
    "widgets": [{"id": "w1", "type": "stat", "size": "small", "label": "Total", "value": "€4"}],
}


@pytest.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    await SqliteConversationStore(conn).create("c1", OWNER, 1000)
    yield conn
    await conn.close()


async def updated_at(conn: aiosqlite.Connection, cid: str) -> int:
    async with conn.execute("SELECT updated_at FROM boards WHERE conversation_id = ?", (cid,)) as c:
        row = await c.fetchone()
    assert row is not None
    return int(row[0])


async def test_a_board_round_trips(conn: aiosqlite.Connection) -> None:
    store = SqliteBoardStore(conn)
    await store.put("c1", OWNER, BOARD, 2000)
    assert await store.get("c1", OWNER) == BOARD
    assert await updated_at(conn, "c1") == 2000


async def test_another_owner_or_conversation_has_none(conn: aiosqlite.Connection) -> None:
    store = SqliteBoardStore(conn)
    await store.put("c1", OWNER, BOARD, 2000)
    assert await store.get("c1", "someone-else") is None
    assert await store.get("c2", OWNER) is None


async def test_put_again_replaces_it(conn: aiosqlite.Connection) -> None:
    store = SqliteBoardStore(conn)
    await store.put("c1", OWNER, BOARD, 2000)
    closed = {**BOARD, "open": False}
    await store.put("c1", OWNER, closed, 3000)
    assert await store.get("c1", OWNER) == closed
    assert await updated_at(conn, "c1") == 3000


async def test_another_owner_cannot_overwrite_it(conn: aiosqlite.Connection) -> None:
    await conn.execute("INSERT INTO owners (id, created_at) VALUES ('other', 0)")
    store = SqliteBoardStore(conn)
    await store.put("c1", OWNER, BOARD, 2000)
    with pytest.raises(BoardNotSaved):
        await store.put("c1", "other", {**BOARD, "title": "x"}, 3000)
    assert await store.get("c1", OWNER) == BOARD
