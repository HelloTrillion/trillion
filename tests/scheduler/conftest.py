"""A schedule store on a fresh database, a clock the tests move, and a schedule to put in it."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from trillion.scheduler.store import Schedule
from trillion.scheduler.when import parse_local, to_ms
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

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
    await migrate(conn)
    yield SqliteScheduleStore(conn)
    await conn.close()
