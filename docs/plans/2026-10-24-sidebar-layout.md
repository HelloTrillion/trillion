# Sidebar, conversation history and a docking orb: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the conversation into a left sidebar with a thread list (titles she writes, search, rename, delete with undo), give the orb the stage to its right, and add a workspace where Settings opens while the orb docks small.

**Spec:** `docs/specs/2026-10-24-sidebar-layout.md`. Read it first; this plan does not repeat its reasons.

**Architecture:** Backend: a migration adds `title`, `title_source` and `last_message_at` to `conversations`; `SqliteConversationStore` stays their one writer; a new `brain/title.py` asks the conversation's model for a title after her first reply; a new `api/conversations.py` router lists, renames, deletes and restores. Galaxy: a pure `stage.ts` computes a lens shift and zoom for the orb camera, exposed as `setStage`. Web: pure layout and thread helpers (tested), then thin DOM modules `sidebar.ts`, `threads.ts`, `workspace.ts`, `captions.ts`; `panel.ts` draws Settings into the workspace through a small `Surface` interface instead of a `<dialog>`.

**Tech stack:** Python 3.13 (uv, FastAPI, aiosqlite, pytest-asyncio, ruff, strict mypy); TypeScript (Vite, vitest, three.js); pnpm workspace.

**Commands**
- Python tests: `uv run pytest -q <path>`; lint and types: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
- Web: `pnpm -C apps/web test`, `pnpm -C apps/web typecheck`, `pnpm -C apps/web build`
- Galaxy: `pnpm -C packages/galaxy test`, `pnpm -C packages/galaxy typecheck`
- Repo checks: `python3 scripts/check_one_writer.py`, `python3 scripts/containment_check.py`

**House rules for every task**
- Commit with `git commit -s`, message ending with a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Subject style: `Sidebar: <what> (#ui)` is fine; match recent history (`git log --oneline -10`).
- No real people or businesses in code, tests or fixtures (`containment_check.py`). Use names like "Sam" and "the trip".
- Comments: short, plain, say why. Match the surrounding style (see `conversation_store.py`, `panel.ts`).
- Never play sound in the browser preview.

---

## File map

| File | Responsibility |
|---|---|
| `src/trillion/storage/migrations/010_conversation_titles.sql` | New columns, index, backfill |
| `src/trillion/storage/sqlite/conversation_store.py` | Title on first user message, `last_message_at`, `set_title`, `title_of`, `list_chats`, `delete`, `restore` |
| `scripts/check_one_writer.py` | Lists the new columns as not decisions |
| `src/trillion/brain/title.py` | `name()` and `clean()` |
| `tests/brain/fakes.py` | `ScriptedProvider` passes naming calls through without using a scripted reply |
| `src/trillion/api/conversations.py` | The router |
| `src/trillion/api/app.py` | Includes the router |
| `src/trillion/api/session.py` | `title` in `session`, naming after first reply, `conversation.title`, `deleted()` |
| `packages/galaxy/src/stage.ts` | Pure orb placement maths |
| `packages/galaxy/src/index.ts` | `setStage` |
| `packages/galaxy/demo/*` | Controls for `setStage` |
| `apps/web/src/layout.ts` | Pure: stage rect, dock rect, phone or desktop |
| `apps/web/src/threads.ts` | Pure grouping and the thread list DOM |
| `apps/web/src/sidebar.ts` | The aside, width, hidden state |
| `apps/web/src/workspace.ts` | The workspace region and orb placement |
| `apps/web/src/captions.ts` | Captions while the sidebar is hidden |
| `apps/web/src/panel.ts` | Draws into a `Surface` |
| `apps/web/src/view.ts` | `title` state, `conversation.title`, `deleted` |
| `apps/web/index.html`, `apps/web/src/app.css`, `apps/web/src/main.ts` | Shell and wiring |
| Tests | `tests/storage/test_conversation_titles.py`, `tests/brain/test_title.py`, `tests/api/test_conversations.py`, `tests/api/test_naming.py`, `packages/galaxy/test/stage.test.ts`, `apps/web/test/{layout,threads,view}.test.ts` |

---

### Task 1: Migration and store

**Files:**
- Create: `src/trillion/storage/migrations/010_conversation_titles.sql`
- Modify: `src/trillion/storage/sqlite/conversation_store.py`
- Modify: `scripts/check_one_writer.py` (`NOT_DECISIONS`)
- Create: `tests/storage/__init__.py` (empty), `tests/storage/test_conversation_titles.py`

Background: `conversations` has `id, owner_id, created_at, deleted_at, kind` (`kind` is `'chat'` or `'scheduled'`, from migration 008). Messages are saved by `append` and `append_many`. Owner `"local"` exists after `migrate()` (see `tests/scheduler/conftest.py` for a store on a fresh database).

- [ ] **Step 1: Write the migration**

```sql
-- Conversation titles and recency, for the thread list (SUR-1). The store is their
-- one writer. A title is shown as text only: it can hold the user's words or the
-- model's, so the page never treats it as markup.
ALTER TABLE conversations ADD COLUMN title TEXT;
-- 'words': the user's first words; 'model': she named it; 'user': the user renamed it.
ALTER TABLE conversations ADD COLUMN title_source TEXT
    CHECK (title_source IN ('words', 'model', 'user'));
-- Epoch ms of the newest message; NULL until the first one. The list sorts on it.
ALTER TABLE conversations ADD COLUMN last_message_at INTEGER;
CREATE INDEX conversations_by_recent
    ON conversations (owner_id, kind, deleted_at, last_message_at DESC);

-- Existing conversations: recency from their newest message, and a title from the
-- first thing the user typed or said.
UPDATE conversations SET last_message_at =
    (SELECT MAX(created_at) FROM messages m WHERE m.conversation_id = conversations.id);
UPDATE conversations SET
    title = (
        SELECT substr(trim(replace(replace(json_extract(b.value, '$.text'), char(10), ' '), char(13), ' ')), 1, 60)
        FROM messages m, json_each(m.blocks_json) b
        WHERE m.conversation_id = conversations.id AND m.kind = 'user'
          AND json_extract(b.value, '$.type') = 'text'
        ORDER BY m.id LIMIT 1
    ),
    title_source = 'words'
WHERE EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = conversations.id AND m.kind = 'user');
```

Before relying on `$.type = 'text'`, check `_dump` in `conversation_store.py` for the exact key and value a text block is saved with, and use those.

- [ ] **Step 2: Write the failing tests** (`tests/storage/test_conversation_titles.py`)

```python
"""Conversation titles, recency and the thread list (SUR-1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from trillion.brain.types import Msg, TextBlock
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import SqliteConversationStore

OWNER = "local"


def said(text: str) -> Msg:
    return Msg(role="user", blocks=(TextBlock(text=text),))


def reply(text: str) -> Msg:
    return Msg(role="assistant", blocks=(TextBlock(text=text),))


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteConversationStore]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    yield SqliteConversationStore(conn)
    await conn.close()


async def chat(store: SqliteConversationStore, cid: str, at: int, *lines: str) -> None:
    await store.create(cid, OWNER, at)
    for i, line in enumerate(lines):
        await store.append(cid, OWNER, "user", said(line), at + i)


async def test_the_first_words_title_a_conversation(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "  What's on\nFriday?  ", "And Saturday?")
    assert await store.title_of("c1", OWNER) == ("What's on Friday?", "words")


async def test_her_replies_never_title_it(store: SqliteConversationStore) -> None:
    await store.create("c1", OWNER, 1000)
    await store.append("c1", OWNER, "assistant", reply("Hello, I'm here."), 1000)
    assert await store.title_of("c1", OWNER) == (None, None)


async def test_a_long_first_message_is_cut(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "word " * 40)
    title, _ = await store.title_of("c1", OWNER)
    assert title is not None and len(title) <= 80


async def test_a_model_title_never_replaces_a_users(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "hey")
    assert await store.set_title("c1", OWNER, "The trip", "user")
    assert not await store.set_title("c1", OWNER, "Greeting", "model")
    assert await store.title_of("c1", OWNER) == ("The trip", "user")
    assert await store.set_title("c1", OWNER, "Trip plans", "user")
    assert await store.title_of("c1", OWNER) == ("Trip plans", "user")


async def test_the_list_is_newest_first_and_pages(store: SqliteConversationStore) -> None:
    for n in range(5):
        await chat(store, f"c{n}", 1000 * (n + 1), f"line {n}")
    page, more = await store.list_chats(OWNER, limit=2)
    assert [r.id for r in page] == ["c4", "c3"] and more
    page, more = await store.list_chats(OWNER, before_ms=page[-1].last_message_at, limit=2)
    assert [r.id for r in page] == ["c2", "c1"] and more
    page, more = await store.list_chats(OWNER, before_ms=page[-1].last_message_at, limit=2)
    assert [r.id for r in page] == ["c0"] and not more


async def test_empty_deleted_and_scheduled_are_not_listed(store: SqliteConversationStore) -> None:
    await store.create("empty", OWNER, 1000)
    await chat(store, "gone", 2000, "bye")
    await store.delete("gone", OWNER, 3000)
    await store.create("sched", OWNER, 1000, kind="scheduled")
    await store.append("sched", OWNER, "user", said("run it"), 1000)
    await chat(store, "kept", 4000, "hi")
    page, _ = await store.list_chats(OWNER)
    assert [r.id for r in page] == ["kept"]


async def test_delete_hides_and_restore_brings_back(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "hi")
    assert await store.delete("c1", OWNER, 2000)
    assert not await store.exists("c1", OWNER)
    assert await store.restore("c1", OWNER)
    assert await store.exists("c1", OWNER)
    assert not await store.delete("nope", OWNER, 2000)


async def test_search_matches_titles_and_words_not_json_keys(store: SqliteConversationStore) -> None:
    await chat(store, "a", 1000, "Book the dentist")
    await chat(store, "b", 2000, "hello")
    await store.append("b", OWNER, "assistant", reply("Your 50% off code arrived"), 2001)
    await chat(store, "c", 3000, "nothing here")
    ids = lambda rows: sorted(r.id for r in rows)  # noqa: E731
    assert ids((await store.list_chats(OWNER, query="DENTIST"))[0]) == ["a"]
    assert ids((await store.list_chats(OWNER, query="50%"))[0]) == ["b"]
    assert ids((await store.list_chats(OWNER, query="text"))[0]) == []
    assert ids((await store.list_chats(OWNER, query="_"))[0]) == []


async def test_recency_follows_the_newest_message(store: SqliteConversationStore) -> None:
    await chat(store, "c1", 1000, "first")
    await store.append_many("c1", OWNER, "assistant", [reply("a"), reply("b")], 5000)
    page, _ = await store.list_chats(OWNER)
    assert page[0].last_message_at == 5000
```

