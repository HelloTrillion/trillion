"""Schedules, their runs and their notices on SQLite (AGT-4, SUR-9).

The store has a connection of its own and takes one change at a time inside an
explicit transaction, so claiming a run is exact even with a second process on the
same file.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from trillion.scheduler.store import (
    NOTES_MAX,
    PAUSE_AFTER,
    Kind,
    Notice,
    NoticeKind,
    Result,
    Run,
    RunStatus,
    Schedule,
)
from trillion.scheduler.when import read_repeat

# A run still marked running this long past its own timeout is not running any more.
RECLAIM_GRACE_MS = 30_000


def _schedule(r: aiosqlite.Row) -> Schedule:
    raw = r["repeat"]
    return Schedule(
        id=str(r["id"]),
        owner_id=str(r["owner_id"]),
        kind=r["kind"],
        title=str(r["title"]),
        body=str(r["body"]),
        zone=str(r["zone"]),
        at_local=str(r["at_local"]),
        repeat=read_repeat(json.loads(raw)) if raw is not None else None,
        next_due_ms=int(r["next_due_ms"]) if r["next_due_ms"] is not None else None,
        status=r["status"],
        created_ms=int(r["created_ms"]),
        created_in=str(r["created_in"]),
        target=r["target"],
        conversation_id=str(r["conversation_id"]) if r["conversation_id"] is not None else None,
        notes=str(r["notes"]),
        paused_ms=_int(r["paused_ms"]),
        failures=int(r["failures"]),
    )


def _int(value: object) -> int | None:
    return int(str(value)) if value is not None else None


def _run(r: aiosqlite.Row) -> Run:
    return Run(
        id=int(r["id"]),
        schedule_id=str(r["schedule_id"]),
        due_ms=int(r["due_ms"]),
        status=r["status"],
        claimed_by=str(r["claimed_by"]) if r["claimed_by"] is not None else None,
        claimed_ms=_int(r["claimed_ms"]),
        started_ms=_int(r["started_ms"]),
        finished_ms=_int(r["finished_ms"]),
        timeout_s=float(r["timeout_s"]),
        attempts=int(r["attempts"]),
        result_notice=str(r["result_notice"]) if r["result_notice"] is not None else None,
        detail=str(r["detail"]) if r["detail"] is not None else None,
    )


def _notice(r: aiosqlite.Row) -> Notice:
    return Notice(
        id=str(r["id"]),
        owner_id=str(r["owner_id"]),
        source_id=str(r["source_id"]),
        kind=r["kind"],
        title=str(r["title"]),
        text=str(r["text"]) if r["text"] is not None else None,
        due_ms=int(r["due_ms"]),
        late=bool(r["late"]),
        created_ms=int(r["created_ms"]),
        status=r["status"],
        delivered_ms=_int(r["delivered_ms"]),
        delivered_to=str(r["delivered_to"]) if r["delivered_to"] is not None else None,
        notified_ms=_int(r["notified_ms"]),
        conversation_id=str(r["conversation_id"]) if r["conversation_id"] is not None else None,
    )


class SqliteScheduleStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
            except BaseException:
                # As in the spine store: never leave a transaction open on the connection.
                await asyncio.shield(self._conn.rollback())
                raise
            try:
                await self._conn.execute("COMMIT")
            except BaseException:
                # A failed COMMIT leaves the transaction open: close it.
                await asyncio.shield(self._conn.rollback())
                raise

    # ── Schedules ────────────────────────────────────────────────────────

    async def create(self, s: Schedule) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO schedules (id, owner_id, kind, title, body, zone, at_local,"
                " repeat, next_due_ms, status, created_ms, created_in, target, conversation_id,"
                " notes, paused_ms, failures)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    s.id,
                    s.owner_id,
                    s.kind,
                    s.title,
                    s.body,
                    s.zone,
                    s.at_local,
                    json.dumps(s.repeat.to_json()) if s.repeat is not None else None,
                    s.next_due_ms,
                    s.status,
                    s.created_ms,
                    s.created_in,
                    s.target,
                    s.conversation_id,
                    s.notes,
                    s.paused_ms,
                    s.failures,
                ),
            )

    async def get(self, schedule_id: str) -> Schedule | None:
        async with self._conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ) as cur:
            row = await cur.fetchone()
        return _schedule(row) if row is not None else None

    async def active(self, owner_id: str) -> list[Schedule]:
        async with self._conn.execute(
            "SELECT * FROM schedules s WHERE owner_id = ? AND (status = 'active'"
            " OR (status = 'done' AND EXISTS (SELECT 1 FROM task_runs r WHERE"
            " r.schedule_id = s.id AND r.status IN ('queued', 'running', 'blocked'))))"
            " ORDER BY next_due_ms IS NULL, next_due_ms, created_ms",
            (owner_id,),
        ) as cur:
            return [_schedule(r) for r in await cur.fetchall()]

    async def due(self, now_ms: int) -> list[Schedule]:
        async with self._conn.execute(
            # A paused routine is never due: its due time waits where it was.
            "SELECT * FROM schedules WHERE status = 'active' AND next_due_ms IS NOT NULL"
            " AND next_due_ms <= ? AND paused_ms IS NULL ORDER BY next_due_ms",
            (now_ms,),
        ) as cur:
            return [_schedule(r) for r in await cur.fetchall()]

    async def enqueue(
        self, schedule_id: str, due_ms: int, next_due_ms: int | None, timeout_s: float, now_ms: int
    ) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "INSERT OR IGNORE INTO task_runs"
                " (schedule_id, due_ms, status, timeout_s, attempts, created_ms)"
                " SELECT ?, ?, 'queued', ?, 0, ? WHERE EXISTS"
                " (SELECT 1 FROM schedules WHERE id = ? AND status = 'active')",
                (schedule_id, due_ms, timeout_s, now_ms, schedule_id),
            )
            added = cur.rowcount == 1
            await conn.execute(
                "UPDATE schedules SET next_due_ms = ?,"
                " status = CASE WHEN ? IS NULL THEN 'done' ELSE status END"
                " WHERE id = ? AND status = 'active'",
                (next_due_ms, next_due_ms, schedule_id),
            )
        return added

    async def cancel(self, owner_id: str, schedule_id: str, now_ms: int) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE schedules SET status = 'cancelled', next_due_ms = NULL"
                " WHERE id = ? AND owner_id = ? AND status IN ('active', 'done')",
                (schedule_id, owner_id),
            )
            gone = cur.rowcount == 1
            if gone:
                await conn.execute(
                    "UPDATE task_runs SET status = 'cancelled', finished_ms = ?,"
                    " detail = 'cancelled' WHERE schedule_id = ?"
                    " AND status IN ('queued', 'blocked')",
                    (now_ms, schedule_id),
                )
        return gone

    async def pause(self, owner_id: str, schedule_id: str, now_ms: int) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE schedules SET paused_ms = ? WHERE id = ? AND owner_id = ?"
                " AND status = 'active' AND target IS NOT NULL AND paused_ms IS NULL",
                (now_ms, schedule_id, owner_id),
            )
            return cur.rowcount == 1

    async def resume(self, owner_id: str, schedule_id: str, next_due_ms: int | None) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE schedules SET paused_ms = NULL, failures = 0, next_due_ms = ?,"
                " status = CASE WHEN ? IS NULL THEN 'done' ELSE status END"
                " WHERE id = ? AND owner_id = ? AND status = 'active' AND paused_ms IS NOT NULL",
                (next_due_ms, next_due_ms, schedule_id, owner_id),
            )
            return cur.rowcount == 1

    async def set_conversation(self, schedule_id: str, conversation_id: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "UPDATE schedules SET conversation_id = ? WHERE id = ?",
                (conversation_id, schedule_id),
            )

    async def add_note(self, owner_id: str, schedule_id: str, line: str) -> bool:
        async with self._tx() as conn:
            # Checked in the same statement, so two notes at once never pass the cap.
            cur = await conn.execute(
                "UPDATE schedules SET notes = CASE WHEN notes = '' THEN ?1"
                " ELSE notes || char(10) || ?1 END"
                " WHERE id = ?2 AND owner_id = ?3 AND target IS NOT NULL AND status = 'active'"
                " AND length(notes) + (CASE WHEN notes = '' THEN 0 ELSE 1 END) + length(?1) <= ?4",
                (line, schedule_id, owner_id, NOTES_MAX),
            )
            return cur.rowcount == 1

    async def succeeded(self, schedule_id: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "UPDATE schedules SET failures = 0 WHERE id = ? AND failures != 0", (schedule_id,)
            )

    # ── Runs ─────────────────────────────────────────────────────────────

    async def claim(
        self, worker: str, now_ms: int, retry_before_ms: int, kind: Kind | None = None
    ) -> Run | None:
        async with self._tx() as conn:
            # A blocked run is tried again without counting as another attempt: it never ran.
            async with conn.execute(
                "UPDATE task_runs SET status = 'running', claimed_by = ?, claimed_ms = ?,"
                " started_ms = ?, attempts = attempts + CASE WHEN status = 'queued'"
                " THEN 1 ELSE 0 END"
                " WHERE id = (SELECT r.id FROM task_runs r JOIN schedules s"
                " ON s.id = r.schedule_id WHERE r.due_ms <= ? AND (? IS NULL OR s.kind = ?)"
                " AND (r.status = 'queued' OR (r.status = 'blocked' AND r.claimed_ms <= ?))"
                " ORDER BY r.due_ms, r.id LIMIT 1) RETURNING *",
                (worker, now_ms, now_ms, now_ms, kind, kind, retry_before_ms),
            ) as cur:
                row = await cur.fetchone()
        return _run(row) if row is not None else None

    async def finish(
        self,
        run_id: int,
        status: RunStatus,
        now_ms: int,
        *,
        detail: str | None = None,
    ) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE task_runs SET status = ?, finished_ms = ?,"
                " detail = COALESCE(?, detail)"
                " WHERE id = ? AND status IN ('running', 'interrupted')",
                (status, now_ms, detail, run_id),
            )
            return cur.rowcount == 1

    async def block(self, run_id: int, now_ms: int, detail: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "UPDATE task_runs SET status = 'blocked', claimed_by = NULL, claimed_ms = ?,"
                " detail = ? WHERE id = ? AND status = 'running'",
                (now_ms, detail, run_id),
            )

    async def reclaim(self, worker: str, now_ms: int) -> list[Run]:
        # Assumes one Trillion process per data folder: a run claimed by any other
        # worker id is taken to belong to a process that has gone.
        async with self._tx() as conn:
            async with conn.execute(
                "UPDATE task_runs SET status = 'interrupted', finished_ms = ?,"
                " detail = 'interrupted' WHERE status = 'running' AND (claimed_by IS NOT ?"
                " OR claimed_ms + CAST(timeout_s * 1000 AS INTEGER) + ? <= ?) RETURNING *",
                (now_ms, worker, RECLAIM_GRACE_MS, now_ms),
            ) as cur:
                rows = await cur.fetchall()
        return [_run(r) for r in rows]

    async def requeue(self, run_id: int, detail: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "UPDATE task_runs SET status = 'queued', claimed_by = NULL, claimed_ms = NULL,"
                " started_ms = NULL, finished_ms = NULL, detail = ?"
                " WHERE id = ? AND status = 'interrupted'",
                (detail, run_id),
            )

    async def recent(self, owner_id: str, limit: int) -> list[Result]:
        async with self._conn.execute(
            "SELECT r.*, s.title AS schedule_title, s.kind AS schedule_kind,"
            " s.zone AS schedule_zone"
            " FROM task_runs r JOIN schedules s ON s.id = r.schedule_id"
            " WHERE s.owner_id = ? AND r.status NOT IN ('queued', 'running')"
            " ORDER BY COALESCE(r.finished_ms, r.claimed_ms, r.due_ms) DESC, r.id DESC"
            " LIMIT ?",
            (owner_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Result(
                run=_run(r),
                title=str(r["schedule_title"]),
                kind=r["schedule_kind"],
                zone=str(r["schedule_zone"]),
            )
            for r in rows
        ]

    async def last_results(self, owner_id: str) -> dict[str, Run]:
        async with self._conn.execute(
            # Only the latest finished run of each schedule, never its whole history.
            "SELECT r.* FROM task_runs r JOIN (SELECT MAX(x.id) AS id FROM task_runs x"
            " JOIN schedules s ON s.id = x.schedule_id WHERE s.owner_id = ?"
            " AND x.status NOT IN ('queued', 'running') GROUP BY x.schedule_id) last"
            " ON last.id = r.id",
            (owner_id,),
        ) as cur:
            return {str(r["schedule_id"]): _run(r) for r in await cur.fetchall()}

    # ── Notices ──────────────────────────────────────────────────────────

    async def finish_with_notice(
        self,
        run_id: int,
        status: RunStatus,
        now_ms: int,
        *,
        detail: str | None = None,
        owner_id: str,
        source_id: str,
        kind: NoticeKind,
        title: str,
        text: str | None,
        due_ms: int,
        late: bool,
        conversation_id: str | None = None,
        pauses_as: str | None = None,
    ) -> Notice | None:
        n = Notice(
            id=uuid.uuid4().hex,
            owner_id=owner_id,
            source_id=source_id,
            kind=kind,
            title=title,
            text=text,
            due_ms=due_ms,
            late=late,
            created_ms=now_ms,
            status="held",
            delivered_ms=None,
            delivered_to=None,
            notified_ms=None,
            conversation_id=conversation_id,
        )
        async with self._tx() as conn:
            # The write lock is held from BEGIN IMMEDIATE: what this reads stays true.
            async with conn.execute(
                "SELECT 1 FROM task_runs WHERE id = ? AND status IN ('running', 'interrupted')",
                (run_id,),
            ) as cur:
                if await cur.fetchone() is None:
                    return None
            if pauses_as is not None and await self._count_failure(conn, source_id, now_ms):
                n = dataclasses.replace(n, title=pauses_as)
            await conn.execute(
                "INSERT INTO notices (id, owner_id, source, source_id, kind, title, text,"
                " due_ms, late, created_ms, status, conversation_id) VALUES (?, ?, 'schedule',"
                " ?, ?, ?, ?, ?, ?, ?, 'held', ?)",
                (
                    n.id,
                    owner_id,
                    source_id,
                    kind,
                    n.title,
                    text,
                    due_ms,
                    int(late),
                    now_ms,
                    conversation_id,
                ),
            )
            await conn.execute(
                "UPDATE task_runs SET status = ?, finished_ms = ?,"
                " detail = COALESCE(?, detail), result_notice = ? WHERE id = ?",
                (status, now_ms, detail, n.id, run_id),
            )
        return n

    @staticmethod
    async def _count_failure(conn: aiosqlite.Connection, schedule_id: str, now_ms: int) -> bool:
        """One more failed run in a row, in the transaction that writes its notice, so
        a run is counted once. True when this one pauses the routine (`PAUSE_AFTER`);
        a finished one has no runs left to pause."""
        async with conn.execute(
            "UPDATE schedules SET failures = failures + 1 WHERE id = ?"
            " RETURNING failures, paused_ms, status",
            (schedule_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or int(row["failures"]) < PAUSE_AFTER or row["paused_ms"] is not None:
            return False
        if row["status"] != "active":
            return False
        await conn.execute("UPDATE schedules SET paused_ms = ? WHERE id = ?", (now_ms, schedule_id))
        return True

    async def notice(self, notice_id: str) -> Notice | None:
        async with self._conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)) as cur:
            row = await cur.fetchone()
        return _notice(row) if row is not None else None

    async def held(self, owner_id: str) -> list[Notice]:
        async with self._conn.execute(
            "SELECT * FROM notices WHERE owner_id = ? AND status = 'held'"
            " ORDER BY created_ms, rowid",
            (owner_id,),
        ) as cur:
            return [_notice(r) for r in await cur.fetchall()]

    async def mark_delivered(self, notice_id: str, to: str, now_ms: int) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE notices SET status = 'delivered', delivered_ms = ?, delivered_to = ?"
                " WHERE id = ? AND status = 'held'",
                (now_ms, to, notice_id),
            )
            return cur.rowcount == 1

    async def unmark_delivered(self, notice_id: str, to: str) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE notices SET status = 'held', delivered_ms = NULL, delivered_to = NULL"
                " WHERE id = ? AND status = 'delivered' AND delivered_to = ?",
                (notice_id, to),
            )
            return cur.rowcount == 1

    async def mark_notified(self, notice_id: str, now_ms: int) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "UPDATE notices SET notified_ms = ? WHERE id = ? AND notified_ms IS NULL",
                (now_ms, notice_id),
            )
