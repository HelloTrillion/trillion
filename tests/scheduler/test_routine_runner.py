"""A routine in the scheduler's loop (the routines spec): run where it lands, a 'ready'
notice for a board, silence for nothing, skipped over a day built by hand, and paused
after three failures in a row."""

from __future__ import annotations

from typing import Any

from tests.scheduler.conftest import ms, runs, schedule
from tests.scheduler.test_runner import Asks, make
from trillion.scheduler.runner import AskFailed, Built, NoModel
from trillion.scheduler.store import Schedule
from trillion.scheduler.when import Repeat
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

DUE = ms("2026-10-26T08:00")
NEXT = ms("2026-10-27T08:00")
MINUTE = 60_000


def routine(**kw: Any) -> Schedule:
    base: dict[str, Any] = {
        "kind": "ask",
        "title": "Monday revenue",
        "body": "Show me last week's revenue.",
        "at_local": "2026-10-26T08:00",
        "repeat": Repeat(every="day"),
        "next_due_ms": DUE,
        "target": "own",
    }
    return schedule(**{**base, **kw})


class Routines:
    """A fake `Routines`: lands where the test says, and builds what it says."""

    def __init__(self, *results: Built | BaseException, place: str | None = "c9") -> None:
        self.results = list(results)
        self.where = place
        self.built: list[tuple[str, str]] = []

    async def place(self, s: Schedule) -> str | None:
        return self.where

    async def build(self, s: Schedule, conversation_id: str) -> Built:
        self.built.append((s.id, conversation_id))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, BaseException):
            raise result
        return result


BOARD = Built(shown=True, nothing=False, said="Revenue is up.")
NOTHING = Built(shown=False, nothing=True, said="")


async def test_a_board_writes_one_ready_notice_with_the_conversation(
    store: SqliteScheduleStore,
) -> None:
    await store.create(routine())
    routines = Routines(BOARD)
    asks = Asks(AssertionError("a routine is not a plain ask"))
    sched, door, _ = make(store, DUE, asks, routines=routines)
    await sched.tick()
    (n,) = door.notices
    assert (n.kind, n.title, n.text, n.conversation_id) == (
        "ready",
        "Your Monday revenue is ready",
        None,
        "c9",
    )
    assert routines.built == [("a1b2c3d4", "c9")] and asks.calls == 0
    (run,) = await runs(store, "a1b2c3d4")
    assert run.status == "delivered" and run.result_notice == n.id
    s = await store.get("a1b2c3d4")
    # Its own conversation, kept for the next run.
    assert s is not None and s.conversation_id == "c9" and s.next_due_ms == NEXT


async def test_a_day_routine_is_ready_as_your_day(store: SqliteScheduleStore) -> None:
    await store.create(routine(target="day", title="Build my day"))
    sched, door, _ = make(store, DUE, routines=Routines(BOARD, place="d1"))
    await sched.tick()
    (n,) = door.notices
    assert (n.title, n.conversation_id) == ("Your day is ready", "d1")
    s = await store.get("a1b2c3d4")
    assert s is not None and s.conversation_id is None  # the day's is the day's


async def test_nothing_to_say_is_no_notice(store: SqliteScheduleStore) -> None:
    await store.create(routine())
    silent = Built(shown=False, nothing=False, said="  ")
    sched, door, clock = make(store, DUE, routines=Routines(NOTHING, silent))
    await sched.tick()
    clock.now = NEXT
    await sched.tick()
    assert door.notices == []
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["nothing", "nothing"]


async def test_words_without_a_board_reach_the_user_as_an_ask(
    store: SqliteScheduleStore,
) -> None:
    await store.create(routine())
    said = Built(shown=False, nothing=False, said="No revenue data this week.")
    sched, door, _ = make(store, DUE, routines=Routines(said))
    await sched.tick()
    (n,) = door.notices
    assert (n.kind, n.text, n.conversation_id) == ("ask", "No revenue data this week.", "c9")


async def test_a_day_built_by_hand_is_skipped_quietly(store: SqliteScheduleStore) -> None:
    await store.create(routine(target="day"))
    routines = Routines(BOARD, place=None)
    sched, door, _ = make(store, DUE, routines=routines)
    await sched.tick()
    assert door.notices == [] and routines.built == []
    (run,) = await runs(store, "a1b2c3d4")
    assert (run.status, run.detail) == ("skipped", "already built")