- [ ] **Step 3: Run them and see them fail**

Run: `uv run pytest -q tests/storage/test_conversation_titles.py`
Expected: FAIL (`title_of`, `set_title`, `list_chats`, `delete`, `restore` do not exist).

- [ ] **Step 4: Implement in `conversation_store.py`**

Add near the top:

```python
from dataclasses import dataclass
from typing import Literal

TitleSource = Literal["words", "model", "user"]
TITLE_MAX = 80
WORDS_TITLE_MAX = 60


@dataclass(frozen=True)
class ChatRow:
    id: str
    title: str | None
    last_message_at: int


def tidy_title(text: str, limit: int = TITLE_MAX) -> str:
    """One line, single spaces, cut at `limit`."""
    return " ".join(text.split())[:limit].strip()


def _first_text(msg: Msg) -> str:
    return " ".join(b.text for b in msg.blocks if isinstance(b, TextBlock)).strip()


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
```

(`TextBlock` is already imported in the store for `_dump`/`_load`; reuse that import.)

Add a private helper the two append methods call after their insert:

```python
    async def _touched(
        self, conversation_id: str, owner_id: str, kind: Kind, msgs: Sequence[Msg], now_ms: int
    ) -> None:
        """Recency, and the first words as a title until there is one."""
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
```

Call `await self._touched(conversation_id, owner_id, kind, [msg], now_ms)` at the end of `append` (before `return`) and `await self._touched(conversation_id, owner_id, kind, msgs, now_ms)` in `append_many` after the insert (only when `msgs` is non-empty).

Add the public methods:

```python
    async def title_of(
        self, conversation_id: str, owner_id: str
    ) -> tuple[str | None, TitleSource | None]:
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
            " WHERE id = ? AND owner_id = ? AND deleted_at IS NULL" + guard,
            (clean, source, conversation_id, owner_id),
        )
        return cur.rowcount == 1

    async def list_chats(
        self,
        owner_id: str,
        *,
        query: str | None = None,
        before_ms: int | None = None,
        limit: int = 50,
    ) -> tuple[list[ChatRow], bool]:
        """The user's conversations with something in them, newest first, and whether
        there are more. A query matches the title or the words of what was said, never
        tool results and never the JSON around the words."""
        where = [
            "c.owner_id = ?",
            "c.kind = 'chat'",
            "c.deleted_at IS NULL",
            "c.last_message_at IS NOT NULL",
        ]
        args: list[object] = [owner_id]
        if before_ms is not None:
            where.append("c.last_message_at < ?")
            args.append(before_ms)
        q = (query or "").strip()
        if q:
            where.append(
                "(c.title LIKE ? ESCAPE '\\' OR EXISTS ("
                " SELECT 1 FROM messages m, json_each(m.blocks_json) b"
                " WHERE m.conversation_id = c.id AND m.kind IN ('user', 'assistant')"
                " AND json_extract(b.value, '$.type') = 'text'"
                " AND json_extract(b.value, '$.text') LIKE ? ESCAPE '\\'))"
            )
            args += [_like(q), _like(q)]
        sql = (
            "SELECT c.id, c.title, c.last_message_at FROM conversations c"  # noqa: S608
            f" WHERE {' AND '.join(where)} ORDER BY c.last_message_at DESC, c.id LIMIT ?"
        )
        async with self._conn.execute(sql, [*args, limit + 1]) as cur:
            rows = await cur.fetchall()
        out = [ChatRow(r["id"], r["title"], int(r["last_message_at"])) for r in rows[:limit]]
        return out, len(rows) > limit

    async def delete(self, conversation_id: str, owner_id: str, now_ms: int) -> bool:
        """Soft delete (SPN-9): hidden from the list and from resuming, kept on disk."""
        cur = await self._conn.execute(
            "UPDATE conversations SET deleted_at = ?"
            " WHERE id = ? AND owner_id = ? AND kind = 'chat' AND deleted_at IS NULL",
            (now_ms, conversation_id, owner_id),
        )
        return cur.rowcount == 1

    async def restore(self, conversation_id: str, owner_id: str) -> bool:
        cur = await self._conn.execute(
            "UPDATE conversations SET deleted_at = NULL"
            " WHERE id = ? AND owner_id = ? AND kind = 'chat' AND deleted_at IS NOT NULL",
            (conversation_id, owner_id),
        )
        return cur.rowcount == 1
```

Use the same JSON key and value for a text block as `_dump` writes (check it; adjust `'$.type' = 'text'` if `_dump` differs, in the migration too). SQLite `LIKE` is case-insensitive for ASCII, which the spec accepts.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/storage/test_conversation_titles.py`
Expected: PASS.

- [ ] **Step 6: Migration backfill test**

Append to the same test file:

```python
async def test_the_migration_backfills_old_conversations(tmp_path: Path) -> None:
    from trillion.storage import migrate as m

    conn = await connect(tmp_path / "old.db")
    # Apply everything before 010, then write a conversation the old way.
    await m.migrate(conn, upto=9)
    await conn.execute("INSERT INTO conversations (id, owner_id, created_at) VALUES ('old', 'local', 1)")
    await conn.execute(
        "INSERT INTO messages (owner_id, conversation_id, role, kind, blocks_json, created_at)"
        " VALUES ('local', 'old', 'user', 'user', ?, 7)",
        ('[{"type": "text", "text": "Plan the\\ntrip"}]',),
    )
    await m.migrate(conn)
    store = SqliteConversationStore(conn)
    assert await store.title_of("old", OWNER) == ("Plan the trip", "words")
    assert (await store.list_chats(OWNER))[0][0].last_message_at == 7
    await conn.close()
```

If `migrate()` has no way to stop at a version, read `src/trillion/storage/migrate.py` and `tests/unit/test_migrate.py`, and follow how existing migration tests apply a partial set (add a keyword-only `upto: int | None = None` parameter if none exists, with a test-only purpose noted in its docstring). Use `_dump`'s real JSON shape in the inserted row.

Run: `uv run pytest -q tests/storage tests/unit/test_migrate.py`
Expected: PASS.

- [ ] **Step 7: One-writer check**

Run: `python3 scripts/check_one_writer.py`
It fails, naming `conversations.title`, `conversations.title_source`, `conversations.last_message_at` and `conversations.deleted_at` (if not already listed). Add each to `NOT_DECISIONS` with a reason, in the file's existing style, for example:

```python
    "conversations.title": "a label for the thread list; decides nothing (SUR-1)",
    "conversations.title_source": "who named it, so her title never replaces the user's",
    "conversations.last_message_at": "recency for sorting the thread list",
    "conversations.deleted_at": "soft delete of a conversation (SPN-9); hides it, decides no action",
```

Run again: Expected: exit 0. Also run `python3 scripts/check_one_writer.py --selftest`.

- [ ] **Step 8: Full Python suite, lint, types**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. A test that counted `UPDATE`s or asserted exact SQL may need care; fix the test only if it asserted something the spec changed.

- [ ] **Step 9: Commit**

```bash
git add src/trillion/storage tests/storage scripts/check_one_writer.py
git commit -s -m "Conversations: titles, recency, list, soft delete (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Naming a conversation (`brain/title.py`)

**Files:**
- Create: `src/trillion/brain/title.py`
- Modify: `tests/brain/fakes.py`
- Create: `tests/brain/test_title.py`

Background: `brain/compact.py:summarize` is the model for a one-off, tool-less call (`provider.stream(system=[SystemBlock(...)], messages=[...], tools=[], max_tokens=...)`, collecting `TextDelta` and `Done.usage`). Untrusted text is wrapped with `trillion.spine.untrusted.wrap(tool_name, text)`.

- [ ] **Step 1: Write the failing tests**

```python
"""She names a conversation from its first exchange (SUR-1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from tests.brain.fakes import ScriptedProvider, say
from trillion.brain import title
from trillion.brain.types import ModelEvent, Msg, TextBlock, Usage


def first(user: str, her: str) -> list[Msg]:
    return [
        Msg(role="user", blocks=(TextBlock(text=user),)),
        Msg(role="assistant", blocks=(TextBlock(text=her),)),
    ]


def test_clean_keeps_a_plain_title() -> None:
    assert title.clean('"Planning the trip."\n') == "Planning the trip"
    assert title.clean("Title: Moving the dentist") == "Moving the dentist"


def test_clean_refuses_empty_and_long() -> None:
    assert title.clean("   ") is None
    assert title.clean("x" * 81) is None
    assert title.clean("one two three four five six seven eight nine ten eleven") is None


async def test_name_asks_without_tools_and_wraps_the_conversation() -> None:
    model = ScriptedProvider(say("Moving the dentist"), names=True)
    named, usage = await title.name(model, first("move the dentist to monday", "Done, Monday at 3."))
    assert named == "Moving the dentist"
    system, messages = model.calls[0]
    body = messages[0].blocks[0]
    assert isinstance(body, TextBlock)
    assert "<untrusted_tool_result" in body.text and "move the dentist" in body.text
    assert model.tool_calls[0] == []  # no tools offered


async def test_a_failure_or_timeout_gives_none() -> None:
    assert (await title.name(ScriptedProvider(RuntimeError("down"), names=True), first("a", "b")))[0] is None

    class Slow(ScriptedProvider):
        async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:
            await asyncio.sleep(10)
            yield  # type: ignore[misc]

    assert (await title.name(Slow(names=True), first("a", "b"), timeout_s=0.01))[0] is None


async def test_an_unusable_answer_still_reports_its_usage() -> None:
    named, usage = await title.name(ScriptedProvider(say("x" * 200), names=True), first("a", "b"))
    assert named is None
    assert usage == Usage()  # or what `say` reports; read fakes.py and match it
```

Adjust the attribute names (`calls`, how tools are recorded) to what `ScriptedProvider` really keeps; read `tests/brain/fakes.py`. If it does not record `tools`, add a `tool_calls: list[list[ToolSchema]]` list it appends to.

