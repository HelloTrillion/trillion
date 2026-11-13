"""Her browser's task log on SQLite (the her browser spec, decision 13).

Append-only: one writer, `append`, and the database refuses an edit. Nothing is ever
deleted (SPN-9): at 30 days `clear_old` clears a row's origin and detail, which the
database allows only then, and reads show only the last 30 days.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

KEPT_MS = 30 * 86_400_000


@dataclass(frozen=True, kw_only=True)
class LogRow:
    task_id: str
    at: int
    event: str
    origin: str | None
    detail: str | None


class SqliteBrowserLog:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def append(
        self,
        owner_id: str,
        task_id: str,
        at: int,
        event: str,
        origin: str | None,
        detail: str | None,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO browser_log (owner_id, task_id, at, event, origin, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, task_id, at, event, origin, detail),
        )
        await self._conn.commit()

    async def for_task(self, owner_id: str, task_id: str, now_ms: int) -> list[LogRow]:
        async with self._conn.execute(
            "SELECT task_id, at, event, origin, detail FROM browser_log"
            " WHERE owner_id = ? AND task_id = ? AND at >= ? ORDER BY id",
            (owner_id, task_id, now_ms - KEPT_MS),
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def tasks(self, owner_id: str, now_ms: int, limit: int = 50) -> list[LogRow]:
        """Each task of the last 30 days by its first row (when it started), newest first."""
        async with self._conn.execute(
            "SELECT task_id, at, event, origin, detail FROM browser_log"
            " WHERE owner_id = ? AND event = 'started' AND at >= ?"
            " ORDER BY id DESC LIMIT ?",
            (owner_id, now_ms - KEPT_MS, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def clear_old(self, now_ms: int) -> int:
        """What rows more than 30 days old said is cleared. How many were."""
        cur = await self._conn.execute(
            "UPDATE browser_log SET origin = NULL, detail = NULL"
            " WHERE at < ? AND (origin IS NOT NULL OR detail IS NOT NULL)",
            (now_ms - KEPT_MS,),
        )
        await self._conn.commit()
        return cur.rowcount or 0


def _row(r: aiosqlite.Row | tuple[object, ...]) -> LogRow:
    task_id, at, event, origin, detail = tuple(r)
    return LogRow(
        task_id=str(task_id),
        at=int(str(at)),
        event=str(event),
        origin=None if origin is None else str(origin),
        detail=None if detail is None else str(detail),
    )
