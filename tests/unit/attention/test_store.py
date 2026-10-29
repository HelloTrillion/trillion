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
    # Claimed and not yet told: claimed until a notice says it, or the judge doesn't.
    assert {f.key: f.status for f in await store.recent(OWNER, NOW)} == {
        "a": "claimed",
        "b": "claimed",
        "c": "claimed",
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


async def test_a_note_is_added_as_a_line_until_they_are_full(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, _ = db
    assert await store.add_note(OWNER, "email", "Not the bank newsletters.")
    assert await store.add_note(OWNER, "email", "watch: sam@example.com")
    assert (await store.state(OWNER, "email")).notes == (
        "Not the bank newsletters.\nwatch: sam@example.com"
    )
    # Full: nothing is cut, nothing changes.
    assert not await store.add_note(OWNER, "email", "x" * 1000)
    assert (await store.state(OWNER, "email")).notes.endswith("sam@example.com")
    with pytest.raises(ValueError):
        await store.add_note(OWNER, "email", "two\nlines")
    # The last check and place are untouched by a note.
    await store.checked(OWNER, "payments", NOW, cursor="c1")
    assert await store.add_note(OWNER, "payments", "Only over $500.")
    s = await store.state(OWNER, "payments")
    assert (s.last_check_ms, s.cursor, s.notes) == (NOW, "c1", "Only over $500.")


async def test_a_claim_keeps_each_finds_detail(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, _ = db
    await store.claim(OWNER, "meetings", ["a", "b"], NOW, details={"a": "Planning at 10:00 am"})
    rows = {f.key: f.detail for f in await store.recent(OWNER, NOW)}
    assert rows == {"a": "Planning at 10:00 am", "b": None}


async def test_judged_not_worth_saying_is_silent_and_told_only_from_claimed(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, schedules = db
    await store.claim(OWNER, "email", ["email:1", "email:2"], NOW)
    await store.silent(OWNER, ["email:1"])
    await store.dismiss(OWNER, "email:2", NOW)
    n = await schedules.attention_notice(
        OWNER, "email:2", title="Urgent email", text=None, now_ms=NOW
    )
    # A dismissed one is not made told behind the user's back.
    await store.told(OWNER, ["email:1", "email:2"], n.id)
    rows = {f.key: f.status for f in await store.recent(OWNER, NOW)}
    assert rows == {"email:1": "silent", "email:2": "dismissed"}


async def test_repair_marks_told_a_claimed_find_whose_notice_was_written(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, schedules = db
    await store.claim(OWNER, "meetings", ["by_source", "by_id", "lost"], NOW)
    # The process stopped after writing each notice and before `told`.
    await schedules.attention_notice(
        OWNER, "by_source", title="Meeting soon", text=None, now_ms=NOW
    )
    await store.telling(OWNER, ["by_id"], "n-pending")
    await schedules.attention_notice(
        OWNER, "other", title="Meeting soon", text=None, now_ms=NOW, notice_id="n-pending"
    )
    assert await store.repair(OWNER) == 2
    rows = {f.key: f.status for f in await store.recent(OWNER, NOW)}
    # One with no notice stays claimed: never told again (D5), shown as lost.
    assert rows == {"by_source": "told", "by_id": "told", "lost": "claimed"}
    (by_id,) = [f for f in await store.recent(OWNER, NOW) if f.key == "by_id"]
    assert by_id.notice_id == "n-pending"


async def test_a_check_can_clear_its_cursor_and_a_reset_forgets_its_place(
    db: tuple[SqliteAttentionStore, SqliteScheduleStore],
) -> None:
    store, _ = db
    await store.checked(OWNER, "email", NOW, cursor="c1")
    await store.checked(OWNER, "email", NOW + 1, cursor=None)
    assert (await store.state(OWNER, "email")).cursor is None
    await store.checked(OWNER, "email", NOW + 2, cursor="c2")
    await store.set_notes(OWNER, "email", "Not the newsletters.")
    await store.reset(OWNER, "email")
    s = await store.state(OWNER, "email")
    assert (s.last_check_ms, s.cursor, s.notes) == (None, None, "Not the newsletters.")