- [ ] **Step 2: Make `ScriptedProvider` skip naming calls by default**

Every existing test scripts exact replies; an extra naming call would use one up. In `ScriptedProvider.__init__` add a keyword-only `names: bool = False`. At the top of `stream`, before recording the call or popping a reply:

```python
        if max_tokens == TITLE_TOKENS and not self._names:
            # She names the conversation after her first reply; scripts are about turns.
            yield Done(stop_reason="end_turn", usage=Usage())
            return
```

Import `TITLE_TOKENS` from `trillion.brain.title`. Match `Done`'s real constructor (read `brain/types.py`). Other fake providers in `tests/` that implement `stream` themselves: find them with `grep -rn "async def stream" tests` and give each the same early return, or confirm they are never used by a session that completes a first reply.

- [ ] **Step 3: Run the tests and see them fail**

Run: `uv run pytest -q tests/brain/test_title.py`
Expected: FAIL (`trillion.brain.title` missing).

- [ ] **Step 4: Implement `src/trillion/brain/title.py`**

```python
"""She names a conversation from its first exchange, for the thread list (SUR-1).

The exchange reaches the model wrapped as untrusted text: it is what the user and
she said, never an instruction, and no tools are offered, so nothing in it can act.
A failure leaves the first-words title in place, and nothing is shown or spoken."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from trillion.brain.compact import transcript
from trillion.brain.types import Done, ModelProvider, Msg, SystemBlock, TextBlock, TextDelta, Usage
from trillion.spine.untrusted import wrap

TITLE_TOKENS = 24
TIMEOUT_S = 10.0
MAX_CHARS = 80
MAX_WORDS = 8

INSTRUCTIONS = (
    "Write a title for this conversation: 3 to 6 words, sentence case, no quotes, "
    "no ending punctuation. Answer with the title only."
)

_LABEL = re.compile(r"^\s*title\s*:\s*", re.I)


def clean(text: str) -> str | None:
    """The model's answer as a title, or None when it is not one."""
    line = " ".join(text.split())
    line = _LABEL.sub("", line).strip().strip("\"'“”‘’`").rstrip(".!?;:,").strip()
    if not line or len(line) > MAX_CHARS or len(line.split()) > MAX_WORDS:
        return None
    return line


async def name(
    provider: ModelProvider, messages: Sequence[Msg], *, timeout_s: float = TIMEOUT_S
) -> tuple[str | None, Usage]:
    """A title from the conversation's first exchange, or None, and what the call
    used: every paid call is recorded (BRN-4), even one whose answer is not kept."""
    text: list[str] = []
    usage = Usage()

    async def ask() -> None:
        nonlocal usage
        async for ev in provider.stream(
            system=[SystemBlock(text=INSTRUCTIONS, cache=False)],
            messages=[Msg(role="user", blocks=(TextBlock(text=wrap("conversation", transcript(messages))),))],
            tools=[],
            max_tokens=TITLE_TOKENS,
        ):
            if isinstance(ev, TextDelta):
                text.append(ev.text)
            elif isinstance(ev, Done):
                usage = usage + ev.usage

    try:
        await asyncio.wait_for(ask(), timeout_s)
    except Exception:  # noqa: BLE001 - naming is best effort; the first words stay
        return None, usage
    return clean("".join(text)), usage
```

Check `provider.stream`'s exact keyword names against `compact.summarize` and match them. If `Usage` does not support `+`, follow what `summarize` does.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/brain && uv run mypy`
Expected: PASS.

- [ ] **Step 6: Run the whole suite** (the fake changed)

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/trillion/brain/title.py tests/brain
git commit -s -m "Brain: she names a conversation from its first exchange (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Naming in the session, and `title` on `session`

**Files:**
- Modify: `src/trillion/api/session.py`
- Create: `tests/api/test_naming.py`

Background: `Session.start` sends `session{...}` then `history`. `_one_turn` runs a turn; `_save_from_loop` saves her messages. `rt.by_conversation[id]` is the live session per conversation; `rt.sessions` all sessions. `record_usage` calls are shown around `session.py:1641-1657` (`_record_draft_usage` style).

- [ ] **Step 1: Write the failing tests** (`tests/api/test_naming.py`)

Use the `rt` fixture and `Page` helper style from `tests/api/test_session.py` (copy the `rt` fixture; import `Page` from there).

```python
"""She names a conversation after her first reply, once (SUR-1)."""

from __future__ import annotations

import asyncio

from tests.api.test_session import Page, rt  # noqa: F401 - fixture
from tests.brain.fakes import ScriptedProvider, say
from trillion.api.runtime import OWNER, Runtime
from trillion.api.session import Session


async def test_a_title_follows_her_first_reply(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(say("Monday at three."), say("Moving the dentist"), names=True)
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    assert page.all("session")[0]["title"] is None
    await s.handle({"type": "user.text", "text": "move the dentist to monday"})
    await page.until("conversation.title")
    assert page.all("conversation.title")[0] == {
        "type": "conversation.title", "id": s.id, "title": "Moving the dentist",
    }
    assert await rt.conversations.title_of(s.id, OWNER) == ("Moving the dentist", "model")
    await s.close()


async def test_it_names_once(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(say("One."), say("First title"), say("Two."), names=True)
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "user.text", "text": "one"})
    await page.until("conversation.title")
    await s.handle({"type": "user.text", "text": "two"})
    await page.until("state", 6)
    await asyncio.sleep(0.05)
    assert len(page.all("conversation.title")) == 1
    await s.close()


async def test_a_resumed_named_conversation_is_not_renamed(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(say("Hi."), say("Again."), names=True)
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await rt.conversations.set_title(s.id, OWNER, "Mine", "user")
    await s.handle({"type": "user.text", "text": "hello"})
    await page.until("state", 3)
    await asyncio.sleep(0.05)
    assert page.all("conversation.title") == []
    await s.close()
    page2 = Page(rt)
    s2 = await Session.start(rt, page2.send, s.id)
    assert page2.all("session")[0]["title"] == "Mine"
    await s2.close()


async def test_a_slow_namer_does_not_hold_the_next_turn(rt: Runtime) -> None:
    ...
```

Fill in the last test with the `Gated` provider from `test_session.py`: hold call index 1 (the naming call), send a second message, and assert the second reply's `reply.delta` arrives while the gate is still closed; then open the gate and assert the title arrives. Read how `test_two_turns_never_run_at_once` drives `Gated` and the exact `handle` message shape (`user.text` may be named differently: check `Session.handle` and use the real type). Check how many `state` messages a turn sends and use `page.until` on something stable (for example `reply.done` if it exists) rather than counting states, if counting proves brittle.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest -q tests/api/test_naming.py`
Expected: FAIL (`title` missing from `session`, no `conversation.title`).

- [ ] **Step 3: Implement in `session.py`**

1. In `Session.start`, before sending `session`, read the title: `title, source = await rt.conversations.title_of(session.id, OWNER)` (for a new conversation this is `(None, None)`). Add `title=title` to the `session` message. Keep `source` on the session: `session._title_source = source`.
2. In `__init__` add:

```python
        # Who named this conversation, if anyone, and her naming task (SUR-1). She
        # names it once, after her first reply, and never over the user's own name.
        self._title_source: str | None = None
        self._naming: asyncio.Task[None] | None = None
```

3. Add:

```python
    def _maybe_name(self) -> None:
        """After her first reply: name the conversation in the background."""
        if self._naming is not None or self._title_source in ("model", "user"):
            return
        replies = [m for m in self._history if m.role == "assistant" and any(
            isinstance(b, TextBlock) and b.text.strip() for b in m.blocks)]
        said = [m for m, k in zip(self._history, self._kinds, strict=True) if k == "user"]
        if not replies or not said or self._rt.provider is None:
            return
        self._naming = asyncio.create_task(self._name(said[0], replies[0]))

    async def _name(self, said: Msg, reply: Msg) -> None:
        rt = self._rt
        provider = rt.provider
        if provider is None:
            return
        named, usage = await title.name(provider, [said, reply])
        await self._record_usage(provider, usage)
        if named is None:
            return
        if await rt.conversations.set_title(self.id, OWNER, named, "model"):
            self._title_source = "model"
            await self._broadcast_title(named)

    async def _broadcast_title(self, text: str) -> None:
        for s in list(self._rt.sessions):
            if s.id == self.id:
                await s._send("conversation.title", id=self.id, title=text)
```

`_record_usage` stands for the existing "record this provider's usage if non-zero" helper near line 1641; reuse it (rename nothing; if it is shaped differently, call `rt.conversations.record_usage(...)` the same way, skipping when `usage == Usage()`). Import `from trillion.brain import title` at the top (watch for a name clash with any local variable called `title`; name the module import `naming` if so).

