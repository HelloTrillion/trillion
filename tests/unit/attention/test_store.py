"""What the watchers keep: every find once (D5), and each watcher's place and notes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from trillion.attention.store import RECENT_MS
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.attention_store import SqliteAttentionStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

OWNER = "local"
NOW = 10 * 86_400_000


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[SqliteAttentionStore, SqliteScheduleStore]]:
    conn = await connect(tmp_path / "t.db")
    other = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        yield SqliteAttentionStore(conn), SqliteScheduleStore(other)
    finally:
        await conn.close()
        await other.close()


async def test_a_key_is_claimed_once(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, _ = db
    assert await store.claim(OWNER, "meetings", ["a", "b"], NOW) == ["a", "b"]
    assert await store.claim(OWNER, "meetings", ["b", "c", "c"], NOW) == ["c"]
    assert await store.known(OWNER, ["a", "c", "z"]) == {"a", "c"}
    # Claimed and not yet told: silent until a notice says it.
    assert {f.key: f.status for f in await store.recent(OWNER, NOW)} == {
        "a": "silent",
        "b": "silent",
        "c": "silent",
    }


async def test_told_finds_carry_their_notice_and_its_title(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, schedules = db
    await store.claim(OWNER, "email", ["email:1", "email:2"], NOW)
    n = await schedules.attention_notice(OWNER, "email:1", title="From Sam", text=None, now_ms=NOW)
    await store.told(OWNER, ["email:1", "email:2"], n.id)
    rows = await store.recent(OWNER, NOW)
    assert [(f.key, f.status, f.notice_id, f.title) for f in rows] == [
        ("email:1", "told", n.id, "From Sam"),
        ("email:2", "told", n.id, "From Sam"),
    ]


async def test_dismissing_is_a_soft_delete_that_still_counts_as_known(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, _ = db
    await store.claim(OWNER, "payments", ["payment:ch_1:failed"], NOW)
    assert await store.dismiss(OWNER, "payment:ch_1:failed", NOW + 5) is True
    assert await store.dismiss(OWNER, "payment:nope", NOW + 5) is False
    (f,) = await store.recent(OWNER, NOW + 5)
    assert (f.status, f.dismissed_ms) == ("dismissed", NOW + 5)
    assert await store.claim(OWNER, "payments", ["payment:ch_1:failed"], NOW + 6) == []


async def test_recent_is_the_last_fourteen_days_newest_first(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, _ = db
    await store.claim(OWNER, "email", ["old"], NOW - RECENT_MS - 1)
    await store.claim(OWNER, "email", ["older"], NOW - 10)
    await store.claim(OWNER, "email", ["newer"], NOW)
    assert [f.key for f in await store.recent(OWNER, NOW)] == ["newer", "older"]


async def test_a_watchers_state_is_kept(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, _ = db
    blank = await store.state(OWNER, "email")
    assert (blank.last_check_ms, blank.cursor, blank.last_error, blank.notes) == (
        None,
        None,
        None,
        "",
    )
    await store.checked(OWNER, "email", NOW, cursor="c1")
    await store.failed(OWNER, "email", "TimeoutError")
    s = await store.state(OWNER, "email")
    assert (s.last_check_ms, s.cursor, s.last_error) == (NOW, "c1", "TimeoutError")
    # A good check clears the error and keeps the cursor when it gives none.
    await store.checked(OWNER, "email", NOW + 1)
    s = await store.state(OWNER, "email")
    assert (s.last_check_ms, s.cursor, s.last_error) == (NOW + 1, "c1", None)
    assert [x.watcher for x in await store.states(OWNER)] == ["email"]


async def test_notes_are_set_and_cleared(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, _ = db
    await store.set_notes(OWNER, "email", "Not the bank newsletters.")
    assert (await store.state(OWNER, "email")).notes == "Not the bank newsletters."
    with pytest.raises(ValueError):
        await store.set_notes(OWNER, "email", "x" * 1001)
    await store.clear_notes(OWNER, "email")
    assert (await store.state(OWNER, "email")).notes == ""
