"""What a routine keeps (the routines spec): its target, notes, pause and failures, a
'ready' notice with the conversation to open, and the migration that made room."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from tests.scheduler.conftest import NY, ms, runs, schedule, written
from trillion.brain.types import Msg, TextBlock
from trillion.scheduler.store import PAUSE_AFTER, Schedule
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import SqliteConversationStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

DUE = ms("2026-10-26T08:00")


def routine(**kw: Any) -> Schedule:
    return schedule(
        kind="ask",
        title="Monday revenue",
        body="Show me last week's revenue.",
        at_local="2026-10-26T08:00",
        next_due_ms=DUE,
        **kw,
    )


async def test_a_routine_keeps_its_target_notes_and_conversation(
    store: SqliteScheduleStore,
) -> None:
    await store.create(routine(target="own", notes="Leave out refunds."))
    s = await store.get("a1b2c3d4")
    assert s is not None
    assert (s.target, s.conversation_id, s.notes, s.paused_ms, s.failures) == (
        "own",
        None,
        "Leave out refunds.",
        None,
        0,
    )
    await store.set_conversation("a1b2c3d4", "c9")
    s = await store.get("a1b2c3d4")
    assert s is not None and s.conversation_id == "c9"


async def test_a_plain_timed_ask_has_no_target(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    s = await store.get("a1b2c3d4")
    assert s is not None and s.target is None and s.notes == "" and s.failures == 0


async def test_notes_are_kept_short(store: SqliteScheduleStore) -> None:
    with pytest.raises(Exception, match="CHECK"):
        await store.create(routine(target="own", notes="x" * 1001))


async def test_a_paused_routine_is_never_due_and_its_time_waits(
    store: SqliteScheduleStore,
) -> None:
    await store.create(routine(target="own"))
    assert await store.pause("local", "a1b2c3d4", DUE - 1)
    assert not await store.pause("local", "a1b2c3d4", DUE)  # already
    assert await store.due(DUE + 60_000) == []
    s = await store.get("a1b2c3d4")
    assert s is not None and s.next_due_ms == DUE and s.paused_ms == DUE - 1
    later = ms("2026-11-02T08:00")
    assert await store.resume("local", "a1b2c3d4", later)
    s = await store.get("a1b2c3d4")
    assert s is not None and s.paused_ms is None and s.next_due_ms == later


async def test_only_a_routine_pauses(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    assert not await store.pause("local", "a1b2c3d4", DUE)


async def running(store: SqliteScheduleStore, due: int) -> int:
    """A run of the routine, claimed and running."""
    await store.enqueue("a1b2c3d4", due, DUE, 300, 0)
    run = await store.claim("w", due, 0)
    assert run is not None and run.due_ms == due
    return run.id


async def fail(store: SqliteScheduleStore, run_id: int) -> str | None:
    n = await store.finish_with_notice(
        run_id,
        "failed",
        run_id,
        owner_id="local",
        source_id="a1b2c3d4",
        kind="failed",
        title="Monday revenue",
        text=None,
        due_ms=1,
        late=False,
        pauses_as="paused now",
    )
    return n.title if n is not None else None


async def test_failures_count_with_their_notice_pause_once_and_reset(
    store: SqliteScheduleStore,
) -> None:
    await store.create(routine(target="own"))
    assert await fail(store, await running(store, 1)) == "Monday revenue"
    await store.succeeded("a1b2c3d4")
    titles = [await fail(store, await running(store, due)) for due in range(2, PAUSE_AFTER + 3)]
    assert titles == ["Monday revenue", "Monday revenue", "paused now", "Monday revenue"]
    s = await store.get("a1b2c3d4")
    assert s is not None and s.failures == 4 and s.paused_ms is not None
    assert await store.resume("local", "a1b2c3d4", DUE)
    s = await store.get("a1b2c3d4")
    assert s is not None and s.failures == 0


async def test_a_run_already_finished_is_not_counted(store: SqliteScheduleStore) -> None:
    await store.create(routine(target="own"))
    run_id = await running(store, 1)
    assert await fail(store, run_id) == "Monday revenue"
    assert await fail(store, run_id) is None  # finished: no notice, and no count
    s = await store.get("a1b2c3d4")
    assert s is not None and s.failures == 1


async def test_a_ready_notice_carries_the_conversation(store: SqliteScheduleStore) -> None:
    n = await written(store, kind="reminder", due_ms=DUE, now_ms=DUE)
    assert n.conversation_id is None
    async with store._tx() as conn:
        cur = await conn.execute(
            "INSERT INTO task_runs (schedule_id, due_ms, status, claimed_by, claimed_ms,"
            " started_ms, timeout_s, attempts, created_ms)"
            " VALUES ('a1b2c3d4', 7, 'running', 'test', 1, 1, 10, 1, 1)"
        )
        run_id = cur.lastrowid
    assert run_id is not None
    ready = await store.finish_with_notice(
        run_id,
        "delivered",
        DUE,
        owner_id="local",
        source_id="a1b2c3d4",
        kind="ready",
        title="Your Monday revenue is ready",
        text=None,
        due_ms=DUE,
        late=False,
        conversation_id="c9",
    )
    assert ready is not None and ready.conversation_id == "c9"
    kept = await store.notice(ready.id)
    assert kept is not None and (kept.kind, kept.conversation_id) == ("ready", "c9")
    assert [n.id for n in await store.held("local")] == [n.id, ready.id]
    assert {r.status for r in await runs(store, "a1b2c3d4")} == {"delivered"}


async def test_a_ready_notice_with_no_run_is_held_for_the_door(
    store: SqliteScheduleStore,
) -> None:
    # A background read that ended with nobody on its conversation (the background
    # reads spec): no schedule, no run, only the line and where it opens.
    n = await store.ready_notice("local", "job-3f2a", "Stripe numbers are in", DUE, "c9")
    kept = await store.notice(n.id)
    assert kept is not None
    assert (kept.kind, kept.title, kept.text, kept.conversation_id, kept.status) == (
        "ready",
        "Stripe numbers are in",
        "Stripe numbers are in.",
        "c9",
        "held",
    )
    assert [h.id for h in await store.held("local")] == [n.id]


async def test_a_run_can_end_skipped_or_with_nothing(store: SqliteScheduleStore) -> None:
    await store.create(routine(target="own"))
    for due, status in ((1, "skipped"), (2, "nothing")):
        await store.enqueue("a1b2c3d4", due, DUE, 120, 0)
        run = await store.claim("w", 10, 0)
        assert run is not None
        assert await store.finish(run.id, status, 10)  # type: ignore[arg-type]
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["skipped", "nothing"]


async def test_a_routine_conversation_is_titled_and_opened_by_a_page(tmp_path: Path) -> None:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    chats = SqliteConversationStore(conn)
    cid = await chats.routine_conversation("local", "Monday revenue", 1)
    assert await chats.kind_of(cid, "local") == ("routine", None)
    assert await chats.exists(cid, "local")
    assert await chats.title_of(cid, "local") == ("Monday revenue", "user")
    # Her naming never replaces it.
    assert not await chats.set_title(cid, "local", "Revenue talk", "model")
    await conn.close()


async def test_the_migration_keeps_every_row_and_reference(tmp_path: Path) -> None:
    before = tmp_path / "before"
    before.mkdir()
    for entry in (files("trillion.storage") / "migrations").iterdir():
        if entry.name.endswith(".sql") and entry.name < "014":
            (before / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    conn = await connect(tmp_path / "old.db")
    await migrate(conn, before)
    chats = SqliteConversationStore(conn)
    await chats.create("c1", "local", 1)
    await chats.append("c1", "local", "user", Msg(role="user", blocks=(TextBlock(text="hi"),)), 5)
    day, _ = await chats.day_conversation("local", "2026-10-26", "Your day", 2)
    await conn.execute(
        "INSERT INTO usage (owner_id, conversation_id, provider, model, input_tokens,"
        " output_tokens, cache_read_tokens, cache_write_tokens, created_at)"
        " VALUES ('local', ?, 'p', 'm', 1, 1, 0, 0, 3)",
        (day,),
    )
    await conn.execute(
        "INSERT INTO schedules (id, owner_id, kind, title, body, zone, at_local, repeat,"
        " next_due_ms, status, created_ms, created_in)"
        " VALUES ('s1', 'local', 'ask', 'Email', 'Check.', ?, '2026-10-26T09:00', NULL, 9,"
        " 'active', 0, 'c1')",
        (NY,),
    )
    # As the store before this migration wrote them.
    await conn.execute(
        "INSERT INTO notices (id, owner_id, source, source_id, kind, title, text, due_ms,"
        " late, created_ms, status) VALUES ('n1', 'local', 'schedule', 's1', 'ask', 'Email',"
        " 'Two emails.', 9, 0, 9, 'held')"
    )
    await conn.execute(
        "INSERT INTO task_runs (id, schedule_id, due_ms, status, timeout_s, attempts,"
        " result_notice, created_ms) VALUES (41, 's1', 9, 'delivered', 120, 1, 'n1', 9)"
    )
    assert await migrate(conn) == [
        "014_routines",
        "015_attention",
        "016_decision_events",
        "017_usage_spend",
    ]
    async with conn.execute("PRAGMA foreign_key_check") as cur:
        assert await cur.fetchall() == []
    store = SqliteScheduleStore(conn)
    kept = await store.notice("n1")
    assert kept is not None and (kept.text, kept.conversation_id) == ("Two emails.", None)
    (run,) = await runs(store, "s1")
    assert (run.id, run.result_notice, run.status) == (41, "n1", "delivered")
    s = await store.get("s1")
    assert s is not None and (s.target, s.notes, s.failures) == (None, "", 0)
    assert await chats.kind_of(day, "local") == ("day", "2026-10-26")
    assert [m.blocks for _, m in await chats.history("c1", "local")] == [(TextBlock(text="hi"),)]
    # Every index made again.
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    ) as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert {
        "conversations_by_recent",
        "conversations_one_day",
        "notices_held",
        "task_runs_once",
        "task_runs_queue",
    } <= names
    await conn.close()