4. Call `self._maybe_name()` at the end of `_one_turn`, after the turn's messages are saved and only when the turn completed (in the success path, not in `except`/cancel paths). Read the end of `_one_turn` to find that spot.
5. In `close()`, cancel `self._naming` if it is running.
6. Set `self._title_source = "user"` when the conversation is renamed through the API (Task 4 does this through `rt.by_conversation`).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/api/test_naming.py tests/api/test_session.py`
Expected: PASS.

- [ ] **Step 5: Full suite, lint, types**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/trillion/api/session.py tests/api/test_naming.py
git commit -s -m "Session: she names the conversation after her first reply (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The conversations API

**Files:**
- Create: `src/trillion/api/conversations.py`
- Modify: `src/trillion/api/app.py` (include the router next to `create_usage_router`)
- Modify: `src/trillion/api/session.py` (`deleted()`, `renamed()`)
- Create: `tests/api/test_conversations.py`

Background: routers are built as `create_X_router(rt)` and included in `app.py`. Auth (bearer token, origin, JSON content type for writes) is applied by the app for every `/api` route; confirm by reading how `create_usage_router` is protected and how `tests/api/test_usage.py` calls it (`app.client.get(PATH, headers=app.auth)`).

- [ ] **Step 1: Write the failing tests**

```python
"""The thread list's API: list, search, rename, delete and undo (SUR-1)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from tests.api.test_server import ORIGIN, App, home, make  # noqa: F401
from tests.brain.fakes import ScriptedProvider, say
from trillion.api.runtime import OWNER
from trillion.brain.types import Msg, TextBlock

PATH = "/api/conversations"


def seed(app: App, cid: str, at: int, text: str) -> None:
    portal = app.client.portal
    assert portal is not None
    store = app.rt.conversations
    portal.call(store.create, cid, OWNER, at)
    portal.call(store.append, cid, OWNER, "user", Msg(role="user", blocks=(TextBlock(text=text),)), at)


def test_lists_newest_first(make) -> None:
    app = make(None)
    seed(app, "a", 1000, "first")
    seed(app, "b", 2000, "second")
    r = app.client.get(PATH, headers=app.auth)
    assert r.status_code == 200
    body = r.json()
    assert [c["id"] for c in body["conversations"]] == ["b", "a"]
    assert body["conversations"][0] == {"id": "b", "title": "second", "last_message_at": 2000}
    assert body["more"] is False


def test_search_and_paging(make) -> None:
    app = make(None)
    for n in range(3):
        seed(app, f"c{n}", 1000 + n, f"trip {n}")
    r = app.client.get(PATH, params={"q": "trip", "limit": 2}, headers=app.auth).json()
    assert [c["id"] for c in r["conversations"]] == ["c2", "c1"] and r["more"]
    r = app.client.get(PATH, params={"before": 1001, "before_id": "c1", "limit": 2}, headers=app.auth).json()
    assert [c["id"] for c in r["conversations"]] == ["c0"]


def test_needs_the_token(make) -> None:
    app = make(None)
    assert app.client.get(PATH).status_code in (401, 403)


def test_rename(make) -> None:
    app = make(None)
    seed(app, "a", 1000, "first")
    r = app.client.patch(f"{PATH}/a", json={"title": "  The   trip "}, headers=app.auth | ORIGIN)
    assert r.status_code == 200 and r.json() == {"id": "a", "title": "The trip"}
    assert app.client.patch(f"{PATH}/a", json={"title": "  "}, headers=app.auth | ORIGIN).status_code == 400
    assert app.client.patch(f"{PATH}/nope", json={"title": "x"}, headers=app.auth | ORIGIN).status_code == 404


def test_delete_and_restore(make) -> None:
    app = make(None)
    seed(app, "a", 1000, "first")
    assert app.client.delete(f"{PATH}/a", headers=app.auth | ORIGIN).status_code == 200
    assert app.client.get(PATH, headers=app.auth).json()["conversations"] == []
    assert app.client.delete(f"{PATH}/a", headers=app.auth | ORIGIN).status_code == 404
    assert app.client.post(f"{PATH}/a/restore", headers=app.auth | ORIGIN).status_code == 200
    assert len(app.client.get(PATH, headers=app.auth).json()["conversations"]) == 1


def test_deleting_an_open_conversation_tells_its_page(make) -> None:
    app = make(ScriptedProvider(say("Hi.")))
    with app.connect() as chat:
        cid = chat.session["id"]
        chat.say("hello")  # use the real helper that sends a message and waits for the reply
        assert app.client.delete(f"{PATH}/{cid}", headers=app.auth | ORIGIN).status_code == 200
        assert chat.until("error")[-1]["code"] == "deleted"
    with app.connect(resume=cid) as chat:
        assert chat.session["id"] != cid
```

Read `tests/api/test_server.py` for the real `make` fixture signature, `Chat` helpers (`until`, how to send a message and wait), and whether `chat.session` holds the `session` message or the first `state`; adjust names to match. Writes need the origin header and JSON content type per SPN-11; send what other write tests send.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest -q tests/api/test_conversations.py`
Expected: FAIL (404 on every route).

- [ ] **Step 3: Implement the router**

```python
"""The thread list (SUR-1): the user's conversations, newest first, with search,
rename, and a soft delete that can be undone (SPN-9)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from trillion.api.runtime import OWNER, Runtime
from trillion.storage.sqlite.conversation_store import tidy_title

LIMIT_MAX = 100


class Rename(BaseModel):
    title: str


def create_conversations_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")

    @router.get("")
    async def listing(
        q: str = "", before: int | None = None, before_id: str = "", limit: int = 50
    ) -> dict[str, Any]:
        # The cursor is the last row shown: its time and id, so equal times never skip.
        cursor = (before, before_id) if before is not None else None
        rows, more = await rt.conversations.list_chats(
            OWNER, query=q or None, before=cursor, limit=max(1, min(limit, LIMIT_MAX))
        )
        return {
            "conversations": [
                {"id": r.id, "title": r.title, "last_message_at": r.last_message_at} for r in rows
            ],
            "more": more,
        }

    @router.patch("/{conversation_id}")
    async def rename(conversation_id: str, body: Rename) -> dict[str, Any]:
        title = tidy_title(body.title)
        if not title:
            raise HTTPException(400, "bad_request")
        if not await rt.conversations.set_title(conversation_id, OWNER, title, "user"):
            raise HTTPException(404, "not_found")
        live = rt.by_conversation.get(conversation_id)
        if live is not None:
            await live.renamed(title)
        return {"id": conversation_id, "title": title}

    @router.delete("/{conversation_id}")
    async def delete(conversation_id: str) -> dict[str, Any]:
        if not await rt.conversations.delete(conversation_id, OWNER, rt.clock()):
            raise HTTPException(404, "not_found")
        live = rt.by_conversation.get(conversation_id)
        if live is not None:
            await live.deleted()
        return {"id": conversation_id}

    @router.post("/{conversation_id}/restore")
    async def restore(conversation_id: str) -> dict[str, Any]:
        if not await rt.conversations.restore(conversation_id, OWNER):
            raise HTTPException(404, "not_found")
        return {"id": conversation_id}

    return router
```

Match how other routers shape errors (some use `{"error": code}` bodies; read `api/schedules.py` and copy its error style exactly).

In `session.py` add:

```python
    async def renamed(self, title: str) -> None:
        """The user named this conversation: hers will never replace it."""
        self._title_source = "user"
        if self._naming is not None:
            self._naming.cancel()
        await self._broadcast_title(title)

    async def deleted(self) -> None:
        """The conversation was deleted elsewhere: the page starts a new one."""
        await self._send("error", code="deleted")
```

- [ ] **Step 4: Include the router in `app.py`** next to `create_usage_router(rt)`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/api/test_conversations.py`
Expected: PASS.

- [ ] **Step 6: Full suite, lint, types, repo checks**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && python3 scripts/check_one_writer.py && python3 scripts/containment_check.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/trillion/api tests/api/test_conversations.py
git commit -s -m "API: list, rename, delete and restore conversations (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Galaxy `setStage`

**Files:**
- Create: `packages/galaxy/src/stage.ts`
- Modify: `packages/galaxy/src/index.ts`
- Create: `packages/galaxy/test/stage.test.ts`
- Modify: the demo page under `packages/galaxy/demo/` (read it first)

Background: the orb has its own `PerspectiveCamera` (fov 45, at z 4.2) and is drawn after the landscape, which has its own camera; moving the orb's projection never moves the background. The outermost ring has radius 1.75 (`orb/orb.ts`), so the orb's extent is 3.5 world units across. Today `resize()` sets `orbCamera.zoom = Math.min(1, w / h)`.

Maths: for a perspective camera, adding `a` to projection matrix element `[8]` and `b` to `[9]` shifts every point by `-a`, `-b` in NDC (`x_ndc' = x_ndc - a`) without changing perspective. To put the orb's centre at CSS pixel `(x, y)` in a `w × h` canvas: `ndcX = 2x/w - 1`, `ndcY = 1 - 2y/h`, so set `[8] = -ndcX`, `[9] = -ndcY`. Its on-screen diameter at zoom `z` is `D = EXTENT / (2 · d · tan(fov/2)) · h · z` with `d = 4.2`, so the zoom for diameter `S` is `z = S · 2 · d · tan(fov/2) / (EXTENT · h)`.

- [ ] **Step 1: Write the failing tests**

```ts
import { PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { ORB_EXTENT, ORB_DISTANCE, easeRect, placeOrb } from '../src/stage';

function camera(w: number, h: number): PerspectiveCamera {
  const c = new PerspectiveCamera(45, w / h, 0.1, 100);
  c.position.set(0, 0, ORB_DISTANCE);
  c.updateMatrixWorld();
  return c;
}

const px = (c: PerspectiveCamera, p: Vector3, w: number, h: number): [number, number] => {
  const v = p.clone().project(c);
  return [((v.x + 1) / 2) * w, ((1 - v.y) / 2) * h];
};

describe('placeOrb', () => {
  it('puts the orb centre on the requested pixel', () => {
    const c = camera(1200, 800);
    placeOrb(c, { x: 780, y: 400, size: 600 }, 1200, 800);
    const [x, y] = px(c, new Vector3(0, 0, 0), 1200, 800);
    expect(x).toBeCloseTo(780, 0);
    expect(y).toBeCloseTo(400, 0);
  });

  it('makes the outer ring the requested diameter', () => {
    const c = camera(1200, 800);
    placeOrb(c, { x: 1104, y: 704, size: 96 }, 1200, 800);
    const [left] = px(c, new Vector3(-ORB_EXTENT / 2, 0, 0), 1200, 800);
    const [right] = px(c, new Vector3(ORB_EXTENT / 2, 0, 0), 1200, 800);
    expect(right - left).toBeCloseTo(96, 0);
  });
});

describe('easeRect', () => {
  it('jumps when told to, and approaches otherwise', () => {
    const from = { x: 0, y: 0, size: 100 };
    const to = { x: 100, y: 50, size: 200 };
    expect(easeRect(from, to, 0.016, true)).toEqual(to);
    const mid = easeRect(from, to, 0.016, false);
    expect(mid.x).toBeGreaterThan(0);
    expect(mid.x).toBeLessThan(100);
    let r = from;
    for (let i = 0; i < 60; i++) r = easeRect(r, to, 1 / 60, false); // ~1s, well past 300 ms
    expect(r.x).toBeCloseTo(100, 0);
    expect(r.size).toBeCloseTo(200, 0);
  });
});
```

Note: the size test's point at `±EXTENT/2` sits at z = 0, the orb's plane; that is the ring's diameter seen face on.

- [ ] **Step 2: Run and see them fail**

Run: `pnpm -C packages/galaxy test`
Expected: FAIL (`../src/stage` missing).

