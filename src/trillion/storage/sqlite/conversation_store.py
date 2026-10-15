"""Conversations and their messages on SQLite."""

from __future__ import annotations

import json
from typing import Any, Literal

import aiosqlite

from trillion.brain.types import (
    Block,
    ContextBlock,
    Msg,
    ProviderRaw,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

Kind = Literal["user", "tap", "assistant", "tool_results"]
# Only something the user said advances the turn the model acts in (VOI-15a). A tap
# does not: it carries its own authority for the one card tapped, and must never give
# the model a later turn in which to confirm some other card.
USER_INPUTS: tuple[Kind, ...] = ("user",)


def _dump(b: Block) -> dict[str, Any]:
    if isinstance(b, TextBlock):
        return {"t": "text", "text": b.text}
    if isinstance(b, ContextBlock):
        return {"t": "context", "text": b.text}
    if isinstance(b, ToolUseBlock):
        return {"t": "tool_use", "id": b.id, "name": b.name, "args": dict(b.args)}
    return {
        "t": "tool_result",
        "tool_use_id": b.tool_use_id,
        "content": b.content,
        "is_error": b.is_error,
    }


def _load(raw: dict[str, Any]) -> Block:
    if raw["t"] == "text":
        return TextBlock(text=str(raw["text"]))
    if raw["t"] == "context":
        return ContextBlock(text=str(raw["text"]))
    if raw["t"] == "tool_use":
        return ToolUseBlock(id=str(raw["id"]), name=str(raw["name"]), args=dict(raw["args"]))
    return ToolResultBlock(
        tool_use_id=str(raw["tool_use_id"]),
        content=str(raw["content"]),
        is_error=bool(raw["is_error"]),
    )


def _dump_raw(raw: ProviderRaw | None) -> str | None:
    if raw is None:
        return None
    # Key order is kept as received: this is replayed to the provider as it came.
    return json.dumps(
        {"provider": raw.provider, "prefix_key": raw.prefix_key, "content": list(raw.content)},
        ensure_ascii=False,
    )


def _load_raw(text: str | None) -> ProviderRaw | None:
    if text is None:
        return None
    raw = json.loads(text)
    return ProviderRaw(
        provider=str(raw["provider"]),
        prefix_key=str(raw["prefix_key"]),
        content=tuple(raw["content"]),
    )


class SqliteConversationStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create(self, conversation_id: str, owner_id: str, now_ms: int) -> None:
        await self._conn.execute(
            "INSERT INTO conversations (id, owner_id, created_at) VALUES (?, ?, ?)",
            (conversation_id, owner_id, now_ms),
        )

    async def exists(self, conversation_id: str, owner_id: str) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND owner_id = ? AND deleted_at IS NULL",
            (conversation_id, owner_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def append(
        self, conversation_id: str, owner_id: str, kind: Kind, msg: Msg, now_ms: int
    ) -> int:
        """Save one message and return its id, which a later summary can point to."""
        cur = await self._conn.execute(
            "INSERT INTO messages"
            " (owner_id, conversation_id, role, kind, blocks_json, raw_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                owner_id,
                conversation_id,
                msg.role,
                kind,
                json.dumps([_dump(b) for b in msg.blocks], ensure_ascii=False),
                _dump_raw(msg.raw),
                now_ms,
            ),
        )
        return int(cur.lastrowid or 0)

    async def history(self, conversation_id: str, owner_id: str) -> list[tuple[Kind, Msg]]:
        async with self._conn.execute(
            "SELECT role, kind, blocks_json, raw_json FROM messages"
            " WHERE conversation_id = ? AND owner_id = ? ORDER BY id",
            (conversation_id, owner_id),
        ) as cur:
            rows = await cur.fetchall()
        return [
            (
                r["kind"],
                Msg(
                    role=r["role"],
                    blocks=tuple(_load(b) for b in json.loads(r["blocks_json"])),
                    raw=_load_raw(r["raw_json"]),
                ),
            )
            for r in rows
        ]

    async def context(
        self, conversation_id: str, owner_id: str
    ) -> tuple[str | None, list[tuple[int, Kind, Msg]]]:
        """What the model is sent: the latest summary, if any, and every message after it.

        `history` is still the whole conversation, for the page and the turn count."""
        async with self._conn.execute(
            "SELECT upto_message_id, summary FROM compactions"
            " WHERE conversation_id = ? AND owner_id = ? ORDER BY id DESC LIMIT 1",
            (conversation_id, owner_id),
        ) as cur:
            latest = await cur.fetchone()
        upto = int(latest["upto_message_id"]) if latest is not None else 0
        async with self._conn.execute(
            "SELECT id, role, kind, blocks_json, raw_json FROM messages"
            " WHERE conversation_id = ? AND owner_id = ? AND id > ? ORDER BY id",
            (conversation_id, owner_id, upto),
        ) as cur:
            rows = await cur.fetchall()
        return (
            str(latest["summary"]) if latest is not None else None,
            [
                (
                    int(r["id"]),
                    r["kind"],
                    Msg(
                        role=r["role"],
                        blocks=tuple(_load(b) for b in json.loads(r["blocks_json"])),
                        raw=_load_raw(r["raw_json"]),
                    ),
                )
                for r in rows
            ],
        )

    async def compact(
        self, conversation_id: str, owner_id: str, upto_message_id: int, summary: str, now_ms: int
    ) -> None:
        """Record that everything up to one message is now sent as a summary."""
        await self._conn.execute(
            "INSERT INTO compactions"
            " (owner_id, conversation_id, upto_message_id, summary, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (owner_id, conversation_id, upto_message_id, summary, now_ms),
        )

    async def record_usage(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        provider: str,
        model: str,
        usage: Usage,
        cost_usd: float | None,
        now_ms: int,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO usage (owner_id, conversation_id, provider, model, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                owner_id,
                conversation_id,
                provider,
                model,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
                cost_usd,
                now_ms,
            ),
        )


class SqlitePreferences:
    """Small settings, one JSON value per key."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get(self, owner_id: str, key: str) -> Any | None:
        async with self._conn.execute(
            "SELECT value_json FROM preferences WHERE owner_id = ? AND key = ?", (owner_id, key)
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row is not None else None

    async def put(self, owner_id: str, key: str, value: Any, now_ms: int) -> None:
        await self._conn.execute(
            "INSERT INTO preferences (owner_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (owner_id, key) DO UPDATE SET"
            " value_json = excluded.value_json, updated_at = excluded.updated_at",
            (owner_id, key, json.dumps(value), now_ms),
        )
