"""GAL-9: the attention engine decides when a notice deserves a supernova, and which
tier, from the scheduler's own records."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.scheduler.conftest import schedule
from trillion.attention.celebrate import (
    FRESH_MS,
    LONG_RUN_MS,
    MILESTONES,
    Celebrations,
)
from trillion.scheduler.store import Notice, NoticeKind, RunStatus
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.helper_store import SqliteHelperStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

OWNER = "local"
NOW = 1_800_000_000_000
_DUE = itertools.count(1)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteScheduleStore]:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        yield SqliteScheduleStore(conn)
    finally:
        await conn.close()


async def ran(
    store: SqliteScheduleStore,
    schedule_id: str,
    *,
    took_ms: int = 60_000,
    status: RunStatus = "delivered",
    kind: NoticeKind = "ready",
    at_ms: int = NOW,
) -> Notice | None:
    """One run of a schedule that started `took_ms` before `at_ms` and finished then.
    A run that brought something has a notice; one with nothing to say has none."""
    async with store._tx() as conn:
        cur = await conn.execute(
            "INSERT INTO task_runs (schedule_id, due_ms, status, claimed_by, claimed_ms,"
            " started_ms, timeout_s, attempts, created_ms)"
            " VALUES (?, ?, 'running', 'test', ?, ?, 3600, 1, ?)",
            (schedule_id, next(_DUE), at_ms - took_ms, at_ms - took_ms, at_ms - took_ms),
        )
        run_id = cur.lastrowid
    assert run_id is not None
    if status in ("nothing", "skipped"):
        await store.finish(run_id, status, at_ms, detail=status)
        return None
    text = None if kind in ("failed", "missed") else "Ready."
    return await store.finish_with_notice(
        run_id,
        status,
        at_ms,
        owner_id=OWNER,
        source_id=schedule_id,
        kind=kind,
        title="Done",
        text=text,
        due_ms=at_ms,
        late=False,
    )


async def make(store: SqliteScheduleStore, id: str, **kw: Any) -> None:
    await store.create(
        schedule(id=id, kind="ask", title="Job", next_due_ms=None, status="active", **kw)
    )


async def tier(store: SqliteScheduleStore, n: Notice | None, now: int = NOW) -> str | None:
    assert n is not None
    return await Celebrations(store, owner=OWNER).tier(n, now)


async def test_a_routines_milestone_runs_are_large(store: SqliteScheduleStore) -> None:
    await make(store, "r1", target="own")
    told: dict[int, str | None] = {}
    count = 0
    for i in range(1, max(MILESTONES) + 1):
        # Every third run had nothing to say: it is not counted.
        if i % 3 == 0:
            await ran(store, "r1", status="nothing")
        if i % 7 == 0:
            await ran(store, "r1", status="failed", kind="failed")
        n = await ran(store, "r1")
        count += 1
        told[count] = await tier(store, n)
    assert {c for c, t in told.items() if t is not None} == MILESTONES
    assert {t for t in told.values() if t is not None} == {"large"}


async def test_a_helpers_first_run_that_brought_something_is_medium(
    store: SqliteScheduleStore,
) -> None:
    helpers = SqliteHelperStore(store._conn)
    made = await helpers.create(OWNER, "Invoices", "Chases overdue invoices", NOW)
    assert not isinstance(made, str)
    await make(store, "h1", helper_id=made.id)
    await make(store, "h2", helper_id=made.id, target="own")
    # A run with nothing to say, and a failure, came first: neither brought anything.
    await ran(store, "h2", status="nothing")
    await ran(store, "h1", status="failed", kind="failed")
    first = await ran(store, "h1")
    assert await tier(store, first) == "medium"
    # Its next one, from another of its schedules, is not its first.
    assert await tier(store, await ran(store, "h2")) is None


async def test_a_long_handoff_or_timed_ask_is_small(store: SqliteScheduleStore) -> None:
    await make(store, "a1")
    assert await tier(store, await ran(store, "a1", took_ms=LONG_RUN_MS - 1, kind="ask")) is None
    assert await tier(store, await ran(store, "a1", took_ms=LONG_RUN_MS, kind="ask")) == "small"
    # A routine is regular work: a long one is not news.
    await make(store, "r1", target="day")
    assert await tier(store, await ran(store, "r1", took_ms=LONG_RUN_MS * 4)) is None


async def test_the_biggest_that_applies_wins(store: SqliteScheduleStore) -> None:
    helpers = SqliteHelperStore(store._conn)
    made = await helpers.create(OWNER, "Research", "Reads up on things", NOW)
    assert not isinstance(made, str)
    await make(store, "h1", helper_id=made.id)
    # The first run of a new helper, which also worked a long time.
    assert await tier(store, await ran(store, "h1", took_ms=LONG_RUN_MS * 2)) == "medium"


async def test_nothing_else_celebrates(store: SqliteScheduleStore) -> None:
    await make(store, "a1")
    long = LONG_RUN_MS * 2
    # A failure, a miss, a late one shown much later.
    assert (
        await tier(store, await ran(store, "a1", took_ms=long, kind="failed", status="failed"))
        is None
    )
    assert (
        await tier(store, await ran(store, "a1", took_ms=long, kind="missed", status="missed"))
        is None
    )
    late = await ran(store, "a1", took_ms=long, kind="ask")
    assert await tier(store, late, NOW + FRESH_MS + 1) is None
    assert await tier(store, late, NOW + FRESH_MS) == "small"
    # A ready line with no run behind it: a background read is in.
    read = await store.ready_notice(OWNER, "job-1", "Your read", NOW, "c1")
    assert await tier(store, read) is None
    # A watcher's heads-up is never a win.
    heads_up = await store.attention_notice(
        OWNER, "meeting:e1:1", title="Meeting", text="Soon.", now_ms=NOW
    )
    assert await tier(store, heads_up) is None


async def test_another_owners_schedule_never_celebrates(store: SqliteScheduleStore) -> None:
    await make(store, "a1")
    n = await ran(store, "a1", took_ms=LONG_RUN_MS * 2, kind="ask")
    assert n is not None
    assert await Celebrations(store, owner="someone-else").tier(n, NOW) is None


async def test_switched_off_it_says_nothing(store: SqliteScheduleStore) -> None:
    await make(store, "a1")
    n = await ran(store, "a1", took_ms=LONG_RUN_MS * 2, kind="ask")
    assert n is not None
    assert await Celebrations(store, owner=OWNER, on=lambda: False).tier(n, NOW) is None
