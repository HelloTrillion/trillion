"""The queue (AGT-4): each occurrence once, claims that never collide, dead runs reclaimed."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.scheduler.conftest import NY, ms, runs, schedule, written
from trillion.scheduler.store import Notice
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
    assert [r.due_ms for r in await runs(store, "a1b2c3d4")] == [DUE]
    done = await store.get("a1b2c3d4")
    assert done is not None and done.status == "done" and done.next_due_ms is None
    # Finished, with its run still to go: still listed as coming up.
    assert [x.id for x in await store.active("local")] == ["a1b2c3d4"]


async def test_two_claimers_never_get_the_same_run(tmp_path: Path) -> None:
    conn_a = await connect(tmp_path / "t.db")
    try:
        await migrate(conn_a)
        conn_b = await connect(tmp_path / "t.db")
        try:
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
        finally:
            await conn_b.close()
    finally:
        await conn_a.close()


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
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["cancelled"]
    gone = await store.get("a1b2c3d4")
    assert gone is not None and gone.status == "cancelled" and gone.next_due_ms is None
    assert await store.active("local") == []
    assert not await store.cancel("local", "a1b2c3d4", DUE + 6)  # once
    assert not await store.cancel("someone-else", "a1b2c3d4", DUE + 6)


async def test_a_run_whose_schedule_was_cancelled_meanwhile_stays_quiet(
    store: SqliteScheduleStore,
) -> None:
    """Cancelled while it ran: no ready line, no failure, nothing to tell (Settings delete)."""
    await store.create(schedule(kind="ask", target="own", repeat=Repeat(every="day")))
    await store.enqueue("a1b2c3d4", DUE, DUE + 86_400_000, 10.0, DUE)
    run = await store.claim("w1", DUE, 0)
    assert run is not None
    assert await store.cancel("local", "a1b2c3d4", DUE + 5)
    for kind in ("ready", "failed"):
        n = await store.finish_with_notice(
            run.id,
            "delivered",
            DUE + 10,
            owner_id="local",
            source_id="a1b2c3d4",
            kind=kind,
            title="Your Call the dentist is ready",
            text="Ready.",
            due_ms=DUE,
            late=False,
            conversation_id="r1",
            pauses_as="paused" if kind == "failed" else None,
        )
        assert n is None
    assert await store.held("local") == []
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["cancelled"]
    gone = await store.get("a1b2c3d4")
    assert gone is not None and gone.failures == 0


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
    first = await written(store, due_ms=DUE, now_ms=DUE)
    second = await written(store, kind="missed", text=None, due_ms=DUE, now_ms=DUE + 1, late=True)
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
    try:
        await migrate(conn)
        chats = SqliteConversationStore(conn)
        await chats.create("chat1", "local", 1)
        await chats.create("ask1", "local", 1, kind="scheduled")
        assert await chats.exists("chat1", "local")
        assert not await chats.exists("ask1", "local")
    finally:
        await conn.close()


async def test_a_cancel_between_looking_and_enqueueing_leaves_no_run(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule())
    (found,) = await store.due(DUE)
    assert await store.cancel("local", found.id, DUE)
    assert not await store.enqueue(found.id, DUE, None, 10.0, DUE)
    assert await runs(store, found.id) == []


async def test_a_run_finishes_with_its_notice_in_one_change(store: SqliteScheduleStore) -> None:
    due = ms("2026-10-24T16:00")
    await store.create(schedule())
    await store.enqueue("a1b2c3d4", due, None, 10.0, due)
    run = await store.claim("w1", due, 0)
    assert run is not None

    async def finish() -> Notice | None:
        assert run is not None
        return await store.finish_with_notice(
            run.id,
            "delivered",
            due,
            owner_id="local",
            source_id="a1b2c3d4",
            kind="reminder",
            title="Call",
            text="Call now.",
            due_ms=due,
            late=False,
        )

    n = await finish()
    assert n is not None and n.status == "held" and n.text == "Call now."
    (done,) = await runs(store, "a1b2c3d4")
    assert done.status == "delivered" and done.result_notice == n.id
    # A run already finished gets no second notice.
    assert await finish() is None and len(await store.held("local")) == 1


async def test_a_delivery_can_be_taken_back_only_by_its_page(store: SqliteScheduleStore) -> None:
    due = ms("2026-10-24T16:00")
    n = await written(store, due_ms=due, now_ms=due)
    assert await store.mark_delivered(n.id, "c1", due)
    assert not await store.unmark_delivered(n.id, "c2")
    assert await store.unmark_delivered(n.id, "c1")
    kept = await store.notice(n.id)
    assert kept is not None and kept.status == "held" and kept.delivered_to is None
    assert not await store.unmark_delivered(n.id, "c1")  # already held


async def test_the_last_result_is_the_latest_run_of_each(store: SqliteScheduleStore) -> None:
    await store.create(schedule(repeat=Repeat(every="day")))
    for at, status in ((DUE, "missed"), (DUE + 86_400_000, "delivered")):
        await store.enqueue("a1b2c3d4", at, at + 86_400_000, 10.0, at)
        run = await store.claim("w1", at, 0)
        assert run is not None
        await store.finish(run.id, status, at + 5)  # type: ignore[arg-type]
    (last,) = (await store.last_results("local")).values()
    assert last.status == "delivered" and last.due_ms == DUE + 86_400_000


async def test_a_claim_can_take_only_reminders(store: SqliteScheduleStore) -> None:
    await store.create(schedule(id="ask00001", kind="ask"))
    await store.create(schedule(id="rem00001"))
    await store.enqueue("ask00001", DUE - 1, None, 120.0, DUE)
    await store.enqueue("rem00001", DUE, None, 10.0, DUE)
    first = await store.claim("w1", DUE, 0, "reminder")
    assert first is not None and first.schedule_id == "rem00001"
    assert await store.claim("w1", DUE, 0, "reminder") is None
    ask = await store.claim("w1", DUE, 0)
    assert ask is not None and ask.schedule_id == "ask00001"


async def test_running_names_each_schedule_with_a_run_under_way(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(kind="ask"))
    await store.create(schedule(id="e5f6a7b8", kind="ask"))
    for sid in ("a1b2c3d4", "e5f6a7b8"):
        await store.enqueue(sid, DUE, None, 120.0, DUE)
    first = await store.claim("w1", DUE + 7, 0)
    assert first is not None
    assert {k: (r.id, r.started_ms) for k, r in (await store.running("local")).items()} == {
        first.schedule_id: (first.id, DUE + 7)
    }
    await store.finish(first.id, "delivered", DUE + 9)
    assert await store.running("local") == {}