- [ ] **Step 3: Implement `stage.ts`**

```ts
/**
 * Where the orb sits on screen (GAL-5). The host gives a centre and a diameter in
 * CSS pixels; the orb's camera is shifted and zoomed to match, so the landscape
 * behind it, which has its own camera, never moves.
 */
import type { PerspectiveCamera } from 'three';
import { approach } from './easing';

/** CSS pixels: the orb's centre, and the outer ring's diameter. */
export interface StageRect {
  readonly x: number;
  readonly y: number;
  readonly size: number;
}

/** The outer ring's diameter in world units (rings reach radius 1.75). */
export const ORB_EXTENT = 3.5;
/** How far the orb's camera sits from it. */
export const ORB_DISTANCE = 4.2;
/** Eased to a new place over about 300 ms. */
const RATE = 0.02;

export function placeOrb(camera: PerspectiveCamera, rect: StageRect, w: number, h: number): void {
  const view = 2 * ORB_DISTANCE * Math.tan(((camera.fov / 2) * Math.PI) / 180);
  camera.aspect = w / h;
  camera.zoom = Math.max(0.01, (rect.size * view) / (ORB_EXTENT * h));
  camera.updateProjectionMatrix();
  const e = camera.projectionMatrix.elements;
  e[8] = -((2 * rect.x) / w - 1);
  e[9] = -(1 - (2 * rect.y) / h);
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
}

export function easeRect(from: StageRect, to: StageRect, dt: number, jump: boolean): StageRect {
  if (jump) return to;
  return {
    x: approach(from.x, to.x, RATE, dt),
    y: approach(from.y, to.y, RATE, dt),
    size: approach(from.size, to.size, RATE, dt),
  };
}
```

Read `easing.ts` to confirm `approach(value, target, rate, dt)`'s meaning of `rate` (time constant or per-frame share) and choose `RATE` so the move is visually done in about 300 ms; the test only asserts it is done within a second. If `elements[8]` already holds a non-zero value after `updateProjectionMatrix` (it does not for a symmetric frustum), add to it instead of assigning.

- [ ] **Step 4: Wire into `index.ts`**

- Export `StageRect` and add to the `Galaxy` interface:

```ts
  /** Place the orb: centre and diameter in CSS pixels. Eases there unless reduced
   * motion is on or `animate` is false. Without a call it stays centred. */
  setStage(rect: StageRect, opts?: { animate?: boolean }): void;
```

- State: `let stageTarget: StageRect | null = null; let stage: StageRect | null = null; let stageJump = false;`
- In `resize()`, after the existing aspect and zoom lines: `if (stage) placeOrb(orbCamera, stage, w, h);` (keep the current behaviour when `stage` is null).
- In `frame()`, before rendering: if `stageTarget`, `stage = easeRect(stage ?? stageTarget, stageTarget, dt, stageJump || reduced)`, then `stageJump = false` and `placeOrb(orbCamera, stage, canvas.clientWidth || 1, canvas.clientHeight || 1)`. Skip `placeOrb` when `stage` equals `stageTarget` and nothing resized, to avoid needless matrix work (keep a `dirty` flag set by `resize` and `setStage`).
- `setStage(rect, opts)`: ignore after `destroy`; `stageTarget = rect; if (opts?.animate === false) stageJump = true; dirty = true`.

- [ ] **Step 5: Demo controls**

In the Galaxy demo, add three buttons: "Centre", "Left panel" (centre in the right 70% of the window), "Docked" (96 px, 24 px from the bottom-right). Each calls `galaxy.setStage(...)`. Read the demo's existing control style first.

- [ ] **Step 6: Run tests, types, build**

Run: `pnpm -C packages/galaxy test && pnpm -C packages/galaxy typecheck && pnpm -C packages/galaxy build`
Expected: PASS. If the package has a visual test (`visual/orb.spec.ts`), do not change its snapshots in this task.

- [ ] **Step 7: Commit**

```bash
git add packages/galaxy
git commit -s -m "Galaxy: setStage places and sizes the orb (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Web pure helpers: layout, threads, view

**Files:**
- Create: `apps/web/src/layout.ts`, `apps/web/test/layout.test.ts`
- Create: `apps/web/src/threads.ts` (pure part only in this task), `apps/web/test/threads.test.ts`
- Modify: `apps/web/src/view.ts`, `apps/web/test/view.test.ts`

- [ ] **Step 1: Failing tests for `layout.ts`**

```ts
import { describe, expect, it } from 'vitest';
import { DOCK, PHONE_MAX, orbRect, isPhone } from '../src/layout';

describe('orbRect', () => {
  it('centres the orb right of the sidebar, as large as the short side', () => {
    expect(orbRect({ w: 1400, h: 900, sidebar: 360, workspace: false, phone: false }))
      .toEqual({ x: 360 + 1040 / 2, y: 450, size: 900 });
  });

  it('uses the whole window when the sidebar is hidden', () => {
    expect(orbRect({ w: 1400, h: 900, sidebar: 0, workspace: false, phone: false }))
      .toEqual({ x: 700, y: 450, size: 900 });
  });

  it('docks bottom-right while the workspace is open', () => {
    expect(orbRect({ w: 1400, h: 900, sidebar: 360, workspace: true, phone: false }))
      .toEqual({ x: 1400 - DOCK.inset - DOCK.size / 2, y: 900 - DOCK.inset - DOCK.size / 2, size: DOCK.size });
  });

  it('on a phone, docks top centre and otherwise fills the width', () => {
    expect(orbRect({ w: 390, h: 844, sidebar: 0, workspace: true, phone: true }))
      .toEqual({ x: 195, y: DOCK.phoneInset + DOCK.phoneSize / 2, size: DOCK.phoneSize });
    expect(orbRect({ w: 390, h: 844, sidebar: 0, workspace: false, phone: true }).size).toBe(390);
  });

  it('knows a phone', () => {
    expect(isPhone(PHONE_MAX - 1)).toBe(true);
    expect(isPhone(PHONE_MAX)).toBe(false);
  });
});
```

- [ ] **Step 2: Implement `layout.ts`**

```ts
/**
 * Where the orb goes, as a pure function of the window and what is open, so every
 * arrangement can be tested without a browser. Sizes are CSS pixels.
 */
import type { StageRect } from '@hellotrillion/galaxy';

export const PHONE_MAX = 768;
export const SIDEBAR = { min: 300, max: 520, initial: 360 } as const;
export const DOCK = { size: 96, inset: 24, phoneSize: 64, phoneInset: 12 } as const;

export interface Frame {
  readonly w: number;
  readonly h: number;
  /** The sidebar's width, 0 when hidden or on a phone. */
  readonly sidebar: number;
  readonly workspace: boolean;
  readonly phone: boolean;
}

export const isPhone = (width: number): boolean => width < PHONE_MAX;

export function orbRect(f: Frame): StageRect {
  if (f.workspace) {
    return f.phone
      ? { x: f.w / 2, y: DOCK.phoneInset + DOCK.phoneSize / 2, size: DOCK.phoneSize }
      : { x: f.w - DOCK.inset - DOCK.size / 2, y: f.h - DOCK.inset - DOCK.size / 2, size: DOCK.size };
  }
  const left = f.phone ? 0 : f.sidebar;
  const w = f.w - left;
  return { x: left + w / 2, y: f.h / 2, size: Math.min(w, f.h) };
}

export const clampWidth = (px: number): number => Math.round(Math.min(SIDEBAR.max, Math.max(SIDEBAR.min, px)));
```

Confirm `StageRect` is exported from `@hellotrillion/galaxy` (Task 5). Add a test for `clampWidth`.

- [ ] **Step 3: Failing tests for the pure part of `threads.ts`**

```ts
import { describe, expect, it } from 'vitest';
import { groupByDay, type Thread } from '../src/threads';

const day = (iso: string): number => new Date(iso).getTime();
const t = (id: string, at: string): Thread => ({ id, title: id, lastMessageAt: day(at) });

describe('groupByDay', () => {
  const now = day('2026-10-24T15:00:00');
  it('sorts threads into today, yesterday, the last week and older', () => {
    const groups = groupByDay(
      [t('a', '2026-10-24T09:00:00'), t('b', '2026-10-23T23:59:00'), t('c', '2026-10-20T12:00:00'), t('d', '2026-09-01T12:00:00')],
      now,
    );
    expect(groups.map((g) => [g.label, g.threads.map((x) => x.id)])).toEqual([
      ['Today', ['a']], ['Yesterday', ['b']], ['Previous 7 days', ['c']], ['Older', ['d']],
    ]);
  });

  it('leaves out empty groups', () => {
    expect(groupByDay([t('a', '2026-10-24T01:00:00')], now).map((g) => g.label)).toEqual(['Today']);
  });

  it('uses local midnight', () => {
    expect(groupByDay([t('a', '2026-10-24T00:00:01')], day('2026-10-24T23:59:00'))[0]?.label).toBe('Today');
  });
});
```

vitest runs in the machine's timezone; `new Date('2026-10-24T09:00:00')` (no zone) is local, so these hold anywhere. Check `apps/web/vitest.config.ts` for a pinned `TZ`.

- [ ] **Step 4: Implement the pure part of `threads.ts`**

```ts
/**
 * The thread list (SUR-1). The pure part, grouping by day, is tested on its own;
 * the DOM part below it only draws and calls the API.
 */

export interface Thread {
  readonly id: string;
  readonly title: string | null;
  readonly lastMessageAt: number;
}

export interface Group {
  readonly label: string;
  readonly threads: readonly Thread[];
}

const DAY = 86_400_000;