async def test_three_failures_in_a_row_pause_it_with_one_notice(
    store: SqliteScheduleStore,
) -> None:
    await store.create(routine())
    routines = Routines(AskFailed("the model failed: other"))
    sched, door, clock = make(store, DUE, routines=routines)
    for day in range(3):
        clock.now = DUE + day * 24 * 60 * MINUTE
        await sched.tick()
    assert [n.kind for n in door.notices] == ["failed", "failed", "failed"]
    assert [n.title for n in door.notices] == [
        "Monday revenue",
        "Monday revenue",
        "Monday revenue failed three times, so I've paused it",
    ]
    s = await store.get("a1b2c3d4")
    assert s is not None and s.paused_ms is not None and s.failures == 3
    waiting = s.next_due_ms
    # Paused: not run, and its due time waits where it was.
    for day in range(3, 6):
        clock.now = DUE + day * 24 * 60 * MINUTE
        await sched.tick()
    assert len(door.notices) == 3 and len(routines.built) == 3
    s = await store.get("a1b2c3d4")
    assert s is not None and s.next_due_ms == waiting
    assert len(await runs(store, "a1b2c3d4")) == 3


async def test_a_success_resets_the_count(store: SqliteScheduleStore) -> None:
    await store.create(routine())
    failed = AskFailed("the model failed: other")
    routines = Routines(failed, failed, BOARD, failed, failed)
    sched, door, clock = make(store, DUE, routines=routines)
    for day in range(5):
        clock.now = DUE + day * 24 * 60 * MINUTE
        await sched.tick()
    assert [n.kind for n in door.notices] == ["failed", "failed", "ready", "failed", "failed"]
    s = await store.get("a1b2c3d4")
    assert s is not None and s.paused_ms is None and s.failures == 2


async def test_a_timeout_counts_as_a_failure(
    store: SqliteScheduleStore,
) -> None:
    await store.create(routine(failures=2))
    sched, door, _ = make(store, DUE, routines=Routines(TimeoutError()))
    await sched.tick()
    (n,) = door.notices
    assert n.kind == "failed" and n.title.endswith("so I've paused it")


async def test_no_model_blocks_a_routine_without_counting(store: SqliteScheduleStore) -> None:
    await store.create(routine())
    sched, door, _ = make(store, DUE, routines=Routines(NoModel()))
    await sched.tick()
    assert door.notices == []
    assert [r.status for r in await runs(store, "a1b2c3d4")] == ["blocked"]
    s = await store.get("a1b2c3d4")
    assert s is not None and s.failures == 0


async def test_a_routine_queued_before_it_was_paused_is_skipped(
    store: SqliteScheduleStore,
) -> None:
    await store.create(routine())
    await store.enqueue("a1b2c3d4", DUE, NEXT, 120, DUE)
    await store.pause("local", "a1b2c3d4", DUE)
    routines = Routines(BOARD)
    sched, door, _ = make(store, DUE, routines=routines)
    await sched.tick()
    assert door.notices == [] and routines.built == []
    (run,) = await runs(store, "a1b2c3d4")
    assert (run.status, run.detail) == ("skipped", "paused")


async def test_a_routine_more_than_an_hour_late_is_missed(store: SqliteScheduleStore) -> None:
    await store.create(routine())
    routines = Routines(BOARD)
    sched, door, _ = make(store, DUE + 2 * 60 * MINUTE, routines=routines)
    await sched.tick()
    (n,) = door.notices
    assert n.kind == "missed" and routines.built == []


async def test_a_plain_timed_ask_is_untouched(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask", body="Check my email.", next_due_ms=DUE))
    routines = Routines(AssertionError("not a routine"))
    asks = Asks("Two urgent emails.")
    sched, door, _ = make(store, DUE, asks, routines=routines)
    await sched.tick()
    (n,) = door.notices
    assert (n.kind, n.text, n.conversation_id) == ("ask", "Two urgent emails.", None)
    assert routines.built == []
