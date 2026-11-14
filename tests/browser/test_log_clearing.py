"""Her browser's log is cleared on disk, not only hidden (decision 13, CMP-9): what a
row held past 30 days goes at startup, then at most once a day as tasks run."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from trillion import paths
from trillion.api.runtime import Runtime, open_runtime
from trillion.browser.browse import CLEAR_EVERY_MS, NAME, Browse
from trillion.secrets.store import MemoryStore
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.browser_log_store import KEPT_MS, SqliteBrowserLog

OLD_ORIGIN = "https://old.example"


def now_ms() -> int:
    # The database allows clearing by its own clock, so these rows are old by the real one.
    return int(time.time() * 1000)


async def rows(path: Path) -> list[tuple[str, str | None, str | None]]:
    conn = await connect(path)
    try:
        async with conn.execute(
            "SELECT task_id, origin, detail FROM browser_log ORDER BY id"
        ) as cur:
            return [(str(r[0]), r[1], r[2]) for r in await cur.fetchall()]
    finally:
        await conn.close()


@pytest.fixture
async def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    conn = await connect(paths.db_path())
    await migrate(conn)
    log = SqliteBrowserLog(conn)
    old = now_ms() - KEPT_MS - 60_000
    await log.append("local", "a" * 16, old, "started", None, "dentists open saturday")
    await log.append("local", "a" * 16, old, "opened", OLD_ORIGIN, None)
    await log.append("local", "b" * 16, now_ms(), "opened", "https://new.example", None)
    await conn.close()
    return tmp_path


@pytest.fixture
async def rt(home: Path) -> AsyncIterator[Runtime]:
    granted = home / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        share_rules=lambda _p: None,
        granted_roots=(granted,),
        persona=home,
        secrets=MemoryStore(),
        env={},
    )
    yield runtime
    await runtime.close()


def browse_of(rt: Runtime) -> Browse:
    tool = rt.registry.get(NAME)
    assert isinstance(tool, Browse)
    return tool


async def test_startup_clears_what_the_log_held_past_30_days(rt: Runtime) -> None:
    got = await rows(paths.db_path())
    # Every row is still there, as the log is append-only: the old ones hold nothing.
    assert got == [
        ("a" * 16, None, None),
        ("a" * 16, None, None),
        ("b" * 16, "https://new.example", None),
    ]


async def test_then_at_most_once_a_day(rt: Runtime) -> None:
    tool = browse_of(rt)
    assert tool.log is not None
    old = now_ms() - KEPT_MS - 60_000
    await tool.log.append("local", "c" * 16, old, "opened", OLD_ORIGIN, None)
    # Cleared at startup: not again within the day.
    assert await tool.clear_log() == 0
    assert ("c" * 16, OLD_ORIGIN, None) in await rows(paths.db_path())
    start = rt.clock()
    rt.clock = lambda: start + CLEAR_EVERY_MS
    assert await tool.clear_log() == 1
    assert ("c" * 16, None, None) in await rows(paths.db_path())


async def test_a_clearing_that_fails_never_stops_her_browser(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = browse_of(rt)
    assert tool.log is not None

    async def broken(_now: int) -> int:
        raise RuntimeError("disk")

    monkeypatch.setattr(tool.log, "clear_old", broken)
    start = rt.clock()
    rt.clock = lambda: start + CLEAR_EVERY_MS
    assert await tool.clear_log() == 0


async def test_nothing_to_clear_before_it_is_attached() -> None:
    assert await Browse().clear_log() == 0
