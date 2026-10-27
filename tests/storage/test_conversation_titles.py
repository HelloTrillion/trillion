"""Conversation titles, recency and the thread list (SUR-1)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from importlib.resources import files
from pathlib import Path

import pytest

from trillion.brain.types import Msg, TextBlock
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import WORDS_TITLE_MAX, SqliteConversationStore

OWNER = "local"


def said(text: str) -> Msg:
    return Msg(role="user", blocks=(TextBlock(text=text),))


def reply(text: str) -> Msg:
    return Msg(role="assistant", blocks=(TextBlock(text=text),))


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteConversationStore]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    yield SqliteConversationStore(conn)
    await conn.close()


async def chat(store: SqliteConversationStore, cid: str, at: int, *lines: str) -> None:
    await store.create(cid, OWNER, at)
    for i, line in enumerate(lines):
        await store.append(cid, OWNER, "user", said(line), at + i)


async def test_the_first_words_title_a_conversation(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "  What's on\nFriday?  ", "And Saturday?")
    assert await store.title_of("c1", OWNER) == ("What's on Friday?", "words")


async def test_her_replies_never_title_it(store: SqliteConversationStore) -> None:
    await store.create("c1", OWNER, 1000)
    await store.append("c1", OWNER, "assistant", reply("Hello, I'm here."), 1000)
    assert await store.title_of("c1", OWNER) == (None, None)


async def test_a_long_first_message_is_cut(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "word " * 40)
    title, _ = await store.title_of("c1", OWNER)
    assert title is not None and len(title) <= WORDS_TITLE_MAX


async def test_a_model_title_never_replaces_a_users(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "hey")
    assert await store.set_title("c1", OWNER, "The trip", "user")
    assert not await store.set_title("c1", OWNER, "Greeting", "model")
    assert await store.title_of("c1", OWNER) == ("The trip", "user")
    assert await store.set_title("c1", OWNER, "Trip plans", "user")
    assert await store.title_of("c1", OWNER) == ("Trip plans", "user")


async def test_the_list_is_newest_first_and_pages(store: SqliteConversationStore) -> None:
    for n in range(5):
        await chat(store, f"c{n}", 1000 * (n + 1), f"line {n}")
    page, more = await store.list_chats(OWNER, limit=2)
    assert [r.id for r in page] == ["c4", "c3"] and more
    page, more = await store.list_chats(
        OWNER, before=(page[-1].last_message_at, page[-1].id), limit=2
    )
    assert [r.id for r in page] == ["c2", "c1"] and more
    page, more = await store.list_chats(
        OWNER, before=(page[-1].last_message_at, page[-1].id), limit=2
    )
    assert [r.id for r in page] == ["c0"] and not more


async def test_chats_at_the_same_moment_are_neither_skipped_nor_repeated(
    store: SqliteConversationStore,
) -> None:
    for cid in ("a", "b", "c"):
        await chat(store, cid, 1000, "hi")
    first, more = await store.list_chats(OWNER, limit=2)
    assert [r.id for r in first] == ["a", "b"] and more
    last = first[-1]
    second, more = await store.list_chats(OWNER, before=(last.last_message_at, last.id), limit=2)
    assert [r.id for r in second] == ["c"] and not more


async def test_empty_deleted_and_scheduled_are_not_listed(store: SqliteConversationStore) -> None:
    await store.create("empty", OWNER, 1000)
    await chat(store, "gone", 2000, "bye")
    await store.delete("gone", OWNER, 3000)
    await store.create("sched", OWNER, 1000, kind="scheduled")
    await store.append("sched", OWNER, "user", said("run it"), 1000)
    await chat(store, "kept", 4000, "hi")
    page, _ = await store.list_chats(OWNER)
    assert [r.id for r in page] == ["kept"]


async def test_delete_hides_and_restore_brings_back(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "hi")
    assert await store.delete("c1", OWNER, 2000)
    assert not await store.exists("c1", OWNER)
    assert await store.restore("c1", OWNER)
    assert await store.exists("c1", OWNER)
    assert not await store.delete("nope", OWNER, 2000)


async def test_search_matches_titles_and_words_not_json_keys(
    store: SqliteConversationStore,
) -> None:
    await chat(store, "a", 1000, "Book the dentist")
    await chat(store, "b", 2000, "hello")
    await store.append("b", OWNER, "assistant", reply("Your 50% off code arrived"), 2001)
    await chat(store, "c", 3000, "nothing here")
    ids = lambda rows: sorted(r.id for r in rows)  # noqa: E731
    assert ids((await store.list_chats(OWNER, query="DENTIST"))[0]) == ["a"]
    assert ids((await store.list_chats(OWNER, query="50%"))[0]) == ["b"]
    assert ids((await store.list_chats(OWNER, query="text"))[0]) == []
    assert ids((await store.list_chats(OWNER, query="_"))[0]) == []


async def test_recency_follows_the_newest_message(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "first")
    await store.append_many("c1", OWNER, "assistant", [reply("a"), reply("b")], 5000)
    page, _ = await store.list_chats(OWNER)
    assert page[0].last_message_at == 5000


async def test_the_migration_backfills_old_conversations(tmp_path: Path) -> None:
    # Everything before 010, then a conversation written the old way.
    before = tmp_path / "before"
    before.mkdir()
    for entry in (files("trillion.storage") / "migrations").iterdir():
        if entry.name.endswith(".sql") and entry.name < "010":
            (before / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    conn = await connect(tmp_path / "old.db")
    await migrate(conn, before)
    await conn.execute(
        "INSERT INTO conversations (id, owner_id, created_at) VALUES ('old', 'local', 1)"
    )
    await conn.execute(
        "INSERT INTO messages (owner_id, conversation_id, role, kind, blocks_json, created_at)"
        " VALUES ('local', 'old', 'user', 'user', ?, 7)",
        ('[{"t": "text", "text": "Plan the\\ntrip"}]',),
    )
    await conn.execute(
        "INSERT INTO conversations (id, owner_id, created_at) VALUES ('tabs', 'local', 1)"
    )
    # Cut at 60 characters lands on a space: the title must not end in one.
    tabbed = "a\tb" + "c" * 56 + " d"
    await conn.execute(
        "INSERT INTO messages (owner_id, conversation_id, role, kind, blocks_json, created_at)"
        " VALUES ('local', 'tabs', 'user', 'user', ?, 8)",
        (json.dumps([{"t": "text", "text": tabbed}]),),
    )
    await conn.execute(
        "INSERT INTO conversations (id, owner_id, created_at) VALUES ('blank', 'local', 1)"
    )
    await conn.execute(
        "INSERT INTO messages (owner_id, conversation_id, role, kind, blocks_json, created_at)"
        " VALUES ('local', 'blank', 'user', 'user', ?, 6)",
        (json.dumps([{"t": "text", "text": "  "}, {"t": "text", "text": "Real words"}]),),
    )
    await conn.execute(
        "INSERT INTO conversations (id, owner_id, created_at) VALUES ('bare', 'local', 1)"
    )
    await conn.execute(
        "INSERT INTO messages (owner_id, conversation_id, role, kind, blocks_json, created_at)"
        " VALUES ('local', 'bare', 'user', 'user', ?, 9)",
        (json.dumps([{"t": "context", "text": "no words"}]),),
    )
    assert await migrate(conn) == [
        "010_conversation_titles",
        "011_boards",
        "012_rule_events",
    ]
    store = SqliteConversationStore(conn)
    assert await store.title_of("old", OWNER) == ("Plan the trip", "words")
    assert (await store.list_chats(OWNER))[0][0].last_message_at == 9
    title, source = await store.title_of("tabs", OWNER)
    assert source == "words" and title is not None and title.startswith("a bc")
    assert "\t" not in title and title == title.strip() and len(title) <= 60
    assert await store.title_of("bare", OWNER) == (None, None)
    assert await store.title_of("blank", OWNER) == ("Real words", "words")
    await conn.close()
