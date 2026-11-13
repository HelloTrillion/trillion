"""The Seamless measures on SQLite (docs/specs/2026-11-12-seamless.md, "Measures").

Each measure with fixed clocks, including nothing recorded. Asks and quick approvals
come from the decision record (SPN-17); the first run and the tool turns from the two
tables of their own. The app's async read and `doctor --seamless`'s read-only one agree.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.spine.store import DecisionIn
from trillion.storage import inspect
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.measures_store import (
    DAY_MS,
    KEEP_MS,
    Asks,
    FirstRun,
    Measures,
    SqliteMeasuresStore,
    ToolTurns,
)
from trillion.storage.sqlite.spine_store import SqliteSpineStore

OWNER = "local"
NOW = 1_800_000_000_000
CONV = "conversation-1"


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[Path, aiosqlite.Connection]]:
    path = tmp_path / "trillion.db"
    conn = await connect(path)
    await migrate(conn)
    yield path, conn
    await conn.close()


def person(event: str, via: str = "tap") -> DecisionIn:
    return DecisionIn(event=event, decided_by=CONV, via=via)


async def card(
    spine: SqliteSpineStore,
    pid: str,
    *,
    shown_at: int | None,
    target: str | None = None,
    represented_from: str | None = None,
) -> None:
    """A made-up, generic card proposed a second before it was shown."""
    at = (shown_at or NOW) - 1_000
    a = PendingAction(
        id=pid,
        owner_id=OWNER,
        tool="file_create",
        args={"path": "/projects/note.txt"},
        args_hash="h-" + pid,
        summary="Create a note in Projects",
        target=target or f"/projects/{pid}.txt",
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        session_id=CONV,
        created_turn=1,
        status=Status.PROPOSED,
        created_at=at,
        expires_at=at + 600_000,
        represented_from=represented_from,
    )
    await spine.insert_pending(a, person("proposed", "text"), supersede=person("superseded"))
    if shown_at is not None:
        assert await spine.mark_delivered(pid, OWNER, "web", shown_at)


async def answer(
    spine: SqliteSpineStore, pid: str, at: int, d: DecisionIn, to: Status = Status.APPROVED
) -> None:
    assert await spine.transition(pid, Status.PROPOSED, to, now_ms=at, decision=d)


async def both(path: Path, store: SqliteMeasuresStore, now: int = NOW) -> Measures:
    """The app's read, checked against doctor's read-only one."""
    found = await store.read(OWNER, now)
    assert inspect.seamless(path, OWNER, now) == found
    return found


async def test_nothing_recorded_reads_as_nothing(db: tuple[Path, aiosqlite.Connection]) -> None:
    path, conn = db
    m = await both(path, SqliteMeasuresStore(conn))
    assert m.first_run is None
    assert m.asks == (Asks(days=7, shown=0, quick=0), Asks(days=28, shown=0, quick=0))
    assert m.tool_turns == ToolTurns(days=28, turns=0, in_time=0)
    assert m.to_json()["first_run"] is None


def test_no_database_at_all_is_none(tmp_path: Path) -> None:
    assert inspect.seamless(tmp_path / "missing.db", OWNER, NOW) is None


async def test_a_database_from_before_the_tables_still_reads_its_asks(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = await connect(path)
    await migrate(conn)
    spine = SqliteSpineStore(conn)
    await card(spine, "p1", shown_at=NOW - 60_000)
    await conn.execute("DROP TABLE seamless_first_run")
    await conn.execute("DROP TABLE seamless_tool_turns")
    await conn.close()
    m = inspect.seamless(path, OWNER, NOW)
    assert m is not None
    assert m.first_run is None and m.tool_turns is None
    assert m.asks[0] == Asks(days=7, shown=1, quick=0)


async def test_the_first_run_counts_questions_until_her_first_reply(
    db: tuple[Path, aiosqlite.Connection],
) -> None:
    path, conn = db
    store = SqliteMeasuresStore(conn)
    await store.opened(OWNER, at=NOW, model_ready=True)
    # Opened again later (a reload): only the first counts.
    await store.opened(OWNER, at=NOW + 5_000, model_ready=False)
    for _ in range(3):
        await store.asked(OWNER)
    m = await both(path, store)
    assert m.first_run == FirstRun(
        opened_at=NOW, model_ready=True, questions=3, first_reply_at=None
    )
    assert m.first_run.seconds is None

    await store.replied(OWNER, at=NOW + 42_000)
    # Asked after her first reply, and replied again: neither changes the first run.
    await store.asked(OWNER)
    await store.replied(OWNER, at=NOW + 90_000)
    await SqliteMeasuresStore(conn).replied(OWNER, at=NOW + 95_000)
    m = await both(path, store)
    assert m.first_run == FirstRun(
        opened_at=NOW, model_ready=True, questions=3, first_reply_at=NOW + 42_000
    )
    assert m.first_run.seconds == 42.0
    assert m.to_json()["first_run"]["seconds"] == 42.0


async def test_an_install_from_before_has_no_first_run_and_a_reply_writes_none(
    db: tuple[Path, aiosqlite.Connection],
) -> None:
    path, conn = db
    store = SqliteMeasuresStore(conn)
    await store.asked(OWNER)
    await store.replied(OWNER, at=NOW)
    assert (await both(path, store)).first_run is None


async def test_asks_are_cards_shown_in_the_last_7_and_28_days(
    db: tuple[Path, aiosqlite.Connection],
) -> None:
    path, conn = db
    spine = SqliteSpineStore(conn)
    await card(spine, "today", shown_at=NOW - 60_000)
    await card(spine, "six-days", shown_at=NOW - 6 * DAY_MS)
    await card(spine, "ten-days", shown_at=NOW - 10 * DAY_MS)
    await card(spine, "forty-days", shown_at=NOW - 40 * DAY_MS)
    # Proposed but never on a screen: not an ask the user saw.
    await card(spine, "never-shown", shown_at=None)
    m = await both(path, SqliteMeasuresStore(conn))
    assert [(a.days, a.shown) for a in m.asks] == [(7, 2), (28, 3)]


async def test_quick_approvals_are_a_persons_yes_within_30_seconds_with_nothing_changed(
    db: tuple[Path, aiosqlite.Connection],
) -> None:
    path, conn = db
    spine = SqliteSpineStore(conn)
    shown = NOW - 3_600_000

    # Counted: a tap at 12 s, a spoken yes at exactly 30 s, a typed yes at 5 s.
    await card(spine, "tap", shown_at=shown)
    await answer(spine, "tap", shown + 12_000, person("approved", "tap"))
    await card(spine, "voice", shown_at=shown)
    await answer(spine, "voice", shown + 30_000, person("approved", "voice"))
    await card(spine, "typed", shown_at=shown)
    await answer(spine, "typed", shown + 5_000, person("approved", "text"))

    # Not counted: approved at 31 s; declined; expired; superseded by a newer card for
    # the same target (which is itself still waiting); an expired card presented again,
    # and the new card that presented it, even approved at once.
    await card(spine, "slow", shown_at=shown)
    await answer(spine, "slow", shown + 31_000, person("approved"))
    await card(spine, "declined", shown_at=shown)
    await answer(spine, "declined", shown + 3_000, person("rejected"), Status.REJECTED)
    await card(spine, "expired", shown_at=shown)
    await answer(
        spine,
        "expired",
        shown + 600_000,
        DecisionIn(event="expired", decided_by="system", via="text"),
        Status.EXPIRED,
    )
    await card(spine, "old", shown_at=shown, target="/projects/same.txt")
    await card(spine, "newer", shown_at=shown + 2_000, target="/projects/same.txt")
    assert (await spine.get_pending("old")).status is Status.SUPERSEDED  # type: ignore[union-attr]
    await card(spine, "again", shown_at=shown + 700_000, represented_from="expired")
    await answer(spine, "again", shown + 702_000, person("approved"))

    m = await both(path, SqliteMeasuresStore(conn))
    assert m.asks[0] == Asks(days=7, shown=9, quick=3)
    assert m.asks[1] == Asks(days=28, shown=9, quick=3)


async def test_tool_turns_count_voiced_ones_and_are_kept_90_days(
    db: tuple[Path, aiosqlite.Connection],
) -> None:
    path, conn = db
    store = SqliteMeasuresStore(conn)
    old = NOW - KEEP_MS - 2 * DAY_MS
    older = old - DAY_MS
    await store.tool_turn(OWNER, at=older, voiced=True, spoke_in_time=True)
    await store.tool_turn(OWNER, at=old, voiced=True, spoke_in_time=True)
    await store.tool_turn(OWNER, at=NOW - 40 * DAY_MS, voiced=True, spoke_in_time=True)
    await store.tool_turn(OWNER, at=NOW - 3 * DAY_MS, voiced=True, spoke_in_time=True)
    await store.tool_turn(OWNER, at=NOW - 2 * DAY_MS, voiced=True, spoke_in_time=False)
    await store.tool_turn(OWNER, at=NOW - DAY_MS, voiced=False, spoke_in_time=False)
    await store.tool_turn(OWNER, at=NOW, voiced=True, spoke_in_time=False)
    m = await both(path, store)
    # The last 28 days, her voice on: three, one of them in time.
    assert m.tool_turns == ToolTurns(days=28, turns=3, in_time=1)
    with contextlib.closing(sqlite3.connect(path)) as raw:
        times = [r[0] for r in raw.execute("SELECT at FROM seamless_tool_turns ORDER BY at")]
    # The rows older than 90 days were written over by the newer ones, oldest first,
    # never deleted: five rows hold the five turns of the last 90 days.
    assert older not in times and old not in times and len(times) == 5
