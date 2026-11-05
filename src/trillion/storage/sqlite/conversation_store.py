"""Conversations and their messages on SQLite."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from trillion.spend.types import Caller, Unit

Kind = Literal["user", "tap", "assistant", "tool_results"]
# Only something the user said advances the turn the model acts in (VOI-15a). A tap
# does not: it carries its own authority for the one card tapped, and must never give
# the model a later turn in which to confirm some other card.
USER_INPUTS: tuple[Kind, ...] = ("user",)
# A chat is one a page opens. A scheduled one is where a timed ask ran: kept, never opened.
# A day is a page's too: the one conversation per date where she builds the user's day.
# A routine is a page's as well: one routine's own, where each of its runs puts its board.
ConversationKind = Literal["chat", "scheduled", "day", "routine"]
# The kinds a page opens, lists, renames and deletes.
_PAGE_KINDS = "kind IN ('chat', 'day', 'routine')"
TitleSource = Literal["words", "model", "user"]
TITLE_MAX = 80
WORDS_TITLE_MAX = 60


@dataclass(frozen=True)
class ChatRow:
    """One line of the thread list."""

    id: str
    title: str | None
    last_message_at: int
    kind: ConversationKind = "chat"
    # A day conversation's local date, YYYY-MM-DD; None for any other.
    day: str | None = None


def tidy_title(text: str, limit: int = TITLE_MAX) -> str:
    """One line, single spaces, cut at `limit`."""
    return " ".join(text.split())[:limit].strip()


def _first_text(msg: Msg) -> str:
    return " ".join(b.text for b in msg.blocks if isinstance(b, TextBlock)).strip()


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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


# A usage row's quantity in its unit is the sum of its four token columns: tokens, or
# a voice row's characters or a hearing row's seconds, kept in input_tokens with the
# other three at 0.
# No end: later than any row's created_at.
_FOREVER = 2**62


class SqliteConversationStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create(
        self, conversation_id: str, owner_id: str, now_ms: int, kind: ConversationKind = "chat"
    ) -> None:
        await self._conn.execute(
            "INSERT INTO conversations (id, owner_id, created_at, kind) VALUES (?, ?, ?, ?)",
            (conversation_id, owner_id, now_ms, kind),
        )

    async def exists(self, conversation_id: str, owner_id: str) -> bool:
        """Whether a page may open it: a chat of this owner's that was not deleted."""
        async with self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND owner_id = ? AND deleted_at IS NULL"  # noqa: S608 - fixed text only
            f" AND {_PAGE_KINDS}",
            (conversation_id, owner_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def kind_of(
        self, conversation_id: str, owner_id: str
    ) -> tuple[ConversationKind, str | None] | None:
        """A conversation's kind, and its date when it is a day; None if there is none."""
        async with self._conn.execute(
            "SELECT kind, day FROM conversations WHERE id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        ) as cur:
            row = await cur.fetchone()
        return (row["kind"], row["day"]) if row is not None else None

    async def day_conversation(
        self, owner_id: str, day: str, title: str, now_ms: int
    ) -> tuple[str, bool]:
        """The day conversation for a date, made now if there is none, and whether it
        was. Its title is the host's, saved as the user's so her naming never replaces
        it. One that was deleted stays deleted: a fresh one takes its place."""
        cur = await self._conn.execute(
            "INSERT INTO conversations"
            " (id, owner_id, created_at, kind, day, title, title_source)"
            " VALUES (?, ?, ?, 'day', ?, ?, 'user') ON CONFLICT DO NOTHING",
            (uuid.uuid4().hex, owner_id, now_ms, day, tidy_title(title)),
        )
        created = cur.rowcount == 1
        async with self._conn.execute(
            "SELECT id FROM conversations"
            " WHERE owner_id = ? AND kind = 'day' AND day = ? AND deleted_at IS NULL",
            (owner_id, day),
        ) as c:
            row = await c.fetchone()
        if row is None:  # pragma: no cover - the insert or the row before it is there
            raise RuntimeError("the day conversation was neither found nor made")
        return str(row["id"]), created

    async def find_day(self, owner_id: str, day: str) -> str | None:
        """The day conversation for a date, or None: unlike `day_conversation`, it
        never makes one, so only looking leaves no empty day behind."""
        async with self._conn.execute(
            "SELECT id FROM conversations"
            " WHERE owner_id = ? AND kind = 'day' AND day = ? AND deleted_at IS NULL",
            (owner_id, day),
        ) as c:
            row = await c.fetchone()
        return str(row["id"]) if row is not None else None

    async def routine_conversation(self, owner_id: str, title: str, now_ms: int) -> str:
        """A new conversation of a routine's own, titled with its name. The title is
        saved as the user's, so her naming never replaces it."""
        conversation_id = uuid.uuid4().hex
        await self._conn.execute(
            "INSERT INTO conversations (id, owner_id, created_at, kind, title, title_source)"
            " VALUES (?, ?, ?, 'routine', ?, 'user')",
            (conversation_id, owner_id, now_ms, tidy_title(title)),
        )
        return conversation_id

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
        await self._touched(conversation_id, owner_id, kind, [msg], now_ms)
        return int(cur.lastrowid or 0)

    async def append_many(
        self,
        conversation_id: str,
        owner_id: str,
        kind: Kind | Sequence[Kind],
        msgs: Sequence[Msg],
        now_ms: int,
    ) -> list[int]:
        """Save several messages in one statement, so all are kept or none, with no
        transaction held open on the shared connection. Returns their ids in order.
        `kind` is one for all, or each message's own."""
        if not msgs:
            return []
        kinds: list[Kind] = [kind] * len(msgs) if isinstance(kind, str) else list(kind)
        if len(kinds) != len(msgs):
            raise ValueError("a kind for each message")
        values: list[object] = []
        for kind, msg in zip(kinds, msgs, strict=True):
            values += [
                owner_id,
                conversation_id,
                msg.role,
                kind,
                json.dumps([_dump(b) for b in msg.blocks], ensure_ascii=False),
                _dump_raw(msg.raw),
                now_ms,
            ]
        rows = ", ".join(["(?, ?, ?, ?, ?, ?, ?)"] * len(msgs))
        cols = "owner_id, conversation_id, role, kind, blocks_json, raw_json, created_at"
        sql = f"INSERT INTO messages ({cols}) VALUES {rows} RETURNING id"  # noqa: S608 - placeholders only
        async with self._conn.execute(sql, values) as cur:
            # RETURNING's order is not promised; ids rise in the order the rows went in.
            ids = sorted(int(r[0]) for r in await cur.fetchall())
        # Only the user's own words can title a conversation.
        said = [m for k, m in zip(kinds, msgs, strict=True) if k == "user"]
        await self._touched(
            conversation_id, owner_id, "user" if said else kinds[0], said or msgs, now_ms
        )
        return ids

    async def append_run(
        self, conversation_id: str, owner_id: str, rows: Sequence[tuple[Kind, Msg, int]]
    ) -> None:
        """Save a whole run's messages, each with its kind and time, in one statement:
        kept all or none, so a tool call is never kept without its result."""
        if not rows:
            return
        values: list[object] = []
        for kind, msg, at in rows:
            values += [
                owner_id,
                conversation_id,
                msg.role,
                kind,
                json.dumps([_dump(b) for b in msg.blocks], ensure_ascii=False),
                _dump_raw(msg.raw),
                at,
            ]
        marks = ", ".join(["(?, ?, ?, ?, ?, ?, ?)"] * len(rows))
        cols = "owner_id, conversation_id, role, kind, blocks_json, raw_json, created_at"
        await self._conn.execute(f"INSERT INTO messages ({cols}) VALUES {marks}", values)  # noqa: S608 - placeholders only
        # Recency only: none of these is the user's, so none names the conversation.
        await self._touched(conversation_id, owner_id, "assistant", [], max(r[2] for r in rows))

    async def _touched(
        self, conversation_id: str, owner_id: str, kind: Kind, msgs: Sequence[Msg], now_ms: int
    ) -> None:
        """Recency, and the first words as a title until there is one. The connection
        autocommits, so these commit apart from the insert; a missed one heals on the
        next message."""
        await self._conn.execute(
            "UPDATE conversations SET last_message_at = ? WHERE id = ? AND owner_id = ?",
            (now_ms, conversation_id, owner_id),
        )
        if kind != "user":
            return
        words = next((t for m in msgs if (t := _first_text(m))), "")
        if not words:
            return
        await self._conn.execute(
            "UPDATE conversations SET title = ?, title_source = 'words'"
            " WHERE id = ? AND owner_id = ? AND title IS NULL",
            (tidy_title(words, WORDS_TITLE_MAX), conversation_id, owner_id),
        )

    async def title_of(
        self, conversation_id: str, owner_id: str
    ) -> tuple[str | None, TitleSource | None]:
        """A conversation's title and who gave it; both None until it has one."""
        async with self._conn.execute(
            "SELECT title, title_source FROM conversations WHERE id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        ) as cur:
            row = await cur.fetchone()
        return (row["title"], row["title_source"]) if row is not None else (None, None)

    async def set_title(
        self, conversation_id: str, owner_id: str, title: str, source: TitleSource
    ) -> bool:
        """Name a conversation; returns whether it did. Her title never replaces one the
        user gave, checked in the statement itself so a rename racing her cannot lose."""
        clean = tidy_title(title)
        if not clean:
            return False
        guard = " AND (title_source IS NULL OR title_source != 'user')" if source == "model" else ""
        cur = await self._conn.execute(
            "UPDATE conversations SET title = ?, title_source = ?"  # noqa: S608 - fixed text only
            f" WHERE id = ? AND owner_id = ? AND {_PAGE_KINDS} AND deleted_at IS NULL" + guard,
            (clean, source, conversation_id, owner_id),
        )
        return cur.rowcount == 1

    async def list_chats(
        self,
        owner_id: str,
        *,
        query: str | None = None,
        before: tuple[int, str] | None = None,
        limit: int = 50,
    ) -> tuple[list[ChatRow], bool]:
        """The user's conversations with something in them, newest first, and whether
        there are more. A query matches the title or the words of what was said, never
        tool results and never the JSON around the words. `before` is the last row shown,
        as (last_message_at, id). LIKE folds ASCII case only."""
        where = [
            "c.owner_id = ?",
            f"c.{_PAGE_KINDS}",
            "c.deleted_at IS NULL",
            "c.last_message_at IS NOT NULL",
        ]
        args: list[object] = [owner_id]
        if before is not None:
            where.append("(c.last_message_at < ? OR (c.last_message_at = ? AND c.id > ?))")
            args += [before[0], before[0], before[1]]
        q = (query or "").strip()
        if q:
            where.append(
                "(c.title LIKE ? ESCAPE '\\' OR EXISTS ("
                " SELECT 1 FROM messages m, json_each(m.blocks_json) b"
                " WHERE m.conversation_id = c.id AND m.kind IN ('user', 'assistant')"
                " AND json_extract(b.value, '$.t') = 'text'"
                " AND json_extract(b.value, '$.text') LIKE ? ESCAPE '\\'))"
            )
            args += [_like(q), _like(q)]
        sql = (
            "SELECT c.id, c.title, c.last_message_at, c.kind, c.day FROM conversations c"  # noqa: S608
            f" WHERE {' AND '.join(where)} ORDER BY c.last_message_at DESC, c.id LIMIT ?"
        )
        async with self._conn.execute(sql, [*args, limit + 1]) as cur:
            rows = list(await cur.fetchall())
        out = [
            ChatRow(r["id"], r["title"], int(r["last_message_at"]), r["kind"], r["day"])
            for r in rows[:limit]
        ]
        return out, len(rows) > limit

    async def delete(self, conversation_id: str, owner_id: str, now_ms: int) -> bool:
        """Soft delete (SPN-9): hidden from the list and from resuming, kept on disk."""
        cur = await self._conn.execute(
            "UPDATE conversations SET deleted_at = ?"  # noqa: S608 - fixed text only
            f" WHERE id = ? AND owner_id = ? AND {_PAGE_KINDS} AND deleted_at IS NULL",
            (now_ms, conversation_id, owner_id),
        )
        return cur.rowcount == 1

    async def restore(self, conversation_id: str, owner_id: str) -> bool:
        """Undo a delete; returns whether it brought one back (False for one not deleted,
        and for a day whose date has a fresh day conversation since)."""
        cur = await self._conn.execute(
            "UPDATE conversations SET deleted_at = NULL"  # noqa: S608 - fixed text only
            f" WHERE id = ? AND owner_id = ? AND {_PAGE_KINDS} AND deleted_at IS NOT NULL"
            " AND NOT (kind = 'day' AND EXISTS (SELECT 1 FROM conversations o"
            " WHERE o.owner_id = conversations.owner_id AND o.kind = 'day'"
            " AND o.day = conversations.day AND o.deleted_at IS NULL))",
            (conversation_id, owner_id),
        )
        return cur.rowcount == 1

    async def amend_tail(
        self, conversation_id: str, owner_id: str, message_id: int, msg: Msg
    ) -> bool:
        """Replace a conversation's last message, and only its last; returns whether it
        did. The one change history allows: her last spoken reply trimmed to what was
        heard, before the model has been sent it (VOI-9, D8). Anything saved after it,
        in this conversation, makes this a no-op."""
        cur = await self._conn.execute(
            "UPDATE messages SET blocks_json = ?, raw_json = ?"
            " WHERE id = ? AND conversation_id = ? AND owner_id = ? AND role = 'assistant'"
            " AND id = (SELECT MAX(id) FROM messages WHERE conversation_id = ?)",
            (
                json.dumps([_dump(b) for b in msg.blocks], ensure_ascii=False),
                _dump_raw(msg.raw),
                message_id,
                conversation_id,
                owner_id,
                conversation_id,
            ),
        )
        return cur.rowcount == 1

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
        # None for background work in no conversation: a watcher's line.
        conversation_id: str | None,
        provider: str,
        model: str,
        usage: Usage,
        cost_usd: float | None,
        now_ms: int,
        # The cap this call counts against (trillion.spend.prices); None stores
        # `provider` there, as the migration did for old rows.
        provider_id: str | None = None,
        caller: Caller = "conversation",
        # What `usage` counts: a voice row's characters or a hearing row's seconds go
        # in input_tokens, with the other three left at 0.
        units: Unit = "tokens",
    ) -> None:
        await self._conn.execute(
            "INSERT INTO usage (owner_id, conversation_id, provider, model, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, created_at,"
            " provider_id, caller, units)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                provider if provider_id is None else provider_id,
                caller,
                units,
            ),
        )

    async def spend_since(
        self, owner_id: str, since_ms: int, until_ms: int | None = None
    ) -> list[dict[str, Any]]:
        """Spend per provider id, caller and unit from `since_ms`, up to and
        including `until_ms` when given (BRN-7), for the ledger. `cost_usd` sums the priced
        rows, None when none was priced; `unpriced` is the quantity of the rows
        nothing priced, never $0."""
        async with self._conn.execute(
            "SELECT provider_id, caller, units, SUM(cost_usd),"
            " SUM(CASE WHEN cost_usd IS NULL THEN input_tokens + output_tokens"
            " + cache_read_tokens + cache_write_tokens ELSE 0 END),"
            " SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens)"
            " FROM usage WHERE owner_id = ? AND created_at >= ? AND created_at <= ?"
            " GROUP BY provider_id, caller, units",
            (owner_id, since_ms, _FOREVER if until_ms is None else until_ms),
        ) as cur:
            names = ("provider_id", "caller", "units", "cost_usd", "unpriced", "quantity")
            return [dict(zip(names, r, strict=True)) for r in await cur.fetchall()]

    async def usage_since(self, owner_id: str, since_ms: int) -> list[dict[str, Any]]:
        """Usage per provider and model since a moment (BRN-4). A NULL cost is unknown,
        not free: it is left out of `cost_usd` and counted in `unpriced_*` instead.

        Rows are also split by provider id and unit. `unpriced` is the unpriced
        quantity in `unit`; `unpriced_tokens` is that quantity only when the unit is
        tokens, so characters or seconds are never shown as tokens."""
        async with self._conn.execute(
            "SELECT provider, model, SUM(input_tokens), SUM(output_tokens),"
            " SUM(cache_read_tokens), SUM(cache_write_tokens), SUM(cost_usd),"
            " SUM(cost_usd IS NULL),"
            " SUM(CASE WHEN cost_usd IS NULL AND units = 'tokens' THEN input_tokens"
            " + output_tokens + cache_read_tokens + cache_write_tokens ELSE 0 END),"
            " provider_id, units,"
            " SUM(CASE WHEN cost_usd IS NULL THEN input_tokens + output_tokens"
            " + cache_read_tokens + cache_write_tokens ELSE 0 END)"
            " FROM usage WHERE owner_id = ? AND created_at >= ?"
            " GROUP BY provider, model, provider_id, units"
            " ORDER BY SUM(cost_usd) IS NULL, SUM(cost_usd) DESC,"
            " SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) DESC",
            (owner_id, since_ms),
        ) as cur:
            names = (
                "provider",
                "model",
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "cost_usd",
                # Named before the spend caps, when a row was a turn: it counts unpriced
                # rows, which are now one per model call (BRN-7). Kept for the API.
                "unpriced_turns",
                "unpriced_tokens",
                "provider_id",
                "unit",
                "unpriced",
            )
            rows = [dict(zip(names, row, strict=True)) for row in await cur.fetchall()]
        for r in rows:
            # Float sums drift (0.5 + 0.7 = 1.2000000000000002); a micro-dollar is plenty.
            if r["cost_usd"] is not None:
                r["cost_usd"] = round(r["cost_usd"], 6)
        return rows

    async def record_timing(self, owner_id: str, row: Mapping[str, Any], now_ms: int) -> int:
        """Keep one turn's timings (ONB-11). Returns its id, for a late `played_ms`."""
        names = ("conversation_id", *TIMING_FIELDS)
        async with self._conn.execute(
            f"INSERT INTO turn_timings (owner_id, {', '.join(names)}, created_at)"  # noqa: S608 - fixed names
            f" VALUES (?, {', '.join('?' for _ in names)}, ?)",
            (owner_id, *(row.get(n) for n in names), now_ms),
        ) as cur:
            return int(cur.lastrowid or 0)

    async def timing_played(self, owner_id: str, timing_id: int, played_ms: int) -> None:
        """The page heard the turn's first audio after the turn was saved. Set once."""
        await self._conn.execute(
            "UPDATE turn_timings SET played_ms = ?"
            " WHERE id = ? AND owner_id = ? AND played_ms IS NULL",
            (played_ms, timing_id, owner_id),
        )


# What a turn's timing row holds, beside its owner, conversation and time (migrations
# 007 and 009).
TIMING_FIELDS = (
    "turn",
    "via",
    "first_in_session",
    "provider",
    "model",
    "voice",
    "ears",
    "stop",
    "tools",
    "endpoint_ms",
    "heard_ms",
    "first_text_ms",
    "first_sentence_ms",
    "first_audio_ms",
    "played_ms",
    "saved_ms",
    "calls_json",
    "draft",
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
