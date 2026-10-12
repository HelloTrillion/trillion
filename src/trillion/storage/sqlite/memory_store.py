"""The index over memory files, on SQLite. The files, not these rows, are the truth (MEM-1)."""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from trillion.memory.types import Memory

_COLUMNS = (
    "id, type, text, source, moves, status, version, created_at, as_of, supersedes, superseded_by"
)


@dataclass(frozen=True, kw_only=True)
class Row:
    memory: Memory
    digest: str
    missing: bool


def _row(r: aiosqlite.Row) -> Row:
    return Row(
        memory=Memory(
            id=r["id"],
            type=r["type"],
            text=r["text"],
            source=r["source"],
            moves=bool(r["moves"]),
            status=r["status"],
            version=r["version"],
            created_at=r["created_at"],
            as_of=r["as_of"],
            supersedes=r["supersedes"],
            superseded_by=r["superseded_by"],
        ),
        digest=r["file_digest"],
        missing=bool(r["file_missing"]),
    )


class SqliteMemoryStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def all(self, owner_id: str) -> list[Row]:
        cur = await self._conn.execute(
            f"SELECT {_COLUMNS}, file_digest, file_missing FROM memories "  # noqa: S608
            "WHERE owner_id = ? ORDER BY created_at, id",
            (owner_id,),
        )
        return [_row(r) for r in await cur.fetchall()]

    async def put(self, owner_id: str, m: Memory, digest: str, now_ms: int) -> None:
        """Insert or replace one row from its file. Finding the file clears `file_missing`."""
        await self._conn.execute(
            f"INSERT INTO memories ({_COLUMNS}, owner_id, file_digest, file_missing, updated_at) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?) "
            "ON CONFLICT (id) DO UPDATE SET type = excluded.type, text = excluded.text, "
            "source = excluded.source, moves = excluded.moves, status = excluded.status, "
            "version = excluded.version, created_at = excluded.created_at, "
            "as_of = excluded.as_of, supersedes = excluded.supersedes, "
            "superseded_by = excluded.superseded_by, file_digest = excluded.file_digest, "
            "file_missing = 0, updated_at = excluded.updated_at "
            "WHERE memories.owner_id = excluded.owner_id",
            (
                m.id,
                m.type,
                m.text,
                m.source,
                int(m.moves),
                m.status,
                m.version,
                m.created_at,
                m.as_of,
                m.supersedes,
                m.superseded_by,
                owner_id,
                digest,
                now_ms,
            ),
        )

    async def mark_missing(self, owner_id: str, memory_id: str, now_ms: int) -> None:
        await self._conn.execute(
            "UPDATE memories SET file_missing = 1, updated_at = ? WHERE id = ? AND owner_id = ?",
            (now_ms, memory_id, owner_id),
        )
