"""A watcher's notice (the speaking up spec), and the migration that makes room for it."""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest

from tests.scheduler.conftest import NY, runs, written
from trillion.scheduler.store import https_link
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

DUE = 1_000_000
MEET = "https://meet.example.com/abc"


async def test_an_attention_notice_is_held_with_its_link_and_freshness(
    store: SqliteScheduleStore,
) -> None:
    n = await store.attention_notice(
        "local",
        "meeting:e1:1700000000000",
        title="Planning with Sam",
        text="Your 10:00 with Sam starts in five minutes.",
        now_ms=DUE,
        link=MEET,
        fresh_until_ms=DUE + 300_000,
    )
    kept = await store.notice(n.id)
    assert kept == n
    assert (kept.source, kept.source_id, kept.kind) == (
        "attention",
        "meeting:e1:1700000000000",
        "heads_up",
    )
    assert (kept.link, kept.fresh_until_ms, kept.status) == (MEET, DUE + 300_000, "held")
    assert [x.id for x in await store.held("local")] == [n.id]


async def test_an_attention_notice_with_no_words_is_kept_by_its_title(
    store: SqliteScheduleStore,
) -> None:
    n = await store.attention_notice("local", "email:m1", title="From Sam", text=None, now_ms=DUE)
    assert (n.text, n.link, n.fresh_until_ms) == (None, None, None)


@pytest.mark.parametrize("link", ["http://meet.example.com/abc", "javascript:alert(1)", ""])
async def test_only_an_https_link_is_kept(store: SqliteScheduleStore, link: str) -> None:
    with pytest.raises(ValueError):
        await store.attention_notice(
            "local", "meeting:e1:1", title="Sam", text="Soon.", now_ms=DUE, link=link
        )
    assert await store.held("local") == []
    # The database refuses one too, whoever writes it.
    with pytest.raises(sqlite3.IntegrityError):
        await store._conn.execute(
            "INSERT INTO notices (id, owner_id, source, source_id, kind, title, due_ms, late,"
            " created_ms, status, link) VALUES ('x', 'local', 'attention', 'k', 'heads_up',"
            " 'T', 1, 0, 1, 'held', ?)",
            (link,),
        )


@pytest.mark.parametrize(
    "link",
    [
        "https://",
        "https:///path",
        "https://:443/x",
        "HTTPS://meet.example.com/abc",
        "https://user:pass@meet.example.com/abc",
        "https://meet.example.com@elsewhere.example.com/x",
        "https://meet.example.com/a b",
        "https://meet.example.com/a\tb",
        "https://meet.example.com/a\nb",
        "https://meet.example.com/\x00",
        "https://meet.example.com/\x7f",
        " https://meet.example.com/abc",
        "https://[::1/x",
        "https://meet.example.com/" + "x" * 2000,
    ],
)
def test_a_link_that_is_not_plainly_https_somewhere_is_refused(link: str) -> None:
    with pytest.raises(ValueError):
        https_link(link)


@pytest.mark.parametrize(
    "link",
    [
        MEET,
        "https://meet.example.com",
        "https://zoom.example.com/j/123?pwd=abc#x",
        "https://meet.example.com:8443/abc",
        "https://meet.example.com/" + "x" * (2000 - len("https://meet.example.com/")),
    ],
)
def test_a_plain_https_link_is_kept(link: str) -> None:
    assert https_link(link) == link


async def test_a_schedule_notice_is_still_a_schedules(store: SqliteScheduleStore) -> None:
    n = await written(store, due_ms=DUE, now_ms=DUE)
    assert (n.source, n.link, n.fresh_until_ms) == ("schedule", None, None)
    kept = await store.notice(n.id)
    assert kept is not None and kept.source == "schedule"


async def test_the_migration_keeps_every_notice_and_reference(tmp_path: Path) -> None:
    before = tmp_path / "before"
    before.mkdir()
    for entry in (files("trillion.storage") / "migrations").iterdir():
        if entry.name.endswith(".sql") and entry.name < "015":
            (before / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    conn = await connect(tmp_path / "old.db")
    await migrate(conn, before)
    await conn.execute(
        "INSERT INTO schedules (id, owner_id, kind, title, body, zone, at_local, repeat,"
        " next_due_ms, status, created_ms, created_in)"
        " VALUES ('s1', 'local', 'ask', 'Email', 'Check.', ?, '2026-10-26T09:00', NULL, 9,"
        " 'active', 0, 'c1')",
        (NY,),
    )
    # As the store before this migration wrote them: one held, one delivered and shown.
    await conn.execute(
        "INSERT INTO notices (id, owner_id, source, source_id, kind, title, text, due_ms,"
        " late, created_ms, status, conversation_id) VALUES ('n1', 'local', 'schedule', 's1',"
        " 'ready', 'Email', 'Ready.', 9, 0, 9, 'held', 'conv-1')"
    )
    await conn.execute(
        "INSERT INTO notices (id, owner_id, source, source_id, kind, title, text, due_ms,"
        " late, created_ms, status, delivered_ms, delivered_to, notified_ms)"
        " VALUES ('n2', 'local', 'schedule', 's1', 'ask', 'Email', 'Two emails.', 9, 1, 10,"
        " 'delivered', 11, 'c1', 12)"
    )
    await conn.execute(
        "INSERT INTO task_runs (id, schedule_id, due_ms, status, timeout_s, attempts,"
        " result_notice, created_ms) VALUES (41, 's1', 9, 'delivered', 120, 1, 'n2', 9)"
    )
    await conn.commit()
    assert await migrate(conn) == ["015_attention"]
    async with conn.execute("PRAGMA foreign_key_check") as cur:
        assert await cur.fetchall() == []
    store = SqliteScheduleStore(conn)
    held = await store.notice("n1")
    assert held is not None
    assert (held.kind, held.text, held.conversation_id, held.status) == (
        "ready",
        "Ready.",
        "conv-1",
        "held",
    )
    assert (held.source, held.link, held.fresh_until_ms) == ("schedule", None, None)
    done = await store.notice("n2")
    assert done is not None
    assert (done.late, done.delivered_ms, done.delivered_to, done.notified_ms) == (
        True,
        11,
        "c1",
        12,
    )
    (run,) = await runs(store, "s1")
    assert run.result_notice == "n2"
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE name IN"
        " ('notices_held', 'attention_finds', 'attention_state')"
    ) as cur:
        assert {r[0] for r in await cur.fetchall()} == {
            "notices_held",
            "attention_finds",
            "attention_state",
        }
    await conn.close()


async def test_a_find_is_kept_once_per_owner_and_key(store: SqliteScheduleStore) -> None:
    insert = (
        "INSERT INTO attention_finds (owner_id, key, watcher, first_seen_ms, status)"
        " VALUES ('local', 'email:m1', 'email', 1, 'silent')"
    )
    await store._conn.execute(insert)
    with pytest.raises(sqlite3.IntegrityError):
        await store._conn.execute(insert)
    with pytest.raises(sqlite3.IntegrityError):
        await store._conn.execute(
            "INSERT INTO attention_finds (owner_id, key, watcher, first_seen_ms, status)"
            " VALUES ('local', 'email:m2', 'email', 1, 'forgotten')"
        )
    await store._conn.execute(
        "INSERT INTO attention_state (owner_id, watcher, last_check_ms)"
        " VALUES ('local', 'email', 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await store._conn.execute(
            "INSERT INTO attention_state (owner_id, watcher) VALUES ('local', 'email')"
        )
