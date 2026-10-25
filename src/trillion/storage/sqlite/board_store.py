"""Her board per conversation (SUR-1), on SQLite. The one writer of `boards`.

A board is the JSON `trillion.workspace.board` edits; this only keeps it. A deleted
conversation's board stays with it, as the conversation does (soft delete).
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite


class SqliteBoardStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get(self, conversation_id: str, owner_id: str) -> dict[str, Any] | None:
        async with self._conn.execute(
            "SELECT board_json FROM boards WHERE conversation_id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row["board_json"]) if row is not None else None

    async def put(
        self, conversation_id: str, owner_id: str, board: dict[str, Any], now_ms: int
    ) -> None:
        # The owner check keeps one owner from overwriting another's board.
        await self._conn.execute(
            "INSERT INTO boards (conversation_id, owner_id, board_json, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(conversation_id) DO UPDATE SET"
            " board_json = excluded.board_json, updated_at = excluded.updated_at"
            " WHERE boards.owner_id = excluded.owner_id",
            (conversation_id, owner_id, json.dumps(board, ensure_ascii=False), now_ms),
        )
