"""The Seamless measures on SQLite (docs/specs/2026-11-12-seamless.md, "Measures").

The one writer of `seamless_first_run` and `seamless_tool_turns`, and the one place the
measures are read, from those two tables and from the decision record (SPN-17). The
same statements serve the app (aiosqlite) and `trillion doctor --seamless` (the
standard library's read-only connection, `storage/inspect.py`).

Numbers and times only. Nothing here reads or writes what anyone said, a tool's
arguments, a name or a card's summary, and nothing leaves the machine (ONB-6).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import aiosqlite

DAY_MS = 86_400_000
# Tool-turn rows are kept this long, then written over by new ones.
KEEP_MS = 90 * DAY_MS
# An ask approved this soon after its card was shown, with nothing changed, is a sign
# it didn't need the user.
QUICK_MS = 30_000
# The windows asks are counted over, in days: a week, and four for a steadier rate.
ASK_DAYS = (7, 28)
# The window the tool-turn figure is counted over.
TOOL_DAYS = 28
# Who decided, when it was not a person answering a card.
_NOT_A_PERSON = ("system", "own_words")

# The first run, if this install recorded one.
FIRST_RUN_SQL = (
    "SELECT opened_at, model_ready, questions, first_reply_at FROM seamless_first_run"
    " WHERE owner_id = ?"
)

# Cards shown (SPN-17: `decision_deliveries.delivered_at` is written once, when a card
# is on a screen), and of those, the ones approved within 30 seconds of being shown with
# nothing changed: the first answer after the proposal was an approval by a person (a
# tap or a yes), and the card was neither a re-presentation of an older one nor
# presented again later. A superseded card's first answer is `superseded`, never this.
ASKS_SQL = f"""
SELECT COUNT(*) AS asks, COALESCE(SUM(
    p.represented_from IS NULL
    AND NOT EXISTS (SELECT 1 FROM pending_actions c WHERE c.represented_from = p.id)
    AND EXISTS (
        SELECT 1 FROM decisions d
        WHERE d.id = (
            SELECT MIN(e.id) FROM decisions e
            WHERE e.pending_id = p.id AND e.event != 'proposed'
        )
        AND d.event = 'approved'
        AND d.decided_by NOT IN ('{_NOT_A_PERSON[0]}', '{_NOT_A_PERSON[1]}')
        AND d.at >= v.delivered_at
        AND d.at - v.delivered_at <= {QUICK_MS}
    )
), 0) AS quick
FROM decision_deliveries v JOIN pending_actions p ON p.id = v.pending_id
WHERE v.owner_id = ? AND v.delivered_at >= ? AND v.delivered_at <= ?
"""  # noqa: S608 - fixed names and numbers, never a value

# Turns with a tool over two seconds while her voice was on, and how many of them had
# her first words heard before two seconds into that tool.
TOOL_TURNS_SQL = (
    "SELECT COUNT(*) AS turns, COALESCE(SUM(spoke_in_time), 0) AS in_time"
    " FROM seamless_tool_turns WHERE owner_id = ? AND voiced = 1 AND at >= ?"
)


@dataclass(frozen=True, kw_only=True)
class FirstRun:
    opened_at: int
    model_ready: bool
    # Onboarding steps that asked the user something before her first reply.
    questions: int
    first_reply_at: int | None

    @property
    def seconds(self) -> float | None:
        if self.first_reply_at is None:
            return None
        return max(0, self.first_reply_at - self.opened_at) / 1000


@dataclass(frozen=True, kw_only=True)
class Asks:
    days: int
    # Cards shown in the window.
    shown: int
    # Of those, approved within 30 seconds with nothing changed.
    quick: int


@dataclass(frozen=True, kw_only=True)
class ToolTurns:
    days: int
    # Turns with a tool over two seconds, her voice on.
    turns: int
    # Of those, her first words heard before two seconds into the tool.
    in_time: int


@dataclass(frozen=True, kw_only=True)
class Measures:
    # None: this install has no first run on record (it began before these were counted).
    first_run: FirstRun | None
    asks: tuple[Asks, ...]
    # None: the table isn't there to read (a database from before it).
    tool_turns: ToolTurns | None

    def to_json(self) -> dict[str, Any]:
        f = self.first_run
        return {
            "first_run": None
            if f is None
            else {
                "opened_at": f.opened_at,
                "model_ready": f.model_ready,
                "questions": f.questions,
                "first_reply_at": f.first_reply_at,
                "seconds": f.seconds,
            },
            "asks": [{"days": a.days, "shown": a.shown, "quick": a.quick} for a in self.asks],
            "tool_turns": None
            if self.tool_turns is None
            else {
                "days": self.tool_turns.days,
                "turns": self.tool_turns.turns,
                "in_time": self.tool_turns.in_time,
            },
        }


def _first_run(r: Any) -> FirstRun | None:
    if r is None:
        return None
    return FirstRun(
        opened_at=int(r[0]),
        model_ready=bool(r[1]),
        questions=int(r[2]),
        first_reply_at=int(r[3]) if r[3] is not None else None,
    )


def read_sync(conn: sqlite3.Connection, owner_id: str, now_ms: int) -> Measures:
    """Every measure, from a read-only connection (`trillion doctor --seamless`). A part
    whose table is missing (an older database) reads as not recorded."""
    try:
        first = _first_run(conn.execute(FIRST_RUN_SQL, (owner_id,)).fetchone())
    except sqlite3.Error:
        first = None
    asks: list[Asks] = []
    try:
        for days in ASK_DAYS:
            shown, quick = conn.execute(
                ASKS_SQL, (owner_id, now_ms - days * DAY_MS, now_ms)
            ).fetchone()
            asks.append(Asks(days=days, shown=int(shown), quick=int(quick)))
    except sqlite3.Error:
        asks = []
    try:
        turns, in_time = conn.execute(
            TOOL_TURNS_SQL, (owner_id, now_ms - TOOL_DAYS * DAY_MS)
        ).fetchone()
        tools: ToolTurns | None = ToolTurns(days=TOOL_DAYS, turns=int(turns), in_time=int(in_time))
    except sqlite3.Error:
        tools = None
    return Measures(first_run=first, asks=tuple(asks), tool_turns=tools)


class SqliteMeasuresStore:
    """On the conversations' connection, which autocommits: every write here is one
    statement, so nothing rides inside another store's transaction."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        # Whether a first run here still waits for her first reply. None until looked
        # at; then each reply after the first costs no write.
        self._waiting: bool | None = None

    async def opened(self, owner_id: str, *, at: int, model_ready: bool) -> None:
        """Onboarding began: the first open with no persona yet. Only the first counts."""
        cur = await self._conn.execute(
            "INSERT OR IGNORE INTO seamless_first_run (owner_id, opened_at, model_ready)"
            " VALUES (?, ?, ?)",
            (owner_id, at, int(model_ready)),
        )
        if cur.rowcount == 1:
            self._waiting = True

    async def asked(self, owner_id: str) -> None:
        """An onboarding step asked the user something, before her first reply."""
        await self._conn.execute(
            "UPDATE seamless_first_run SET questions = questions + 1"
            " WHERE owner_id = ? AND first_reply_at IS NULL",
            (owner_id,),
        )

    async def replied(self, owner_id: str, *, at: int) -> None:
        """A reply of hers reached the page. The first one after onboarding began is kept."""
        if self._waiting is False:
            return
        await self._conn.execute(
            "UPDATE seamless_first_run SET first_reply_at = ?"
            " WHERE owner_id = ? AND first_reply_at IS NULL",
            (at, owner_id),
        )
        self._waiting = False

    async def tool_turn(self, owner_id: str, *, at: int, voiced: bool, spoke_in_time: bool) -> None:
        """A turn with a tool over two seconds. Nothing is ever hard-deleted (SPN-9; a
        structure test refuses a hard delete anywhere), so a row older than 90 days is
        written over with the new turn instead of a new row being added: the table never
        holds more than 90 days of turns while she is used."""
        cur = await self._conn.execute(
            "UPDATE seamless_tool_turns SET at = ?, voiced = ?, spoke_in_time = ?"
            " WHERE id = (SELECT id FROM seamless_tool_turns"
            " WHERE owner_id = ? AND at < ? ORDER BY at, id LIMIT 1)",
            (at, int(voiced), int(spoke_in_time), owner_id, at - KEEP_MS),
        )
        if cur.rowcount == 1:
            return
        await self._conn.execute(
            "INSERT INTO seamless_tool_turns (owner_id, at, voiced, spoke_in_time)"
            " VALUES (?, ?, ?, ?)",
            (owner_id, at, int(voiced), int(spoke_in_time)),
        )

    async def read(self, owner_id: str, now_ms: int) -> Measures:
        async with self._conn.execute(FIRST_RUN_SQL, (owner_id,)) as cur:
            first = _first_run(await cur.fetchone())
        asks: list[Asks] = []
        for days in ASK_DAYS:
            async with self._conn.execute(
                ASKS_SQL, (owner_id, now_ms - days * DAY_MS, now_ms)
            ) as cur:
                r = await cur.fetchone()
            # An aggregate always has its one row.
            shown, quick = (int(r[0]), int(r[1])) if r is not None else (0, 0)
            asks.append(Asks(days=days, shown=shown, quick=quick))
        async with self._conn.execute(
            TOOL_TURNS_SQL, (owner_id, now_ms - TOOL_DAYS * DAY_MS)
        ) as cur:
            r = await cur.fetchone()
        turns, in_time = (int(r[0]), int(r[1])) if r is not None else (0, 0)
        tools = ToolTurns(days=TOOL_DAYS, turns=turns, in_time=in_time)
        return Measures(first_run=first, asks=tuple(asks), tool_turns=tools)
