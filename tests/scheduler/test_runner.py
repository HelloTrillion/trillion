"""The scheduler's loop, driven tick by tick with a clock the test moves (AGT-4, AGT-8)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from tests.scheduler.conftest import FakeClock, ms, schedule
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
    return Scheduler(store, clock, ask, door, worker="w1", **kw), door, clock  # type: ignore[arg-type]


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
    (run,) = await store.runs("a1b2c3d4")
    assert run.status == "delivered" and run.result_notice == n.id
    assert sched.checked_ms == DUE and door.flushes == 1
    await sched.tick()
    assert len(door.notices) == 1  # once


async def test_nothing_happens_before_it_is_due(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    sched, door, _ = make(store, DUE - 1)
    await sched.tick()
    assert door.notices == [] and await store.runs("a1b2c3d4") == []


async def test_asleep_through_it_the_reminder_arrives_late(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    sched, door, _ = make(store, DUE + 10 * MINUTE)
    await sched.tick()
    (n,) = door.notices
    assert n.late and n.text == "Time to call the dentist."
    assert [r.status for r in await store.runs("a1b2c3d4")] == ["late"]


async def test_five_missed_days_catch_up_as_one_late_run(store: SqliteScheduleStore) -> None:
    first = ms("2026-10-19T09:00")
    await store.create(
        schedule(at_local="2026-10-19T09:00", repeat=Repeat(every="day"), next_due_ms=first)
    )
    sched, door, _ = make(store, ms("2026-10-24T09:30"))
    await sched.tick()
    assert len(door.notices) == 1 and door.notices[0].late
    (run,) = await store.runs("a1b2c3d4")
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
    assert [r.status for r in await store.runs("a1b2c3d4")] == ["late"]


async def test_a_timed_ask_two_hours_late_is_missed_not_run(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    asks = Asks("never")
    sched, door, _ = make(store, DUE + 120 * MINUTE, asks)
    await sched.tick()
    assert asks.calls == 0
    (n,) = door.notices
    assert n.kind == "missed" and n.text is None
    (run,) = await store.runs("a1b2c3d4")
    assert run.status == "missed" and run.detail == "more than an hour late"


async def test_no_model_blocks_then_runs_once_it_is_back(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    asks = Asks(NoModel(), "Done.")
    sched, door, clock = make(store, DUE, asks)
    await sched.tick()
    assert door.notices == []
    (run,) = await store.runs("a1b2c3d4")
    assert run.status == "blocked" and run.detail == "no model"
    clock.advance(15_000)
    await sched.tick()
    assert asks.calls == 2 and [n.text for n in door.notices] == ["Done."]
    (run,) = await store.runs("a1b2c3d4")
    assert run.status == "delivered" and run.attempts == 1


async def test_no_model_for_over_an_hour_becomes_missed(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    asks = Asks(NoModel())
    sched, door, clock = make(store, DUE, asks)
    await sched.tick()
    clock.advance(61 * MINUTE)
    await sched.tick()
    assert [n.kind for n in door.notices] == ["missed"]
    assert [r.status for r in await store.runs("a1b2c3d4")] == ["missed"]


async def test_a_timed_ask_that_times_out_fails_and_is_not_retried(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(kind="ask"))
    calls = 0

    async def forever(s: Schedule) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(30)
        return "late"

    sched, door, clock = make(store, DUE, forever, ask_timeout_s=0.05)
    await sched.tick()
    (n,) = door.notices
    assert n.kind == "failed" and n.text is None
    (run,) = await store.runs("a1b2c3d4")
    assert run.status == "failed" and run.detail == "timed out after 0.05 s"
    clock.advance(15_000)
    await sched.tick()
    assert calls == 1


async def test_a_failure_says_why_in_a_few_words(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    sched, door, _ = make(store, DUE, Asks(AskFailed("the model sent back nothing")))
    await sched.tick()
    (run,) = await store.runs("a1b2c3d4")
    assert run.status == "failed" and run.detail == "the model sent back nothing"
    assert door.notices[0].kind == "failed"


async def test_a_restart_mid_run_runs_it_once_more_and_records_it(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(kind="ask"))
    await store.enqueue("a1b2c3d4", DUE, None, 120.0, DUE)
    assert await store.claim("the-old-process", DUE, 0) is not None  # then it crashed
    asks = Asks("Here it is.")
    sched, door, _ = make(store, DUE + MINUTE, asks)
    await sched.tick()
    assert asks.calls == 1 and [n.text for n in door.notices] == ["Here it is."]
    (run,) = await store.runs("a1b2c3d4")
    assert run.status == "delivered" and run.attempts == 2
    assert run.detail == "interrupted; tried again"


async def test_a_run_interrupted_twice_is_missed(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask"))
    await store.enqueue("a1b2c3d4", DUE, None, 120.0, DUE)
    await store.claim("first", DUE, 0)
    await store.reclaim("second", DUE)
    (run,) = await store.runs("a1b2c3d4")
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
    assert [r.status for r in await store.runs("a1b2c3d4")] == ["cancelled"]


async def test_a_tick_that_fails_leaves_the_last_check_where_it_was(
    store: SqliteScheduleStore,
) -> None:
    sched, _, clock = make(store, DUE)
    await sched.tick()
    assert sched.checked_ms == DUE

    async def broken(now_ms: int) -> list[Schedule]:
        raise RuntimeError("disk")

    store.due = broken  # type: ignore[method-assign]
    clock.advance(15_000)
    sched.start()
    await asyncio.sleep(0.05)
    await sched.stop()
    assert sched.checked_ms == DUE  # stale, as Settings will say
