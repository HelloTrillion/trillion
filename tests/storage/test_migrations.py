"""Migration 019 (the your helpers spec) on a database made up to 018: every row kept,
and the new kind, table and column there."""

from __future__ import annotations

import shutil
import sqlite3
from importlib.resources import as_file, files
from pathlib import Path

import aiosqlite
import pytest

from trillion.helpers.store import JOB_MAX, NAME_MAX, NOTES_MAX
from trillion.storage.db import connect
from trillion.storage.migrate import migrate

TABLES = (
    "conversations",
    "messages",
    "compactions",
    "turn_timings",
    "schedules",
    "task_runs",
    "notices",
    "boards",
)


def _up_to(version: str, into: Path) -> Path:
    """The bundled migrations up to `version`, copied as they are."""
    into.mkdir()
    with as_file(files("trillion.storage") / "migrations") as src:
        for f in sorted(Path(src).glob("[0-9][0-9][0-9]_*.sql")):
            if f.name[:3] <= version:
                shutil.copy(f, into / f.name)
    return into


async def _rows(conn: aiosqlite.Connection, table: str) -> list[tuple[object, ...]]:
    async with conn.execute(f"SELECT * FROM {table} ORDER BY rowid") as cur:  # noqa: S608 - fixed names only
        return [tuple(r) for r in await cur.fetchall()]


async def test_019_keeps_every_row_of_a_018_database(tmp_path: Path) -> None:
    conn = await connect(tmp_path / "t.db")
    try:
        old = _up_to("018", tmp_path / "old")
        assert (await migrate(conn, old))[-1] == "018_attention_deferred"
        await conn.executescript(
            "INSERT INTO conversations (id, owner_id, created_at, kind, title, title_source,"
            " last_message_at) VALUES ('c1', 'local', 1, 'chat', 'Inbox', 'words', 5);"
            "INSERT INTO conversations (id, owner_id, created_at, kind, day, title)"
            " VALUES ('c2', 'local', 2, 'day', '2026-11-05', 'Your day');"
            "INSERT INTO conversations (id, owner_id, created_at, kind, title, deleted_at)"
            " VALUES ('c3', 'local', 3, 'routine', 'Invoices', 9);"
            "INSERT INTO messages (owner_id, conversation_id, role, kind, blocks_json,"
            " created_at) VALUES ('local', 'c1', 'user', 'user', '[]', 4);"
            "INSERT INTO compactions (owner_id, conversation_id, upto_message_id, summary,"
            " created_at) VALUES ('local', 'c1', 1, 'Talked about invoices.', 6);"
            "INSERT INTO turn_timings (owner_id, conversation_id, turn, via, first_in_session,"
            " provider, model, stop, tools, calls_json, created_at)"
            " VALUES ('local', 'c1', 1, 'text', 1, 'p', 'm', 'end', 0, '[]', 7);"
            "INSERT INTO schedules (id, owner_id, kind, title, body, zone, at_local, repeat,"
            " next_due_ms, status, created_ms, created_in, target, conversation_id, notes)"
            " VALUES ('s1', 'local', 'ask', 'Invoices', 'Check', 'UTC', '2026-11-05T09:00',"
            " NULL, 10, 'active', 1, 'c1', 'own', 'c3', 'Leave out refunds.');"
            "INSERT INTO task_runs (schedule_id, due_ms, status, timeout_s, created_ms)"
            " VALUES ('s1', 10, 'delivered', 60, 10);"
        )
        before = {t: await _rows(conn, t) for t in TABLES}
        assert await migrate(conn) == ["019_helpers", "020_helper_draft"]
        for t in TABLES:
            after = await _rows(conn, t)
            if t == "schedules":
                # The one new column, empty.
                assert [r[:-1] for r in after] == before[t]
                assert all(r[-1] is None for r in after)
            else:
                assert after == before[t], t
        async with conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []
        async with conn.execute("PRAGMA index_list(conversations)") as cur:
            names = {r[1] for r in await cur.fetchall()}
        assert {"conversations_by_recent", "conversations_one_day"} <= names
        # The new kind is allowed, and an unknown one still is not.
        await conn.execute(
            "INSERT INTO conversations (id, owner_id, created_at, kind)"
            " VALUES ('c4', 'local', 4, 'helper')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO conversations (id, owner_id, created_at, kind)"
                " VALUES ('c5', 'local', 5, 'other')"
            )
        await conn.execute(
            "INSERT INTO helpers (id, owner_id, name, name_key, job, conversation_id,"
            " created_at) VALUES ('h1', 'local', 'Invoices', 'invoices', 'Chases.', 'c4', 4)"
        )
        with pytest.raises(sqlite3.IntegrityError):  # one name per owner, by its key
            await conn.execute(
                "INSERT INTO helpers (id, owner_id, name, name_key, job, conversation_id,"
                " created_at) VALUES ('h2', 'local', 'INVOICES', 'invoices', 'Again.', 'c4', 4)"
            )
        await conn.execute("UPDATE schedules SET helper_id = 'h1' WHERE id = 's1'")
        with pytest.raises(sqlite3.IntegrityError):  # a helper that is not there
            await conn.execute("UPDATE schedules SET helper_id = 'nope' WHERE id = 's1'")
    finally:
        await conn.close()


async def test_019_limits_match_the_store(tmp_path: Path) -> None:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        await conn.execute(
            "INSERT INTO conversations (id, owner_id, created_at, kind)"
            " VALUES ('c1', 'local', 1, 'helper')"
        )
        n = iter(range(100))

        async def add(name: str, job: str, notes: str = "") -> None:
            i = next(n)
            await conn.execute(
                "INSERT INTO helpers (id, owner_id, name, name_key, job, notes,"
                " conversation_id, created_at) VALUES (?, 'local', ?, ?, ?, ?, 'c1', 1)",
                (f"h{i}", name, f"k{i}", job, notes),
            )

        await add("n" * NAME_MAX, "j" * JOB_MAX, "x" * NOTES_MAX)
        for name, job, notes in (
            ("n" * (NAME_MAX + 1), "j", ""),
            ("n", "j" * (JOB_MAX + 1), ""),
            ("n", "j", "x" * (NOTES_MAX + 1)),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                await add(name, job, notes)
    finally:
        await conn.close()


async def test_020_adds_the_draft_to_a_019_database(tmp_path: Path) -> None:
    """Migration 020 on a database already at 019: its helpers kept, with an empty draft."""
    conn = await connect(tmp_path / "t.db")
    try:
        old = _up_to("019", tmp_path / "old")
        assert (await migrate(conn, old))[-1] == "019_helpers"
        await conn.executescript(
            "INSERT INTO conversations (id, owner_id, created_at, kind)"
            " VALUES ('c1', 'local', 1, 'helper');"
            "INSERT INTO helpers (id, owner_id, name, name_key, job, notes, conversation_id,"
            " created_at) VALUES ('h1', 'local', 'Invoices', 'invoices', 'Chases.',"
            " 'Leave out refunds.', 'c1', 1);"
        )
        before = await _rows(conn, "helpers")
        assert await migrate(conn) == ["020_helper_draft"]
        after = await _rows(conn, "helpers")
        assert [r[:-2] for r in after] == before
        assert [r[-2:] for r in after] == [("[]", "[]")]
        await conn.execute("UPDATE helpers SET connectors = '[\"canva\"]', asks = '[]'")
        with pytest.raises(sqlite3.IntegrityError):  # an array, nothing else
            await conn.execute("UPDATE helpers SET asks = 'not json'")
    finally:
        await conn.close()
