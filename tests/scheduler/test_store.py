"""The queue (AGT-4): each occurrence once, claims that never collide, dead runs reclaimed."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.scheduler.conftest import NY, ms, schedule
from trillion.scheduler.when import Repeat
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.schedule_store import RECLAIM_GRACE_MS, SqliteScheduleStore

DUE = ms("2026-10-24T16:00")


async def test_a_schedule_is_kept_as_it_was_set(store: SqliteScheduleStore) -> None:
    s = schedule(repeat=Repeat(every="week", days=("mon", "thu")), kind="ask")
    await store.create(s)
    assert await store.get(s.id) == s
    assert [x.id for x in await store.active("local")] == [s.id]
    assert await store.get("nope") is None


async def test_an_occurrence_is_recorded_once(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    assert await store.enqueue("a1b2c3d4", DUE, None, 10.0, DUE)
    assert not await store.enqueue("a1b2c3d4", DUE, None, 10.0, DUE + 1)
    assert [r.due_ms for r in await store.runs("a1b2c3d4")] == [DUE]
    done = await store.get("a1b2c3d4")
    assert done is not None and done.status == "done" and done.next_due_ms is None
    # Finished, with its run still to go: still listed as coming up.
    assert [x.id for x in await store.active("local")] == ["a1b2c3d4"]


async def test_two_claimers_never_get_the_same_run(tmp_path: Path) -> None:
    conn_a = await connect(tmp_path / "t.db")
    await migrate(conn_a)
    conn_b = await connect(tmp_path / "t.db")
    a, b = SqliteScheduleStore(conn_a), SqliteScheduleStore(conn_b)
    await a.create(schedule())
    await a.enqueue("a1b2c3d4", DUE, None, 10.0, DUE)
    one, other = await asyncio.gather(a.claim("w1", DUE, 0), b.claim("w2", DUE, 0))
    assert (one is None) != (other is None)
    await a.create(schedule(id="e5f6a7b8"))
    await a.enqueue("e5f6a7b8", DUE, None, 10.0, DUE)
    await a.create(schedule(id="c9d0e1f2"))
    await a.enqueue("c9d0e1f2", DUE, None, 10.0, DUE)
    first, second = await asyncio.gather(a.claim("w1", DUE, 0), b.claim("w2", DUE, 0))
    assert first is not None and second is not None and first.id != second.id
    await conn_a.close()
    await conn_b.close()


async def test_a_claim_counts_the_attempt_and_takes_only_what_is_due(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule())
    await store.enqueue("a1b2c3d4", DUE, None, 10.0, DUE)
    assert await store.claim("w1", DUE - 1, 0) is None
    run = await store.claim("w1", DUE, 0)
    assert run is not None and run.status == "running" and run.attempts == 1
    assert await store.claim("w1", DUE, 0) is None  # nothing else is queued


async def test_a_blocked_run_waits_and_is_not_counted_again(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    await store.enqueue("a1b2c3d4", DUE, None, 120.0, DUE)
    run = await store.claim("w1", DUE, 0)
    assert run is not None
    await store.block(run.id, DUE, "no model")
    assert await store.claim("w1", DUE + 1, DUE - 1) is None  # tried just now
    again = await store.claim("w1", DUE + 15_000, DUE)
    assert again is not None and again.id == run.id and again.attempts == 1


async def test_a_run_another_process_left_running_is_interrupted(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule())
    await store.enqueue("a1b2c3d4", DUE, None, 10.0, DUE)
    await store.claim("old-process", DUE, 0)
    assert await store.reclaim("old-process", DUE + 1) == []  # its own, within its time
    (gone,) = await store.reclaim("new-process", DUE + 1)
    assert gone.status == "interrupted" and gone.attempts == 1
    await store.requeue(gone.id, "interrupted; tried again")
    again = await store.claim("new-process", DUE + 2, 0)
    assert again is not None and again.attempts == 2


async def test_a_run_past_its_timeout_is_interrupted_even_in_this_process(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule())
    await store.enqueue("a1b2c3d4", DUE, None, 10.0, DUE)
    await store.claim("w1", DUE, 0)
    assert await store.reclaim("w1", DUE + 10_000 + RECLAIM_GRACE_MS - 1) == []
    assert len(await store.reclaim("w1", DUE + 10_000 + RECLAIM_GRACE_MS)) == 1


async def test_cancelling_cancels_the_runs_still_waiting(store: SqliteScheduleStore) -> None:
    await store.create(schedule(repeat=Repeat(every="day")))
    await store.enqueue("a1b2c3d4", DUE, DUE + 86_400_000, 10.0, DUE)
    assert await store.cancel("local", "a1b2c3d4", DUE + 5)
    assert [r.status for r in await store.runs("a1b2c3d4")] == ["cancelled"]
    gone = await store.get("a1b2c3d4")
    assert gone is not None and gone.status == "cancelled" and gone.next_due_ms is None
    assert await store.active("local") == []
    assert not await store.cancel("local", "a1b2c3d4", DUE + 6)  # once
    assert not await store.cancel("someone-else", "a1b2c3d4", DUE + 6)


async def test_results_name_their_schedule_newest_first(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    await store.create(schedule(id="e5f6a7b8", title="Stretch", zone=NY))
    for sid, at in (("a1b2c3d4", DUE), ("e5f6a7b8", DUE + 1000)):
        await store.enqueue(sid, at, None, 10.0, at)
        run = await store.claim("w1", at, 0)
        assert run is not None
        await store.finish(run.id, "delivered", at + 5)
    assert [(r.title, r.run.status) for r in await store.recent("local", 20)] == [
        ("Stretch", "delivered"),
        ("Call the dentist", "delivered"),
    ]
    assert set(await store.last_results("local")) == {"a1b2c3d4", "e5f6a7b8"}


async def test_a_notice_is_held_then_delivered_once(store: SqliteScheduleStore) -> None:
    first = await store.add_notice(
        owner_id="local",
        source_id="a1b2c3d4",
        kind="reminder",
        title="Call",
        text="Call now.",
        due_ms=DUE,
        late=False,
        now_ms=DUE,
    )
    second = await store.add_notice(
        owner_id="local",
        source_id="a1b2c3d4",
        kind="missed",
        title="Call",
        text=None,
        due_ms=DUE,
        late=True,
        now_ms=DUE + 1,
    )
    assert [n.id for n in await store.held("local")] == [first.id, second.id]
    assert await store.mark_delivered(first.id, "c1", DUE + 2)
    assert not await store.mark_delivered(first.id, "c2", DUE + 3)
    kept = await store.notice(first.id)
    assert kept is not None and kept.delivered_to == "c1" and kept.status == "delivered"
    await store.mark_notified(second.id, DUE + 4)
    await store.mark_notified(second.id, DUE + 9)  # shown once: the first time stays
    shown = await store.notice(second.id)
    assert shown is not None and shown.notified_ms == DUE + 4 and shown.text is None
    assert [n.id for n in await store.held("local")] == [second.id]


async def test_a_scheduled_conversation_is_kept_but_no_page_can_open_it(tmp_path: Path) -> None:
    from trillion.storage.sqlite.conversation_store import SqliteConversationStore

    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    chats = SqliteConversationStore(conn)
    await chats.create("chat1", "local", 1)
    await chats.create("ask1", "local", 1, kind="scheduled")
    assert await chats.exists("chat1", "local")
    assert not await chats.exists("ask1", "local")
    await conn.close()
