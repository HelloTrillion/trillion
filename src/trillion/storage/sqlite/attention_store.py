"""What the watchers keep, on SQLite (`attention/store.py`). The one writer of
`attention_finds` and `attention_state`.

It takes a connection of its own: a claim is a transaction, and must never ride inside
one another store has open.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

import aiosqlite

from trillion.attention.store import KEEP, RECENT_MS, Cursor, FindRecord, WatcherState
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
        # Each watcher's resets in this process. In memory: a restart leaves no check
        # on its way.
        self._generations: dict[tuple[str, str], int] = {}

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
        # A deferred find (`release`) is not known: it is claimed again.
        sql = f"SELECT key FROM attention_finds WHERE owner_id = ? AND key IN ({marks})"  # noqa: S608 - placeholders only
        async with self._conn.execute(
            sql + " AND status != 'deferred'",
            (owner_id, *keys),
        ) as cur:
            return {str(r["key"]) for r in await cur.fetchall()}

    async def claim(
        self,
        owner_id: str,
        watcher: str,
        keys: Sequence[str],
        now_ms: int,
        *,
        details: Mapping[str, str | None] | None = None,
    ) -> list[str]:
        out: list[str] = []
        about = details or {}
        async with self._tx() as conn:
            for key in dict.fromkeys(keys):
                detail = about.get(key)
                async with conn.execute(
                    "INSERT INTO attention_finds"
                    " (owner_id, key, watcher, first_seen_ms, status, detail)"
                    " VALUES (?, ?, ?, ?, 'claimed', ?) ON CONFLICT (owner_id, key) DO UPDATE"
                    # A deferred find (`release`) is claimed again, as it was first seen.
                    " SET status = 'claimed', notice_id = NULL"
                    " WHERE attention_finds.status = 'deferred'"
                    " RETURNING key",
                    (owner_id, key, watcher, now_ms, detail[:300] if detail else None),
                ) as cur:
                    if await cur.fetchone() is not None:
                        out.append(key)
        return out

    async def _claimed(self, owner_id: str, keys: Sequence[str], sets: str, *args: str) -> None:
        async with self._tx() as conn:
            for key in keys:
                await conn.execute(
                    f"UPDATE attention_finds SET {sets}"  # noqa: S608 - fixed text only
                    " WHERE owner_id = ? AND key = ? AND status = 'claimed'",
                    (*args, owner_id, key),
                )

    async def telling(self, owner_id: str, keys: Sequence[str], notice_id: str) -> None:
        await self._claimed(owner_id, keys, "notice_id = ?", notice_id)

    async def told(self, owner_id: str, keys: Sequence[str], notice_id: str) -> None:
        await self._claimed(owner_id, keys, "status = 'told', notice_id = ?", notice_id)

    async def silent(self, owner_id: str, keys: Sequence[str]) -> None:
        await self._claimed(owner_id, keys, "status = 'silent', notice_id = NULL")

    async def release(self, owner_id: str, keys: Sequence[str]) -> None:
        async with self._tx() as conn:
            for key in keys:
                await conn.execute(
                    "UPDATE attention_finds SET status = 'deferred'"
                    " WHERE owner_id = ? AND key = ? AND status = 'claimed' AND notice_id IS NULL",
                    (owner_id, key),
                )

    async def repair(self, owner_id: str) -> int:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE attention_finds SET status = 'told', notice_id = COALESCE("
                " (SELECT n.id FROM notices n WHERE n.id = attention_finds.notice_id),"
                " (SELECT n.id FROM notices n WHERE n.owner_id = attention_finds.owner_id"
                "  AND n.source = 'attention' AND n.source_id = attention_finds.key"
                "  ORDER BY n.created_ms LIMIT 1))"
                " WHERE owner_id = ? AND status = 'claimed' AND ("
                " EXISTS (SELECT 1 FROM notices n WHERE n.id = attention_finds.notice_id)"
                " OR EXISTS (SELECT 1 FROM notices n WHERE n.owner_id = attention_finds.owner_id"
                "  AND n.source = 'attention' AND n.source_id = attention_finds.key))",
                (owner_id,),
            )
            return cur.rowcount

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
            " f.detail, n.title FROM attention_finds f LEFT JOIN notices n ON n.id = f.notice_id"
            " WHERE f.owner_id = ? AND f.first_seen_ms >= ? AND f.status != 'deferred'"
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
                detail=_str(r["detail"]),
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

    def generation(self, owner_id: str, watcher: str) -> int:
        return self._generations.get((owner_id, watcher), 0)

    async def checked(
        self,
        owner_id: str,
        watcher: str,
        now_ms: int,
        *,
        cursor: Cursor = KEEP,
        generation: int | None = None,
    ) -> bool:
        keep = cursor is KEEP
        async with self._tx() as conn:
            # Under the lock a reset takes too: compared and written as one.
            if generation is not None and generation != self.generation(owner_id, watcher):
                return False
            await conn.execute(
                "INSERT INTO attention_state (owner_id, watcher, last_check_ms, cursor)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (owner_id, watcher) DO UPDATE SET"
                " last_check_ms = excluded.last_check_ms,"
                " cursor = CASE WHEN ? THEN attention_state.cursor ELSE excluded.cursor END,"
                " last_error = NULL",
                (owner_id, watcher, now_ms, None if keep else cursor, keep),
            )
        return True

    async def reset(self, owner_id: str, watcher: str) -> None:
        async with self._tx() as conn:
            key = (owner_id, watcher)
            self._generations[key] = self._generations.get(key, 0) + 1
            await conn.execute(
                "UPDATE attention_state SET last_check_ms = NULL, cursor = NULL"
                " WHERE owner_id = ? AND watcher = ?",
                (owner_id, watcher),
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

    async def add_note(self, owner_id: str, watcher: str, line: str) -> bool:
        # Every line break Python reads, not only "\n": the notes are read back by lines.
        if len(line.splitlines()) != 1 or not line.strip():
            raise ValueError("a note is one line")
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO attention_state (owner_id, watcher) VALUES (?, ?)"
                " ON CONFLICT (owner_id, watcher) DO NOTHING",
                (owner_id, watcher),
            )
            # Checked in the same statement, so two notes at once never pass the cap.
            cur = await conn.execute(
                "UPDATE attention_state SET notes = CASE WHEN notes = '' THEN ?1"
                " ELSE notes || char(10) || ?1 END"
                " WHERE owner_id = ?2 AND watcher = ?3"
                " AND length(notes) + (CASE WHEN notes = '' THEN 0 ELSE 1 END) + length(?1) <= ?4",
                (line, owner_id, watcher, NOTES_MAX),
            )
            return cur.rowcount == 1

    async def clear_notes(self, owner_id: str, watcher: str) -> None:
        await self.set_notes(owner_id, watcher, "")
