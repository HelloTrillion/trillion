"""What the watchers keep, on SQLite (`attention/store.py`). The one writer of
`attention_finds` and `attention_state`.

It takes a connection of its own: a claim is a transaction, and must never ride inside
one another store has open.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import aiosqlite

from trillion.attention.store import RECENT_MS, FindRecord, WatcherState
from trillion.scheduler.store import NOTES_MAX


def _int(v: object) -> int | None:
    return int(v) if isinstance(v, int) else None


def _str(v: object) -> str | None:
    return str(v) if v is not None else None


def _state(r: aiosqlite.Row) -> WatcherState:
    return WatcherState(
        watcher=str(r["watcher"]),
        last_check_ms=_int(r["last_check_ms"]),
        cursor=_str(r["cursor"]),
        last_error=_str(r["last_error"]),
        notes=str(r["notes"] or ""),
    )


class SqliteAttentionStore:
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
                await asyncio.shield(self._conn.rollback())
                raise
            try:
                await self._conn.execute("COMMIT")
            except BaseException:
                await asyncio.shield(self._conn.rollback())
                raise

    # ── Finds ────────────────────────────────────────────────────────────

    async def known(self, owner_id: str, keys: Sequence[str]) -> set[str]:
        if not keys:
            return set()
        marks = ", ".join("?" for _ in keys)
        async with self._conn.execute(
            f"SELECT key FROM attention_finds WHERE owner_id = ? AND key IN ({marks})",  # noqa: S608 - placeholders only
            (owner_id, *keys),
        ) as cur:
            return {str(r["key"]) for r in await cur.fetchall()}

    async def claim(
        self, owner_id: str, watcher: str, keys: Sequence[str], now_ms: int
    ) -> list[str]:
        out: list[str] = []
        async with self._tx() as conn:
            for key in dict.fromkeys(keys):
                async with conn.execute(
                    "INSERT INTO attention_finds (owner_id, key, watcher, first_seen_ms, status)"
                    " VALUES (?, ?, ?, ?, 'silent') ON CONFLICT (owner_id, key) DO NOTHING"
                    " RETURNING key",
                    (owner_id, key, watcher, now_ms),
                ) as cur:
                    if await cur.fetchone() is not None:
                        out.append(key)
        return out

    async def told(self, owner_id: str, keys: Sequence[str], notice_id: str) -> None:
        async with self._tx() as conn:
            for key in keys:
                await conn.execute(
                    "UPDATE attention_finds SET status = 'told', notice_id = ?"
                    " WHERE owner_id = ? AND key = ? AND status = 'silent'",
                    (notice_id, owner_id, key),
                )

    async def dismiss(self, owner_id: str, key: str, now_ms: int) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE attention_finds SET status = 'dismissed', dismissed_ms = ?"
                " WHERE owner_id = ? AND key = ?",
                (now_ms, owner_id, key),
            )
            return cur.rowcount == 1

    async def recent(self, owner_id: str, now_ms: int) -> list[FindRecord]:
        async with self._conn.execute(
            "SELECT f.key, f.watcher, f.first_seen_ms, f.status, f.notice_id, f.dismissed_ms,"
            " n.title FROM attention_finds f LEFT JOIN notices n ON n.id = f.notice_id"
            " WHERE f.owner_id = ? AND f.first_seen_ms >= ?"
            " ORDER BY f.first_seen_ms DESC, f.rowid",
            (owner_id, now_ms - RECENT_MS),
        ) as cur:
            rows = await cur.fetchall()
        return [
            FindRecord(
                key=str(r["key"]),
                watcher=str(r["watcher"]),
                first_seen_ms=int(r["first_seen_ms"]),
                status=r["status"],
                notice_id=_str(r["notice_id"]),
                dismissed_ms=_int(r["dismissed_ms"]),
                title=_str(r["title"]),
            )
            for r in rows
        ]

    # ── Each watcher's place ─────────────────────────────────────────────

    async def state(self, owner_id: str, watcher: str) -> WatcherState:
        async with self._conn.execute(
            "SELECT * FROM attention_state WHERE owner_id = ? AND watcher = ?",
            (owner_id, watcher),
        ) as cur:
            r = await cur.fetchone()
        return _state(r) if r is not None else WatcherState(watcher=watcher)

    async def states(self, owner_id: str) -> list[WatcherState]:
        async with self._conn.execute(
            "SELECT * FROM attention_state WHERE owner_id = ? ORDER BY watcher", (owner_id,)
        ) as cur:
            return [_state(r) for r in await cur.fetchall()]

    async def checked(
        self, owner_id: str, watcher: str, now_ms: int, *, cursor: str | None = None
    ) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO attention_state (owner_id, watcher, last_check_ms, cursor)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (owner_id, watcher) DO UPDATE SET"
                " last_check_ms = excluded.last_check_ms,"
                " cursor = COALESCE(excluded.cursor, attention_state.cursor),"
                " last_error = NULL",
                (owner_id, watcher, now_ms, cursor),
            )

    async def failed(self, owner_id: str, watcher: str, kind: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO attention_state (owner_id, watcher, last_error) VALUES (?, ?, ?)"
                " ON CONFLICT (owner_id, watcher) DO UPDATE SET last_error = excluded.last_error",
                (owner_id, watcher, kind[:80]),
            )

    async def set_notes(self, owner_id: str, watcher: str, notes: str) -> None:
        if len(notes) > NOTES_MAX:
            raise ValueError(f"a watcher's notes are at most {NOTES_MAX} characters")
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO attention_state (owner_id, watcher, notes) VALUES (?, ?, ?)"
                " ON CONFLICT (owner_id, watcher) DO UPDATE SET notes = excluded.notes",
                (owner_id, watcher, notes),
            )

    async def clear_notes(self, owner_id: str, watcher: str) -> None:
        await self.set_notes(owner_id, watcher, "")
