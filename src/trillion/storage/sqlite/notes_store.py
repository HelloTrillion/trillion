"""What the memory noter has read, on SQLite (MEM-4).

Only the words of a conversation are handed out here: what the user typed or said and
her replies' text. Tool calls, tool results, taps and the context sent with each
message stay behind, so nothing a page, a file or a connected service wrote reaches
the noter from this store.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import aiosqlite

Outcome = Literal["saved", "nothing", "failed"]
# The conversations a page opens. A scheduled one holds a timed ask's run: nobody
# talked there.
_KINDS = "('chat', 'day', 'routine', 'helper')"


@dataclass(frozen=True, kw_only=True)
class Quiet:
    """A conversation gone quiet with something the user said since the last pass."""

    conversation_id: str
    # The last message the previous pass read, or 0.
    noted: int
    last_message_at: int


@dataclass(frozen=True, kw_only=True)
class Said:
    message_id: int
    who: Literal["user", "assistant"]
    text: str


def _text(blocks_json: str) -> str:
    blocks = json.loads(blocks_json)
    return "\n".join(
        str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("t") == "text"
    ).strip()


class SqliteNotesStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def quiet(
        self, owner_id: str, *, since_ms: int, quiet_before_ms: int, limit: int
    ) -> list[Quiet]:
        """Conversations whose last message is no later than `quiet_before_ms`, with
        something the user said at or after `since_ms` that no pass has read. Oldest
        first, so none waits behind a busier one for ever."""
        sql = (
            "WITH c AS ("  # noqa: S608 - fixed text only
            " SELECT id, last_message_at, COALESCE((SELECT MAX(n.upto_message_id)"
            "  FROM memory_notes n WHERE n.conversation_id = conversations.id), 0) AS noted"
            " FROM conversations"
            f" WHERE owner_id = ? AND deleted_at IS NULL AND kind IN {_KINDS}"
            " AND last_message_at IS NOT NULL AND last_message_at <= ? AND last_message_at >= ?)"
            " SELECT id, last_message_at, noted FROM c WHERE EXISTS ("
            "  SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.owner_id = ?"
            "  AND m.kind = 'user' AND m.id > c.noted AND m.created_at >= ?)"
            " ORDER BY last_message_at, id LIMIT ?"
        )
        async with self._conn.execute(
            sql, (owner_id, quiet_before_ms, since_ms, owner_id, since_ms, limit)
        ) as cur:
            rows = await cur.fetchall()
        return [
            Quiet(
                conversation_id=r["id"],
                noted=int(r["noted"]),
                last_message_at=int(r["last_message_at"]),
            )
            for r in rows
        ]

    async def said_after(
        self, conversation_id: str, owner_id: str, *, after_id: int, since_ms: int
    ) -> tuple[list[Said], int]:
        """The words said after message `after_id` and no earlier than `since_ms`, and
        the last message id looked at (to record the pass up to), or `after_id`."""
        async with self._conn.execute(
            "SELECT id, kind, blocks_json FROM messages"
            " WHERE conversation_id = ? AND owner_id = ? AND id > ? AND created_at >= ?"
            " ORDER BY id",
            (conversation_id, owner_id, after_id, since_ms),
        ) as cur:
            rows = await cur.fetchall()
        out: list[Said] = []
        upto = after_id
        for r in rows:
            upto = int(r["id"])
            if r["kind"] not in ("user", "assistant"):
                continue
            text = _text(r["blocks_json"])
            if text:
                out.append(Said(message_id=upto, who=r["kind"], text=text))
        return out, upto

    async def record(
        self,
        owner_id: str,
        conversation_id: str,
        upto_message_id: int,
        outcome: Outcome,
        saved: int,
        now_ms: int,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO memory_notes"
            " (owner_id, conversation_id, upto_message_id, outcome, saved, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, conversation_id, upto_message_id, outcome, saved, now_ms),
        )

    async def passes(self, owner_id: str, conversation_id: str) -> list[tuple[int, Outcome, int]]:
        """Every pass over one conversation, oldest first: (upto, outcome, saved)."""
        async with self._conn.execute(
            "SELECT upto_message_id, outcome, saved FROM memory_notes"
            " WHERE owner_id = ? AND conversation_id = ? ORDER BY id",
            (owner_id, conversation_id),
        ) as cur:
            rows = await cur.fetchall()
        return [(int(r[0]), r[1], int(r[2])) for r in rows]
