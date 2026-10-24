"""A schedule store on a fresh database, a clock the tests move, and a schedule to put in it."""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from trillion.scheduler.store import Notice, NoticeKind, Run, Schedule
from trillion.scheduler.when import parse_local, to_ms
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore, _run

NY = "America/New_York"


def ms(local: str, zone: str = NY) -> int:
    return to_ms(parse_local(local, zone))


class FakeClock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def schedule(**kw: Any) -> Schedule:
    base = Schedule(
        id="a1b2c3d4",
        owner_id="local",
        kind="reminder",
        title="Call the dentist",
        body="Time to call the dentist.",
        zone=NY,
        at_local="2026-10-24T16:00",
        repeat=None,
        next_due_ms=ms("2026-10-24T16:00"),
        status="active",
        created_ms=0,
        created_in="c1",
    )
    return dataclasses.replace(base, **kw)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteScheduleStore]:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        yield SqliteScheduleStore(conn)
    finally:
        await conn.close()


async def runs(store: SqliteScheduleStore, schedule_id: str) -> list[Run]:
    """Every run of one schedule, oldest first. Only tests need the whole history."""
    async with store._conn.execute(
        "SELECT * FROM task_runs WHERE schedule_id = ? ORDER BY id", (schedule_id,)
    ) as cur:
        return [_run(r) for r in await cur.fetchall()]


# Each written notice gets a run of its own, at a due time no real run uses.
_RUN_DUE = itertools.count(1)


async def written(
    store: SqliteScheduleStore,
    *,
    kind: NoticeKind = "reminder",
    text: str | None = "Call now.",
    title: str = "Call",
    source_id: str = "a1b2c3d4",
    due_ms: int,
    now_ms: int,
    late: bool = False,
) -> Notice:
    """A held notice, written the way the runner writes one: a running run of its
    schedule, finished together with the notice (`finish_with_notice`)."""
    if await store.get(source_id) is None:
        await store.create(schedule(id=source_id, title=title, next_due_ms=None, status="done"))
    async with store._tx() as conn:
        cur = await conn.execute(
            "INSERT INTO task_runs (schedule_id, due_ms, status, claimed_by, claimed_ms,"
            " started_ms, timeout_s, attempts, created_ms)"
            " VALUES (?, ?, 'running', 'test', ?, ?, 10, 1, ?)",
            (source_id, next(_RUN_DUE), now_ms, now_ms, now_ms),
        )
        run_id = cur.lastrowid
    assert run_id is not None
    n = await store.finish_with_notice(
        run_id,
        "delivered",
        now_ms,
        owner_id="local",
        source_id=source_id,
        kind=kind,
        title=title,
        text=text,
        due_ms=due_ms,
        late=late,
    )
    assert n is not None
    return n
