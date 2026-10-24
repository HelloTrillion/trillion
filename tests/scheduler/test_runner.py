"""The scheduler's loop, driven tick by tick with a clock the test moves (AGT-4, AGT-8)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from tests.scheduler.conftest import FakeClock, ms, runs, schedule
from trillion.scheduler.runner import AskFailed, NoModel, Scheduler
from trillion.scheduler.store import Notice, Schedule
from trillion.scheduler.when import Repeat
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

DUE = ms("2026-10-24T16:00")
MINUTE = 60_000


class FakeDoor:
    def __init__(self) -> None:
        self.notices: list[Notice] = []
        self.flushes = 0

    async def added(self, notice: Notice) -> None:
        self.notices.append(notice)

    async def flush(self) -> None:
        self.flushes += 1


class Asks:
    """A fake `run_ask`: answers, or raises, as the test says, and counts its calls."""

    def __init__(self, *answers: str | BaseException) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def __call__(self, s: Schedule) -> str:
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def make(
    store: SqliteScheduleStore,
    now: int,
    run_ask: Callable[[Schedule], Awaitable[str]] | None = None,
    **kw: object,
) -> tuple[Scheduler, FakeDoor, FakeClock]:
    clock, door = FakeClock(now), FakeDoor()
    ask = run_ask or Asks(AssertionError("no ask expected"))
    kw.setdefault("worker", "w1")
    return Scheduler(store, clock, ask, door, **kw), door, clock  # type: ignore[arg-type]


async def test_a_reminder_on_time_is_delivered_in_her_words(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    sched, door, _ = make(store, DUE)
    await sched.tick()
    (n,) = door.notices
    assert (n.kind, n.text, n.late, n.title) == (
        "reminder",
        "Time to call the dentist.",
        False,
        "Call the dentist",
    )
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "delivered" and run.result_notice == n.id
    assert sched.checked_ms == DUE and door.flushes == 1
    await sched.tick()
    assert len(door.notices) == 1  # once


async def test_nothing_happens_before_it_is_due(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    sched, door, _ = make(store, DUE - 1)
    await sched.tick()
    assert door.notices == [] and await runs(store, "a1b2c3d4") == []


async def test_asleep_through_it_the_reminder_arrives_late(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    sched, door, _ = make(store, DUE + 10 * MINUTE)
    await sched.tick()
    (n,) = door.notices
    assert n.late and n.text == "Time to call the dentist."
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["late"]


async def test_five_missed_days_catch_up_as_one_late_run(store: SqliteScheduleStore) -> None:
    first = ms("2026-10-19T09:00")
    await store.create(
        schedule(at_local="2026-10-19T09:00", repeat=Repeat(every="day"), next_due_ms=first)
    )
    sched, door, _ = make(store, ms("2026-10-24T09:30"))
    await sched.tick()
    assert len(door.notices) == 1 and door.notices[0].late
    (run,) = await runs(store, "a1b2c3d4")
    assert run.due_ms == first and run.status == "late"
    s = await store.get("a1b2c3d4")
    assert s is not None and s.status == "active" and s.next_due_ms == ms("2026-10-25T09:00")


async def test_a_timed_ask_half_an_hour_late_still_runs(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask", body="Check my email."))
    asks = Asks("Two urgent emails.")
    sched, door, _ = make(store, DUE + 30 * MINUTE, asks)
    await sched.tick()
    assert asks.calls == 1
    (n,) = door.notices
    assert (n.kind, n.text, n.late) == ("ask", "Two urgent emails.", True)
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["late"]


async def test_a_timed_ask_two_hours_late_is_missed_not_run(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    asks = Asks("never")
    sched, door, _ = make(store, DUE + 120 * MINUTE, asks)
    await sched.tick()
    assert asks.calls == 0
    (n,) = door.notices
    assert n.kind == "missed" and n.text is None
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "missed" and run.detail == "more than an hour late"


async def test_no_model_blocks_then_runs_once_it_is_back(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    asks = Asks(NoModel(), "Done.")
    sched, door, clock = make(store, DUE, asks)
    await sched.tick()
    assert door.notices == []
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "blocked" and run.detail == "no model"
    clock.advance(15_000)
    await sched.tick()
    assert asks.calls == 2 and [n.text for n in door.notices] == ["Done."]
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "delivered" and run.attempts == 1


async def test_no_model_for_over_an_hour_becomes_missed(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    asks = Asks(NoModel())
    sched, door, clock = make(store, DUE, asks)
    await sched.tick()
    clock.advance(61 * MINUTE)
    await sched.tick()
    assert [n.kind for n in door.notices] == ["missed"]
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["missed"]


async def test_a_timed_ask_that_times_out_fails_and_is_not_retried(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(kind="ask"))
    calls = 0

    async def forever(s: Schedule) -> str:
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
        return "late"

    sched, door, clock = make(store, DUE, forever, ask_timeout_s=0.05)
    await sched.tick()
    (n,) = door.notices
    assert n.kind == "failed" and n.text is None
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "failed" and run.detail == "timed out after 0.05 s"
    clock.advance(15_000)
    await sched.tick()
    assert calls == 1


async def test_a_failure_says_why_in_a_few_words(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    sched, door, _ = make(store, DUE, Asks(AskFailed("the model sent back nothing")))
    await sched.tick()
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "failed" and run.detail == "the model sent back nothing"
    assert door.notices[0].kind == "failed"


async def test_a_restart_mid_run_runs_a_reminder_once_more_and_records_it(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule())
    await store.enqueue("a1b2c3d4", DUE, None, 10.0, DUE)
    assert await store.claim("the-old-process", DUE, 0) is not None  # then it crashed
    sched, door, _ = make(store, DUE + MINUTE)
    await sched.tick()
    assert [n.text for n in door.notices] == ["Time to call the dentist."]
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "delivered" and run.attempts == 2
    assert run.detail == "interrupted; tried again"


async def test_a_timed_ask_interrupted_mid_run_is_failed_not_run_again(
    store: SqliteScheduleStore,
) -> None:
    # Its tools may already have done something the user can't undo.
    await store.create(schedule(kind="ask"))
    await store.enqueue("a1b2c3d4", DUE, None, 120.0, DUE)
    assert await store.claim("the-old-process", DUE, 0) is not None  # then it crashed
    asks = Asks("never")
    sched, door, _ = make(store, DUE + MINUTE, asks)
    await sched.tick()
    assert asks.calls == 0
    (n,) = door.notices
    assert n.kind == "failed" and n.text is None
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "failed" and run.detail == "interrupted" and run.attempts == 1
    await sched.tick()
    assert asks.calls == 0 and len(door.notices) == 1


async def test_a_run_interrupted_twice_is_missed(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    await store.enqueue("a1b2c3d4", DUE, None, 10.0, DUE)
    await store.claim("first", DUE, 0)
    await store.reclaim("second", DUE)
    (run,) = await runs(store, "a1b2c3d4")
    await store.requeue(run.id, "interrupted; tried again")
    await store.claim("second", DUE, 0)  # and that process died too
    sched, door, _ = make(store, DUE + MINUTE)
    await sched.tick()
    assert [n.kind for n in door.notices] == ["missed"]


async def test_cancelling_removes_the_runs_still_waiting(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    asks = Asks(NoModel())
    sched, door, clock = make(store, DUE, asks)
    await sched.tick()
    assert await store.cancel("local", "a1b2c3d4", clock())
    clock.advance(15_000)
    await sched.tick()
    assert asks.calls == 1 and door.notices == []
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["cancelled"]


async def test_a_tick_that_fails_leaves_the_last_check_where_it_was(
    store: SqliteScheduleStore,
) -> None:
    sched, _, clock = make(store, DUE, tick_s=0)
    await sched.tick()
    assert sched.checked_ms == DUE
    calls = 0
    again = asyncio.Event()

    async def broken(now_ms: int) -> list[Schedule]:
        nonlocal calls
        calls += 1
        if calls == 2:
            again.set()
        raise RuntimeError("disk")

    store.due = broken  # type: ignore[method-assign]
    clock.advance(15_000)
    sched.start()
    try:
        await asyncio.wait_for(again.wait(), 1)  # it failed, and the loop went on
        assert sched.running
        assert sched.checked_ms == DUE  # stale, as Settings will say
    finally:
        await sched.stop()
    assert not sched.running


async def test_one_schedule_that_breaks_does_not_stop_the_others(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(id="bad00001", next_due_ms=DUE - 1))
    await store.create(schedule(id="bad00002", next_due_ms=DUE - 1))
    await store.create(schedule())
    enqueue, get = store.enqueue, store.get

    async def enqueue_but_one(schedule_id: str, *a: Any) -> bool:
        if schedule_id == "bad00001":
            raise RuntimeError("row")
        return await enqueue(schedule_id, *a)

    async def get_but_one(schedule_id: str) -> Schedule | None:
        if schedule_id == "bad00002":
            raise RuntimeError("row")
        return await get(schedule_id)

    store.enqueue = enqueue_but_one  # type: ignore[method-assign]
    store.get = get_but_one  # type: ignore[method-assign]
    sched, door, _ = make(store, DUE)
    await sched.tick()
    assert [n.source_id for n in door.notices] == ["a1b2c3d4"]
    assert [r.status for r in await runs(store, "bad00002")] == ["failed"]
    assert sched.checked_ms == DUE and door.flushes == 1


async def test_stopped_after_the_notice_is_written_it_is_never_run_twice(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(kind="ask"))

    class Stops(FakeDoor):
        async def added(self, notice: Notice) -> None:
            raise asyncio.CancelledError  # the server stopped right here

    clock, asks, stops = FakeClock(DUE), Asks("Done."), Stops()
    sched = Scheduler(store, clock, asks, stops, worker="w1")
    with pytest.raises(asyncio.CancelledError):
        await sched.tick()
    assert stops.flushes == 0  # stopping never waits on a page
    after, door, _ = make(store, DUE + MINUTE, asks)  # the next process
    await after.tick()
    assert asks.calls == 1 and door.notices == []
    (held,) = await store.held("local")
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "delivered" and run.result_notice == held.id


async def test_stopped_while_the_notice_is_written_nothing_is_kept_and_it_is_not_run_again(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(kind="ask"))
    conn = store._conn
    execute = conn.execute

    def stops_at_the_run(sql: str, *a: Any) -> Any:
        # Inside the transaction, after the notice was inserted.
        if sql.startswith("UPDATE task_runs SET status = ?, finished_ms = ?"):
            raise asyncio.CancelledError
        return execute(sql, *a)

    conn.execute = stops_at_the_run  # type: ignore[method-assign]
    asks = Asks("Done.")
    sched, door, _ = make(store, DUE, asks)
    try:
        with pytest.raises(asyncio.CancelledError):
            await sched.tick()
    finally:
        conn.execute = execute  # type: ignore[method-assign]
    assert await store.held("local") == [] and door.notices == []
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "running"
    after, door, _ = make(store, DUE + MINUTE, asks, worker="w2")
    await after.tick()
    # It ran once already; it is reported as not finished, never run a second time.
    assert asks.calls == 1 and [(n.kind, n.text) for n in door.notices] == [("failed", None)]
    assert len(await store.held("local")) == 1
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "failed" and run.detail == "interrupted" and run.attempts == 1


async def test_an_empty_reply_is_a_failure(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    sched, door, _ = make(store, DUE, Asks("  \n "))
    await sched.tick()
    (n,) = door.notices
    assert n.kind == "failed" and n.text is None
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "failed" and run.detail == "no reply"


async def test_a_timeout_inside_the_ask_is_not_the_ask_timing_out(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(kind="ask"))
    sched, _, _ = make(store, DUE, Asks(TimeoutError()))
    await sched.tick()
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "failed" and run.detail == "TimeoutError"


async def test_an_interrupted_ask_past_the_hour_is_failed(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    await store.enqueue("a1b2c3d4", DUE, None, 120.0, DUE)
    await store.claim("the-old-process", DUE, 0)
    asks = Asks("never")
    sched, door, _ = make(store, DUE + 61 * MINUTE, asks)
    await sched.tick()
    assert asks.calls == 0 and [n.kind for n in door.notices] == ["failed"]
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "failed" and run.detail == "interrupted"


async def test_an_interrupted_reminder_is_delivered_once_more(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule())
    await store.enqueue("a1b2c3d4", DUE, None, 10.0, DUE)
    await store.claim("the-old-process", DUE, 0)
    sched, door, _ = make(store, DUE + 3 * 60 * MINUTE)
    await sched.tick()
    (n,) = door.notices
    assert n.kind == "reminder" and n.late
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "late" and run.attempts == 2


async def test_a_repeat_that_can_no_longer_be_worked_out_runs_once_and_ends(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(zone="Nowhere/Gone", repeat=Repeat(every="day")))
    sched, door, clock = make(store, DUE)
    await sched.tick()
    assert len(door.notices) == 1
    s = await store.get("a1b2c3d4")
    assert s is not None and s.status == "done" and s.next_due_ms is None
    clock.advance(24 * 60 * MINUTE)
    await sched.tick()
    assert len(door.notices) == 1


async def test_start_again_after_the_loop_ended(store: SqliteScheduleStore) -> None:
    sched, _, _ = make(store, DUE, tick_s=0)
    sched.start()
    first = sched._task
    assert first is not None
    first.cancel()  # the loop ends without stop()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    assert not sched.running
    sched.start()
    try:
        assert sched.running and sched._task is not first
    finally:
        await sched.stop()


async def test_two_ticks_at_once_take_turns(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    sched, door, _ = make(store, DUE)
    await asyncio.gather(sched.tick(), sched.tick())
    assert len(door.notices) == 1 and door.flushes == 2


async def test_in_a_pass_reminders_go_out_before_timed_asks(store: SqliteScheduleStore) -> None:
    # The ask was due first, but it can take minutes: the reminder doesn't wait for it.
    await store.create(schedule(id="ask00001", kind="ask", next_due_ms=DUE - MINUTE))
    await store.create(schedule(id="rem00001"))
    sched, door, _ = make(store, DUE, Asks("Two urgent emails."))
    await sched.tick()
    assert [n.kind for n in door.notices] == ["reminder", "ask"]


async def test_a_reminder_due_while_an_ask_runs_goes_out_on_time(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(id="ask00001", kind="ask"))
    await store.create(schedule(id="rem00001", next_due_ms=DUE + MINUTE))
    clock = FakeClock(DUE)
    reminded = asyncio.Event()

    class Door(FakeDoor):
        async def added(self, notice: Notice) -> None:
            await super().added(notice)
            if notice.kind == "reminder":
                reminded.set()

    async def slow(s: Schedule) -> str:
        clock.advance(MINUTE)  # she is still working when the reminder comes due
        await reminded.wait()
        assert sched.checked_ms == DUE + MINUTE  # still checking, as Settings shows
        return "Two urgent emails."

    door = Door()
    sched = Scheduler(store, clock, slow, door, worker="w1", tick_s=0)
    await asyncio.wait_for(sched.tick(), 5)
    assert [(n.kind, n.late) for n in door.notices] == [("reminder", False), ("ask", False)]
    assert [r.status for r in await runs(store, "rem00001")] == ["delivered"]


async def test_a_store_that_fails_while_an_ask_runs_does_not_stop_the_ask(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(kind="ask"))
    clock = FakeClock(DUE)
    looked = asyncio.Event()
    due = store.due

    async def broken(now_ms: int) -> list[Schedule]:
        if now_ms > DUE:
            looked.set()
            raise RuntimeError("disk")
        return await due(now_ms)

    async def slow(s: Schedule) -> str:
        clock.advance(MINUTE)
        await looked.wait()
        return "Done."

    store.due = broken  # type: ignore[method-assign]
    door = FakeDoor()
    sched = Scheduler(store, clock, slow, door, worker="w1", tick_s=0)
    await asyncio.wait_for(sched.tick(), 5)
    assert [n.text for n in door.notices] == ["Done."]
    assert sched.checked_ms == DUE + MINUTE  # the pass itself got through
