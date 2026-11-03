"""Your day (SUR-1): one day conversation per date, kept like any other thread."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from importlib.resources import files
from pathlib import Path

import pytest

from trillion.brain.types import Msg, TextBlock
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import SqliteConversationStore

OWNER = "local"
TITLE = "Your day · Mon 26 Oct"


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteConversationStore]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    yield SqliteConversationStore(conn)
    await conn.close()


async def test_one_day_conversation_per_date(store: SqliteConversationStore) -> None:
    cid, created = await store.day_conversation(OWNER, "2026-10-26", TITLE, 1000)
    assert created
    again, created = await store.day_conversation(OWNER, "2026-10-26", "other", 2000)
    assert (again, created) == (cid, False)
    other, created = await store.day_conversation(OWNER, "2026-10-27", "Your day · Tue", 3000)
    assert created and other != cid
    assert await store.kind_of(cid, OWNER) == ("day", "2026-10-26")
    # Named by the host, so her naming never replaces it.
    assert await store.title_of(cid, OWNER) == (TITLE, "user")
    assert await store.exists(cid, OWNER)


async def test_two_rows_for_one_date_are_refused(store: SqliteConversationStore) -> None:
    await store.day_conversation(OWNER, "2026-10-26", TITLE, 1000)
    conn = store._conn
    with pytest.raises(sqlite3.IntegrityError):
        await conn.execute(
            "INSERT INTO conversations (id, owner_id, created_at, kind, day)"
            " VALUES ('x', 'local', 1, 'day', '2026-10-26')"
        )
    with pytest.raises(sqlite3.IntegrityError):  # a day conversation has a date
        await conn.execute(
            "INSERT INTO conversations (id, owner_id, created_at, kind)"
            " VALUES ('y', 'local', 1, 'day')"
        )


async def test_a_deleted_day_gives_a_fresh_one(store: SqliteConversationStore) -> None:
    cid, _ = await store.day_conversation(OWNER, "2026-10-26", TITLE, 1000)
    assert await store.delete(cid, OWNER, 1500)
    fresh, created = await store.day_conversation(OWNER, "2026-10-26", TITLE, 2000)
    assert created and fresh != cid and not await store.exists(cid, OWNER)
    # The old one cannot come back beside it: one live day per date.
    assert not await store.restore(cid, OWNER)
    assert await store.delete(fresh, OWNER, 2500)
    assert await store.restore(cid, OWNER) and await store.exists(cid, OWNER)


async def test_lookups_by_conversation_are_indexed(store: SqliteConversationStore) -> None:
    async with store._conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'") as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert {"usage_by_conversation", "turn_timings_by_conversation"} <= names


async def test_the_list_carries_kind_and_day(store: SqliteConversationStore) -> None:
    cid, _ = await store.day_conversation(OWNER, "2026-10-26", TITLE, 1000)
    await store.create("c1", OWNER, 1000)
    msg = Msg(role="user", blocks=(TextBlock(text="hello"),))
    await store.append("c1", OWNER, "user", msg, 1000)
    await store.append(cid, OWNER, "assistant", Msg(role="assistant", blocks=msg.blocks), 2000)
    rows, _ = await store.list_chats(OWNER)
    assert [(r.id, r.kind, r.day) for r in rows] == [
        (cid, "day", "2026-10-26"),
        ("c1", "chat", None),
    ]
    assert rows[0].title == TITLE
    assert await store.kind_of("c1", OWNER) == ("chat", None)
    assert await store.kind_of("missing", OWNER) is None


async def test_the_migration_keeps_every_row_and_reference(tmp_path: Path) -> None:
    before = tmp_path / "before"
    before.mkdir()
    for entry in (files("trillion.storage") / "migrations").iterdir():
        if entry.name.endswith(".sql") and entry.name < "013":
            (before / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    conn = await connect(tmp_path / "old.db")
    await migrate(conn, before)
    old = SqliteConversationStore(conn)
    await old.create("c1", OWNER, 1)
    await old.append("c1", OWNER, "user", Msg(role="user", blocks=(TextBlock(text="hi"),)), 5)
    await old.set_title("c1", OWNER, "Mine", "user")
    await conn.execute(
        "INSERT INTO boards (conversation_id, owner_id, board_json, updated_at)"
        " VALUES ('c1', 'local', '{}', 1)"
    )
    assert await migrate(conn) == [
        "013_day_conversations",
        "014_routines",
        "015_attention",
        "016_decision_events",
        "017_turn_cues",
    ]
    async with conn.execute("PRAGMA foreign_key_check") as cur:
        assert await cur.fetchall() == []
    store = SqliteConversationStore(conn)
    assert await store.title_of("c1", OWNER) == ("Mine", "user")
    assert [m.blocks for _, m in await store.history("c1", OWNER)] == [(TextBlock(text="hi"),)]
    rows, _ = await store.list_chats(OWNER)
    assert [(r.id, r.kind, r.day, r.last_message_at) for r in rows] == [("c1", "chat", None, 5)]
    await conn.close()