function midnight(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function groupByDay(threads: readonly Thread[], now: number): Group[] {
  const today = midnight(now);
  const yesterday = midnight(today - 1);
  const week = today - 7 * DAY;
  const buckets: [string, Thread[]][] = [['Today', []], ['Yesterday', []], ['Previous 7 days', []], ['Older', []]];
  for (const t of threads) {
    const i = t.lastMessageAt >= today ? 0 : t.lastMessageAt >= yesterday ? 1 : t.lastMessageAt >= week ? 2 : 3;
    buckets[i]![1].push(t);
  }
  return buckets.filter(([, list]) => list.length > 0).map(([label, list]) => ({ label, threads: list }));
}

/** What a row shows when a conversation has no title yet. */
export const untitled = (t: Thread): string => t.title?.trim() || 'New conversation';
```

- [ ] **Step 5: View: title and deletion**

Failing tests in `apps/web/test/view.test.ts`:

```ts
  it('keeps the conversation title and follows a new one', () => {
    let v = feed([{ type: 'session', id: 'c1', agent_name: 'Nova', model: null, title: null }]);
    expect(v.title).toBeNull();
    v = feed([{ type: 'conversation.title', id: 'c1', title: 'The trip' }], v);
    expect(v.title).toBe('The trip');
    v = feed([{ type: 'conversation.title', id: 'other', title: 'Not ours' }], v);
    expect(v.title).toBe('The trip');
  });
```

In `view.ts`: add `readonly title: string | null;` to `View`, `title: null` in `INITIAL`, set `title: typeof msg['title'] === 'string' ? msg['title'] : null` in `case 'session'`, and add:

```ts
    case 'conversation.title':
      return str(msg['id']) === view.conversationId ? { ...view, title: str(msg['title']) || null } : view;
```

`error{code:'deleted'}` needs no view state: `main.ts` handles it (Task 8).

- [ ] **Step 6: Run tests and types**

Run: `pnpm -C apps/web test && pnpm -C apps/web typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/layout.ts apps/web/src/threads.ts apps/web/src/view.ts apps/web/test
git commit -s -m "Web: orb placement, thread grouping, conversation title (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The shell: sidebar, stage and workspace (desktop)

**Files:**
- Modify: `apps/web/index.html`, `apps/web/src/app.css`, `apps/web/src/main.ts`
- Create: `apps/web/src/sidebar.ts`, `apps/web/src/workspace.ts`

This task moves existing elements; it adds no thread list yet (Task 9) and Settings stays as it is (Task 8). Behaviour must be unchanged except for where things are.

- [ ] **Step 1: New `index.html` body**

```html
  <body>
    <canvas id="galaxy" aria-hidden="true"></canvas>
    <aside class="sidebar" id="sidebar" aria-label="Conversation">
      <header class="side-head">
        <button type="button" class="icon-btn" id="hide-sidebar" aria-label="Hide sidebar" title="Hide sidebar (⌘\)"></button>
        <strong class="side-name" id="agent-name">Trillion</strong>
        <span class="status" id="status" role="status" aria-live="polite">Connecting</span>
        <span class="side-grow"></span>
        <button type="button" class="usage-pill" id="usage" hidden></button>
        <button type="button" class="icon-btn" id="open-settings" aria-haspopup="dialog" aria-label="Settings" hidden></button>
      </header>
      <div class="side-title">
        <button type="button" class="thread-switch" id="thread-switch" aria-haspopup="listbox" aria-expanded="false">
          <span id="thread-title">New conversation</span>
        </button>
        <button type="button" class="icon-btn" id="new" aria-label="New conversation" title="New conversation" hidden></button>
      </div>
      <div class="side-body">
        <div class="banner" id="banner" role="alert" hidden></div>
        <section class="transcript" id="transcript" role="log" aria-label="Messages" aria-live="polite" tabindex="0"></section>
        <div class="threads" id="threads" hidden></div>
      </div>
      <div class="choices" id="choices" role="group" aria-label="Quick replies" hidden></div>
      <form class="ask" id="ask">
        <label class="visually-hidden" for="input">Message</label>
        <textarea id="input" rows="1" placeholder="Say something" autocomplete="off" disabled></textarea>
        <button type="button" id="mic" aria-pressed="false" hidden>Speak</button>
        <button type="submit" id="send" disabled>Send</button>
        <button type="button" id="stop" hidden>Stop</button>
      </form>
      <p class="foot" id="foot"></p>
      <div class="side-resize" id="side-resize" role="separator" aria-orientation="vertical" aria-label="Resize sidebar" tabindex="0"></div>
    </aside>
    <button type="button" class="icon-btn show-sidebar" id="show-sidebar" aria-label="Show sidebar" title="Show sidebar (⌘\)" hidden></button>
    <div class="captions" id="captions" aria-hidden="true" hidden></div>
    <section class="workspace" id="workspace" role="region" aria-labelledby="workspace-title" hidden>
      <header class="workspace-bar">
        <h2 id="workspace-title"></h2>
        <button type="button" class="icon-btn" id="workspace-close" aria-label="Close" title="Close (Esc)"></button>
      </header>
      <div class="workspace-body" id="workspace-body"></div>
    </section>
    <button type="button" class="orb-dock" id="orb-dock" aria-label="Close and return to the orb" hidden></button>
    <dialog id="settings-panel" class="panel"></dialog>
    <script type="module" src="./src/main.ts"></script>
  </body>
```

Keep every existing id that `main.ts` looks up (`status`, `banner`, `transcript`, `ask`, `input`, `send`, `stop`, `foot`, `new`, `usage`, `mic`, `open-settings`, `choices`, `settings-panel`). `new` becomes an icon button: give it `icon('edit', 18)` in `main.ts` (check `icons.ts` for available names; add an `edit` and `sidebar` icon in the file's existing style if missing). Where `main.ts` sets the brand name, point it at `#agent-name`.

- [ ] **Step 2: CSS**

In `app.css`:
- Remove the old `.top` header rules and change `.stage` to nothing (delete the selector, since `<main class="stage">` is gone). Keep `.transcript`, `.msg`, `.ask`, `.choices`, `.foot`, `.banner` rules; remove `.transcript`'s `max-height: 44vh` and the fade mask, since the sidebar scrolls the full height.
- Add (use the existing tokens from `packages/galaxy/tokens.css`: `--color-surface-glass`, `--color-border`, `--color-text-2`, `--radius-lg`, etc.; read the top of `app.css` for the names in use):

```css
:root {
  --sidebar-w: 360px;
}

.sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  width: var(--sidebar-w);
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 12px 14px;
  background: var(--color-surface-glass);
  border-right: 1px solid var(--color-border);
  backdrop-filter: blur(16px);
  z-index: 2;
  transition: transform 300ms ease;
}
.sidebar[data-hidden] {
  transform: translateX(-100%);
}
@media (prefers-reduced-motion: reduce) {
  .sidebar { transition: none; }
}
.side-head,
.side-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.side-grow { flex: 1; }
.side-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.side-body .transcript,
.side-body .threads {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.thread-switch {
  flex: 1;
  min-width: 0;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-resize {
  position: absolute;
  inset: 0 -4px 0 auto;
  width: 8px;
  cursor: col-resize;
}
.show-sidebar {
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: 2;
}
.workspace {
  position: fixed;
  inset: 16px 16px 16px calc(var(--sidebar-w) + 16px);
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  background: var(--color-surface-glass);
  backdrop-filter: blur(16px);
  z-index: 1;
  overflow: hidden;
}
body[data-sidebar='hidden'] .workspace { left: 16px; }
.workspace-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border);
}
.workspace-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.orb-dock {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: 96px;
  height: 96px;
  border-radius: 50%;
  background: transparent;
  border: 0;
  z-index: 3;
  cursor: pointer;
}
```

`.orb-dock` is an invisible button over the docked orb (the orb itself is drawn on the canvas). Give `.icon-btn` a style matching `#open-settings`'s current one, and move `#open-settings`'s rules onto `.icon-btn`.

- [ ] **Step 3: `workspace.ts`**

```ts
/**
 * The workspace (SUR-1): one thing at a time opens over the stage, and the orb docks
 * small while it is open. It stays the orb: every state and reaction, just smaller.
 */
import { orbRect, isPhone } from './layout';

export interface WorkspaceDeps {
  readonly region: HTMLElement;
  readonly title: HTMLElement;
  readonly body: HTMLElement;
  readonly close: HTMLButtonElement;
  readonly dock: HTMLButtonElement;
  /** The sidebar's current width in CSS px, 0 when hidden. */
  readonly sidebarWidth: () => number;
  readonly place: (rect: { x: number; y: number; size: number }, animate: boolean) => void;
}

export interface Workspace {
  /** Show `content` under `title`. Returns false if something else is open. */
  open(title: string, content: HTMLElement): void;
  close(): void;
  readonly isOpen: boolean;
  /** Re-place the orb: the window, the sidebar or the workspace changed. */
  layout(animate?: boolean): void;
  onClose(listener: () => void): void;
}

export function createWorkspace(d: WorkspaceDeps): Workspace {
  const listeners: (() => void)[] = [];
  let back: HTMLElement | null = null;

  function layout(animate = true): void {
    const w = innerWidth;
    d.place(
      orbRect({ w, h: innerHeight, sidebar: d.sidebarWidth(), workspace: !d.region.hidden, phone: isPhone(w) }),
      animate,
    );
  }

  function close(): void {
    if (d.region.hidden) return;
    d.region.hidden = true;
    d.dock.hidden = true;
    d.body.replaceChildren();
    layout();
    for (const l of listeners) l();
    back?.focus();
    back = null;
  }

  d.close.addEventListener('click', close);
  d.dock.addEventListener('click', close);
  addEventListener('resize', () => layout(false));

  return {
    open(title, content) {
      if (d.region.hidden) back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      d.title.textContent = title;
      d.body.replaceChildren(content);
      d.region.hidden = false;
      d.dock.hidden = false;
      layout();
    },
    close,
    get isOpen() {
      return !d.region.hidden;
    },
    layout,
    onClose(listener) {
      listeners.push(listener);
    },
  };
}
```

The phone dock position differs (top centre); position `.orb-dock` with a phone media query in Task 11.

- [ ] **Step 4: `sidebar.ts`**

```ts
/**
 * The sidebar (SUR-1): the conversation beside the orb. It can be dragged wider and
 * hidden; both are remembered per device.
 */
import { SIDEBAR, clampWidth, isPhone } from './layout';

const WIDTH_KEY = 'trillion-sidebar-width';
const HIDDEN_KEY = 'trillion-sidebar-hidden';

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Blocked storage: the page simply forgets.
  }
}

export interface Sidebar {
  /** Its width in CSS px; 0 while hidden or on a phone. */
  width(): number;
  readonly hidden: boolean;
  setHidden(hidden: boolean): void;
  toggle(): void;
  onChange(listener: () => void): void;
}

export function createSidebar(aside: HTMLElement, handle: HTMLElement, show: HTMLButtonElement, hide: HTMLButtonElement): Sidebar {
  const listeners: (() => void)[] = [];
  let px = clampWidth(Number(read(WIDTH_KEY)) || SIDEBAR.initial);
  let hidden = read(HIDDEN_KEY) === '1';
  const changed = (): void => listeners.forEach((l) => l());

  function apply(): void {
    document.documentElement.style.setProperty('--sidebar-w', `${px}px`);
    aside.toggleAttribute('data-hidden', hidden);
    aside.inert = hidden;
    document.body.dataset['sidebar'] = hidden ? 'hidden' : 'shown';
    show.hidden = !hidden;
  }

  function setHidden(next: boolean): void {
    if (next === hidden) return;
    hidden = next;
    write(HIDDEN_KEY, hidden ? '1' : null);
    apply();
    changed();
    (hidden ? show : hide).focus();
  }

  handle.addEventListener('pointerdown', (ev) => {
    handle.setPointerCapture(ev.pointerId);
    const move = (e: PointerEvent): void => {
      px = clampWidth(e.clientX);
      apply();
      changed();
    };
    const up = (): void => {
      handle.removeEventListener('pointermove', move);
      write(WIDTH_KEY, String(px));
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up, { once: true });
  });
  handle.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
    ev.preventDefault();
    px = clampWidth(px + (ev.key === 'ArrowRight' ? 16 : -16));
    write(WIDTH_KEY, String(px));
    apply();
    changed();
  });
  show.addEventListener('click', () => setHidden(false));
  hide.addEventListener('click', () => setHidden(true));
  apply();

  return {
    width: () => (hidden || isPhone(innerWidth) ? 0 : px),
    get hidden() {
      return hidden;
    },
    setHidden,
    toggle: () => setHidden(!hidden),
    onChange: (l) => listeners.push(l),
  };
}
```

The sidebar's transform transition is 300 ms; the orb's ease matches it, so they move together.

- [ ] **Step 5: Wire in `main.ts`**

- Look up the new elements; create `sidebar` and `workspace`:

```ts
const sidebar = createSidebar($('sidebar'), $('side-resize'), $<HTMLButtonElement>('show-sidebar'), $<HTMLButtonElement>('hide-sidebar'));
const workspace = createWorkspace({
  region: $('workspace'),
  title: $('workspace-title'),
  body: $('workspace-body'),
  close: $<HTMLButtonElement>('workspace-close'),
  dock: $<HTMLButtonElement>('orb-dock'),
  sidebarWidth: () => sidebar.width(),
  place: (rect, animate) => galaxy.setStage(rect, { animate }),
});
sidebar.onChange(() => workspace.layout());
workspace.layout(false);
```

- Icons on `hide-sidebar`, `show-sidebar`, `workspace-close`, `new`.
- Keyboard (in the existing global `keydown`): `⌘\` / `Ctrl+\` toggles the sidebar (`ev.key === '\\' && primary(ev)`). Escape closes the workspace when it is open and nothing is speaking or listening (keep the existing interrupt-first rule: if `view.busy || voice.active || view.mic`, Escape still interrupts and does nothing else).
- In `render()`, set `#agent-name` from `view.agentName` and `#thread-title` from `view.title ?? 'New conversation'`.

- [ ] **Step 6: Verify in the browser** (no sound: before driving the page, turn Voice off in Settings or stub audio)

Start or reuse the preview (`preview_start` name `trillion`, port 7790). Check at 1400×900: the sidebar on the left with messages and the ask bar; the orb centred in the right area; drag the edge; ⌘\ hides it and the orb glides to the centre; the conversation still works by typing (Ollama `gemma4:e4b` is free). Read the console for errors. Take a screenshot.

- [ ] **Step 7: Tests, types, build**

Run: `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web
git commit -s -m "Web: the conversation moves to a sidebar; the orb takes the stage (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Settings opens in the workspace

**Files:**
- Modify: `apps/web/src/panel.ts`, `apps/web/src/main.ts`, `apps/web/src/app.css`, `apps/web/index.html` (remove `<dialog id="settings-panel">`)
- Test: `apps/web/test/settings.test.ts` (adjust if it builds a panel)

Background: `createPanel(dialog)` builds a rail and a main column inside a `<dialog>` and uses `dialog.open`, `dialog.showModal()`, `dialog.close()` and the `close` event. Section modules only see a `Host`.

- [ ] **Step 1: A `Surface` instead of a dialog**

In `panel.ts`:

```ts
/** Where Settings is drawn. The workspace provides one; so would a dialog. */
export interface Surface {
  readonly open: boolean;
  /** Show `content` (the rail and the section column). */
  show(content: HTMLElement): void;
  close(): void;
  onClose(listener: () => void): void;
}
```

Change `createPanel(dialog: HTMLDialogElement)` to `createPanel(surface: Surface)`:
- Build `rail` and `main` inside one `const root = el('div', 'panel')` instead of `dialog.replaceChildren(rail, main)`; set `root.setAttribute('aria-label', 'Settings')`.
- `shut` click → `surface.close()`. Remove the close button's `Esc` text only if the workspace bar already shows a close button; keep one close button: drop the panel's own `panel-bar` close button and rely on the workspace's (the crumb stays).
- In `show(id)`: replace `if (!dialog.open) { dialog.showModal(); links.get(id)?.focus(); }` with `if (!surface.open) { surface.show(root); links.get(id)?.focus(); }`.
- `dialog.addEventListener('close', …)` → `surface.onClose(…)`.
- `Host.open` → `surface.open && current === id`; `Host.close` → `surface.close()`; `Host.addEventListener('close', l)` → `surface.onClose(l)`.
- `isOpen` → `surface.open`.

- [ ] **Step 2: The workspace as a `Surface`** in `main.ts`

```ts
const settingsSurface: Surface = {
  get open() {
    return workspace.isOpen && workspace.showing === 'settings';
  },
  show: (content) => workspace.open('Settings', content, 'settings'),
  close: () => workspace.close(),
  onClose: (l) => workspace.onClose(l),
};
const panel = createPanel(settingsSurface);
```

Add an optional `id` argument to `workspace.open(title, content, id?)` and a `showing: string | null` getter in `workspace.ts`, so a later workspace view can tell Settings from itself. Opening a different id replaces what is shown and fires the close listeners of the one replaced (call listeners before replacing).

- [ ] **Step 3: CSS**

`.panel` was a full-window dialog with `grid-template-columns: 280px 1fr`. Inside the workspace make it `display: grid; grid-template-columns: 240px 1fr; height: 100%;` and drop dialog-only rules (`::backdrop`, fixed sizing, open animation). Read the existing `.panel` block (around `app.css:600-660`) and keep everything that styles the rail and sections.

- [ ] **Step 4: Remove `<dialog id="settings-panel">`** from `index.html` and its lookup in `main.ts`.

- [ ] **Step 5: Check every way into Settings**

Search `main.ts` and `onboarding.ts` for `panel.open`, `.open()` on a settings module, `openSettings`, and `showModal`; each must still land on the right section, now in the workspace. Escape order: interrupting her first, then closing the workspace.

- [ ] **Step 6: Tests, types, build, browser** (no sound)

Run: `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`
In the preview: open Settings from the gear and with ⌘,; the orb docks bottom-right and keeps its state colour; type a message while Settings is open and see the reply in the sidebar; click the docked orb to close; switch sections; search settings. Screenshot with Settings open.

- [ ] **Step 7: Commit**

```bash
git add apps/web
git commit -s -m "Web: Settings opens in the workspace and the orb docks (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The thread list

**Files:**
- Modify: `apps/web/src/threads.ts` (DOM part), `apps/web/src/main.ts`, `apps/web/src/app.css`
- Test: `apps/web/test/threads.test.ts` (pure additions)

- [ ] **Step 1: Pure additions with tests**

Add and test:

```ts
/** Rows from the API. Anything malformed is dropped rather than shown. */
export function threadsOf(body: unknown): { threads: Thread[]; more: boolean } {
  const b = body as { conversations?: unknown; more?: unknown } | null;
  const rows = Array.isArray(b?.conversations) ? b.conversations : [];
  const threads = rows.flatMap((r: Record<string, unknown>) =>
    typeof r?.['id'] === 'string' && typeof r['last_message_at'] === 'number'
      ? [{ id: r['id'], title: typeof r['title'] === 'string' ? r['title'] : null, lastMessageAt: r['last_message_at'] }]
      : [],
  );
  return { threads, more: b?.more === true };
}

/** A title change arriving over the socket, applied to the rows shown. */
export const retitle = (threads: readonly Thread[], id: string, title: string): Thread[] =>
  threads.map((t) => (t.id === id ? { ...t, title } : t));
```

Tests: malformed rows dropped; `more` defaults false; `retitle` changes only the matching row.

- [ ] **Step 2: The DOM part**

```ts
export interface ThreadsDeps {
  readonly root: HTMLElement;
  /** GET /api/conversations with these params; resolves to the parsed JSON. */
  readonly fetchPage: (params: { q: string; before: { at: number; id: string } | null }) => Promise<unknown>;
  readonly rename: (id: string, title: string) => Promise<boolean>;
  readonly remove: (id: string) => Promise<boolean>;
  readonly restore: (id: string) => Promise<boolean>;
  readonly current: () => string | null;
  readonly pick: (id: string) => void;
  /** The open conversation was deleted. */
  readonly deletedCurrent: () => void;
  readonly now: () => number;
}

export interface ThreadList {
  open(): void;
  close(): void;
  readonly isOpen: boolean;
  retitle(id: string, title: string): void;
  onToggle(listener: (open: boolean) => void): void;
}
```

Build in `createThreads(d: ThreadsDeps): ThreadList`:
- A search `input` (type `search`, placeholder "Search conversations", `aria-label`), a `ul` with `role="listbox"`, `aria-label="Conversations"`, a status line `p` for "Loading…", "No conversations match", "Couldn't load your conversations." with a "Retry" button, and a toast element for "Conversation deleted" + "Undo".
- `open()`: show `root`, load page one (`q` from the box, `before: null`), focus the search box, fire listeners. `close()`: hide, fire listeners.
- Search: debounce 200 ms, then reload page one. Stale responses (an older request finishing after a newer one) are ignored: keep a counter.
- Rendering: `groupByDay(threads, d.now())`; for each group an `li` with `role="presentation"` holding the label, then rows as `li role="option"` with `aria-selected` for the current conversation, `tabindex="-1"` except the active one. Title via `textContent` only (untitled → `untitled(t)`); time via `format.ts` if it has a short time helper (read it), else `toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})` for today and `toLocaleDateString` otherwise.
- Keys in the list: ArrowUp/ArrowDown move the active row; Enter picks; Escape closes. Click picks. Picking calls `d.pick(id)` then `close()`.
- Row menu: a `⋯` button per row (`aria-label="More for <title>"`) and `contextmenu` on the row, opening a small menu with Rename and Delete.
  - Rename: replace the title span with an `input` holding the title; Enter calls `d.rename`; Escape or blur cancels. On failure, restore and show "Couldn't rename it" in the status line. On success update the row.
  - Delete: remove the row, call `d.remove`; on failure put it back with "Couldn't delete it". On success show the toast for 8 s with Undo → `d.restore` and reload. If the row was the current conversation, call `d.deletedCurrent()`.
- Paging: when the last row scrolls into view (an `IntersectionObserver` on a sentinel `li`) and `more`, load the next page with `before` = the last row's `{ at: lastMessageAt, id }` (sent as `before` and `before_id`) and append.

- [ ] **Step 3: Wire in `main.ts`**

- `api` helpers for the calls (there is an existing `api` object used by settings modules; read its shape and reuse it for GET/PATCH/DELETE/POST with the token).
- `#thread-switch` click toggles the list; `aria-expanded` follows `onToggle`. While open, hide `#transcript` and show `#threads`; while closed, the reverse.
- `pick(id)`: if it is the current conversation, just close. Otherwise run the same steps as the New button but store `id` instead of `null`: stop her first (`interrupt()` if busy, speaking or listening), `store(CONVERSATION_KEY, id)`, clear `shown`, reset view to `{ ...INITIAL, link: 'connecting' }`, close the socket and `connect()`. Factor the New button's body into `switchTo(id: string | null)` and use it for both.
- `deletedCurrent()` and the socket's `error{code:'deleted'}` both call `switchTo(null)`.
- `conversation.title` messages: `threads.retitle(id, title)` (besides the view reducer).
- Shortcut: `⌘⇧K` / `Ctrl+Shift+K` toggles the thread list (opening the sidebar first if hidden). Plain ⌘K still focuses the ask bar; check the order of the existing `k` handler so ⌘⇧K does not also focus the input.
- When the open conversation's first title arrives or it is renamed, `#thread-title` follows `view.title`.

- [ ] **Step 4: CSS** for `.threads`, its search box (reuse `.panel-search`'s look), group labels (reuse `.panel-group-label`), rows (reuse `.panel-link` hover and `aria-current` styles but on `[aria-selected='true']`), the row menu, and the toast (bottom of the sidebar, above the ask bar).

- [ ] **Step 5: Tests, types, build, browser** (no sound)

Run: `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`
In the preview, with the Ollama model: make three conversations by typing; see each get her title after the first reply; open the list with the title and with ⌘⇧K; switch between them and see each history load; search a word from one reply; rename one; delete one and Undo; delete the open one and see a new conversation start; reload and see the list persist. Screenshot the open list.

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -s -m "Web: the thread list: switch, search, rename, delete with undo (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Captions while the sidebar is hidden, and cards open it

**Files:**
- Create: `apps/web/src/captions.ts`, `apps/web/test/captions.test.ts`
- Modify: `apps/web/src/main.ts`, `apps/web/src/app.css`

- [ ] **Step 1: Pure part with tests**

```ts
/** The caption lines for the current view: what she is saying, or what she hears. */
export function captionOf(view: Pick<View, 'entries' | 'hearing' | 'replyOpen' | 'orb'>): string {
  if (view.hearing) return view.hearing;
  const last = view.entries[view.entries.length - 1];
  if (last?.kind === 'assistant' && (view.replyOpen || view.orb === 'speaking')) return last.text;
  return '';
}

/** The tail of a long line, so two lines show what is being said now. */
export const tail = (text: string, max = 160): string =>
  text.length <= max ? text : `…${text.slice(text.length - max + 1).replace(/^\S*\s/, '')}`;
```

Tests: hearing wins; her open reply shows; a finished reply with the orb idle shows nothing; `tail` keeps short text and trims long text at a word.

- [ ] **Step 2: DOM part**

`createCaptions(el: HTMLElement)` returns `update(text: string, sidebarHidden: boolean)`: hidden when the sidebar is shown or text is empty; otherwise sets `textContent = tail(text)`, shows it, and hides it 4 s after the last change. `aria-hidden="true"` stays: the transcript's live region already announces everything (SUR-6), and captions would double it.

- [ ] **Step 3: Wire in `main.ts`**: call `captions.update(captionOf(view), sidebar.hidden)` in `render()`. When a `confirm.request` arrives and the sidebar is hidden, `sidebar.setHidden(false)`.

CSS: `.captions` fixed, centred under the orb's resting place (bottom 12% of the stage), max two lines (`-webkit-line-clamp: 2`), the existing subtitle-like text style, with a 200 ms fade (none with reduced motion).

- [ ] **Step 4: Tests, types, build, browser** (no sound: verify captions by typing with Voice off; her reply streams, `replyOpen` drives the caption)

Run: `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`
Screenshot with the sidebar hidden and a caption showing.

- [ ] **Step 5: Commit**

```bash
git add apps/web
git commit -s -m "Web: captions under the orb while the sidebar is hidden (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Phones: the bottom sheet

**Files:**
- Modify: `apps/web/src/sidebar.ts`, `apps/web/src/app.css`, `apps/web/src/main.ts`
- Test: `apps/web/test/layout.test.ts`

- [ ] **Step 1: Pure rests with tests** (in `layout.ts`)

```ts
export type Rest = 'collapsed' | 'half' | 'full';

/** The sheet's top edge in px from the top of a window `h` tall, for each rest.
 * Collapsed leaves the ask bar showing (its height `ask`). */
export function sheetTop(rest: Rest, h: number, ask: number): number {
  return rest === 'full' ? 48 : rest === 'half' ? Math.round(h / 2) : h - ask;
}

/** Where a drag that ended at `top` settles: the nearest rest. */
export function settle(top: number, h: number, ask: number): Rest {
  const rests: Rest[] = ['full', 'half', 'collapsed'];
  return rests.reduce((best, r) => (Math.abs(sheetTop(r, h, ask) - top) < Math.abs(sheetTop(best, h, ask) - top) ? r : best));
}
```

Tests for each rest and for settling between them.

- [ ] **Step 2: CSS under `@media (max-width: 767px)`**

- `.sidebar`: `inset: auto 0 0 0; width: auto; height: 100dvh; transform: translateY(var(--sheet-top)); border-right: 0; border-top: 1px solid var(--color-border); border-radius: 16px 16px 0 0;` with a visible drag handle (`.side-resize` becomes a 36×4 bar centred at the top, `cursor: grab`).
- `.workspace`: `inset: 0; border-radius: 0;` with its bar leaving room for the docked orb (top padding 88px).
- `.orb-dock`: `top: 12px; left: 50%; right: auto; bottom: auto; width: 64px; height: 64px; transform: translateX(-50%);`
- `.show-sidebar` and the hide button: hidden on phones (the sheet is never fully hidden).

- [ ] **Step 3: Behaviour in `sidebar.ts`**

On phones the resize handle drags the sheet vertically: pointer moves set `--sheet-top` directly; on release `settle()` picks the rest and sets `--sheet-top` to `sheetTop(rest)`. Tapping `#thread-switch` while collapsed goes to `full`. The ask bar's height comes from `form.offsetHeight`. Remember the last rest per device (`trillion-sheet-rest`). Window resize across 768 px switches modes cleanly (re-apply, then `changed()` so the orb re-places).

- [ ] **Step 4: Tests, types, build, browser** at 390×844 (`resize_window` preset `mobile`), no sound

Check the three rests by dragging; open Settings and see it full-screen with the orb docked top-centre; reset the viewport with preset `desktop` after. Screenshots of collapsed and half.

- [ ] **Step 5: Commit**

```bash
git add apps/web
git commit -s -m "Web: the sidebar becomes a bottom sheet on phones (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Accessibility pass, docs, and the live run

**Files:**
- Modify: `docs/PRD.md` (SUR-1 note), `docs/architecture.md` (web app section), `docs/TESTING.md` (a manual check), `apps/web/src/*` for fixes found

- [ ] **Step 1: Keyboard and screen reader check** in the preview (no sound)

Tab order: sidebar head → title → new → messages → ask bar → workspace when open. The thread list works with arrows, Enter and Escape alone. Focus moves into the workspace when it opens and back when it closes. The orb's state still reaches the live region (`aria-live` element that `onStateChange` writes to; confirm it still exists after the HTML change). Fix what fails, with a vitest test where the logic is pure.

- [ ] **Step 2: Docs**

- PRD SUR-1: add after the existing text: "The conversation sits in a left sidebar with a thread list (titles, search, rename, soft delete with undo); the orb owns the stage, and a workspace opens over it (Settings first) while the orb docks small. ⌘K is the Ask bar; ⌘⇧K the thread list; ⌘\\ hides the sidebar."
- `docs/architecture.md`: the web app's modules (`sidebar`, `threads`, `workspace`, `captions`, `layout`), the conversations API, `brain/title.py`, and Galaxy's `setStage`. Read the file and match its structure.
- `docs/TESTING.md`: a manual check for switching conversations mid-reply and for the docked orb's reactions while Settings is open.

- [ ] **Step 3: Full checks**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
python3 scripts/check_one_writer.py && python3 scripts/containment_check.py
pnpm -C packages/galaxy test && pnpm -C packages/galaxy typecheck && pnpm -C packages/galaxy build
pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build
```

Expected: all pass.

- [ ] **Step 4: Live run** (preview on 7790, Voice off, Ollama `gemma4:e4b`)

Run the spec's live list: switch between three conversations; rename and delete with undo; a new conversation gets her title after the first reply; open Settings and see the orb dock and keep reacting while typing; hide the sidebar and see captions; phone width. Save screenshots for the PR.

- [ ] **Step 5: Commit**

```bash
git add docs apps/web
git commit -s -m "Sidebar layout: docs and accessibility fixes (#ui)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
