"""The helpers you make on SQLite (the your helpers spec).

Like the schedule store, it has a connection of its own and takes one change at a time
inside an explicit transaction: a helper and its conversation are made together, and
the limit and the name are checked in the same transaction that adds one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

import aiosqlite

from trillion.helpers.store import MAX_HELPERS, NOTES_MAX, OwnHelper
from trillion.storage.sqlite.conversation_store import tidy_title
from trillion.storage.sqlite.schedule_store import cancel_helper_schedules


def _helper(r: aiosqlite.Row) -> OwnHelper:
    return OwnHelper(
        id=str(r["id"]),
        owner_id=str(r["owner_id"]),
        name=str(r["name"]),
        job=str(r["job"]),
        notes=str(r["notes"]),
        conversation_id=str(r["conversation_id"]),
        paused_ms=int(r["paused_ms"]) if r["paused_ms"] is not None else None,
        created_at=int(r["created_at"]),
    )


def name_key(name: str) -> str:
    """What makes two names the same: case set aside in any script, not only ASCII."""
    return name.casefold()


async def _taken(conn: aiosqlite.Connection, owner_id: str, name: str, but: str = "") -> bool:
    async with conn.execute(
        "SELECT 1 FROM helpers WHERE owner_id = ? AND name_key = ? AND id != ?"
        " AND deleted_at IS NULL",
        (owner_id, name_key(name), but),
    ) as cur:
        return await cur.fetchone() is not None


class SqliteHelperStore:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        *,
        changed: Callable[[], None] | None = None,
        cancelled: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._conn = conn
        # Told after each write: the helpers panel.
        self.changed = changed
        # Told the id of each routine a deleted helper took with it, as the schedule
        # store tells it: what was kept for that routine alone goes with it.
        self._cancelled = cancelled
        self._lock = asyncio.Lock()

    def _told(self, did: bool = True) -> None:
        if did and self.changed is not None:
            self.changed()

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
            except BaseException:
                # Never leave a transaction open on the connection.
                await asyncio.shield(self._conn.rollback())
                raise
            try:
                await self._conn.execute("COMMIT")
            except BaseException:
                # A failed COMMIT leaves the transaction open: close it.
                await asyncio.shield(self._conn.rollback())
                raise

    async def _get(self, owner_id: str, helper_id: str) -> OwnHelper | None:
        async with self._conn.execute(
            "SELECT * FROM helpers WHERE id = ? AND owner_id = ? AND deleted_at IS NULL",
            (helper_id, owner_id),
        ) as cur:
            row = await cur.fetchone()
        return _helper(row) if row is not None else None

    async def create(
        self, owner_id: str, name: str, job: str, now_ms: int
    ) -> OwnHelper | Literal["full", "taken"]:
        helper = OwnHelper(
            id=uuid.uuid4().hex,
            owner_id=owner_id,
            name=name,
            job=job,
            notes="",
            conversation_id=uuid.uuid4().hex,
            paused_ms=None,
            created_at=now_ms,
        )
        async with self._tx() as conn:
            async with conn.execute(
                "SELECT count(*) FROM helpers WHERE owner_id = ? AND deleted_at IS NULL",
                (owner_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is not None and int(row[0]) >= MAX_HELPERS:
                return "full"
            if await _taken(conn, owner_id, name):
                return "taken"
            # Titled as the user's, so her naming never replaces it.
            await conn.execute(
                "INSERT INTO conversations (id, owner_id, created_at, kind, title, title_source)"
                " VALUES (?, ?, ?, 'helper', ?, 'user')",
                (helper.conversation_id, owner_id, now_ms, tidy_title(name)),
            )
            await conn.execute(
                "INSERT INTO helpers (id, owner_id, name, name_key, job, notes, conversation_id,"
                " paused_ms, created_at) VALUES (?, ?, ?, ?, ?, '', ?, NULL, ?)",
                (helper.id, owner_id, name, name_key(name), job, helper.conversation_id, now_ms),
            )
        self._told()
        return helper

    async def get(self, owner_id: str, helper_id: str) -> OwnHelper | None:
        return await self._get(owner_id, helper_id)

    async def by_conversation(self, owner_id: str, conversation_id: str) -> OwnHelper | None:
        async with self._conn.execute(
            "SELECT * FROM helpers WHERE conversation_id = ? AND owner_id = ?"
            " AND deleted_at IS NULL",
            (conversation_id, owner_id),
        ) as cur:
            row = await cur.fetchone()
        return _helper(row) if row is not None else None

    async def all(self, owner_id: str) -> list[OwnHelper]:
        async with self._conn.execute(
            "SELECT * FROM helpers WHERE owner_id = ? AND deleted_at IS NULL"
            " ORDER BY created_at, rowid",
            (owner_id,),
        ) as cur:
            return [_helper(r) for r in await cur.fetchall()]

    async def update(
        self, owner_id: str, helper_id: str, *, name: str | None = None, job: str | None = None
    ) -> OwnHelper | Literal["taken"] | None:
        async with self._tx() as conn:
            if await self._get(owner_id, helper_id) is None:
                return None
            if name is not None:
                if await _taken(conn, owner_id, name, but=helper_id):
                    return "taken"
                await conn.execute(
                    "UPDATE helpers SET name = ?, name_key = ? WHERE id = ?",
                    (name, name_key(name), helper_id),
                )
                await conn.execute(
                    "UPDATE conversations SET title = ?, title_source = 'user'"
                    " WHERE id = (SELECT conversation_id FROM helpers WHERE id = ?)",
                    (tidy_title(name), helper_id),
                )
            if job is not None:
                await conn.execute("UPDATE helpers SET job = ? WHERE id = ?", (job, helper_id))
            after = await self._get(owner_id, helper_id)
        self._told(name is not None or job is not None)
        return after

    async def add_note(self, owner_id: str, helper_id: str, line: str) -> bool:
        async with self._tx() as conn:
            # Checked in the same statement, so two notes at once never pass the cap.
            cur = await conn.execute(
                "UPDATE helpers SET notes = CASE WHEN notes = '' THEN ?1"
                " ELSE notes || char(10) || ?1 END"
                " WHERE id = ?2 AND owner_id = ?3 AND deleted_at IS NULL"
                " AND length(notes) + (CASE WHEN notes = '' THEN 0 ELSE 1 END) + length(?1) <= ?4",
                (line, helper_id, owner_id, NOTES_MAX),
            )
            done = cur.rowcount == 1
        self._told(done)
        return done

    async def pause(self, owner_id: str, helper_id: str, now_ms: int) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE helpers SET paused_ms = ? WHERE id = ? AND owner_id = ?"
                " AND paused_ms IS NULL AND deleted_at IS NULL",
                (now_ms, helper_id, owner_id),
            )
            done = cur.rowcount == 1
        self._told(done)
        return done

    async def resume(self, owner_id: str, helper_id: str) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE helpers SET paused_ms = NULL WHERE id = ? AND owner_id = ?"
                " AND paused_ms IS NOT NULL AND deleted_at IS NULL",
                (helper_id, owner_id),
            )
            done = cur.rowcount == 1
        self._told(done)
        return done

    async def delete(self, owner_id: str, helper_id: str, now_ms: int) -> bool:
        async with self._tx() as conn:
            helper = await self._get(owner_id, helper_id)
            if helper is None:
                return False
            _, routines = await cancel_helper_schedules(conn, owner_id, helper_id, now_ms)
            # Its chat stays in History as a plain conversation.
            await conn.execute(
                "UPDATE conversations SET kind = 'chat' WHERE id = ?", (helper.conversation_id,)
            )
            # Soft, as every delete is (SPN-9): its schedules keep pointing at it.
            await conn.execute(
                "UPDATE helpers SET deleted_at = ? WHERE id = ?", (now_ms, helper_id)
            )
        self._told()
        if self._cancelled is not None:
            for schedule_id in routines:
                await self._cancelled(schedule_id)
        return True
