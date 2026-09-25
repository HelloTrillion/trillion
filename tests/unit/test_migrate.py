import sqlite3

import pytest

from trillion.storage.db import connect
from trillion.storage.migrate import MigrationChecksumError, migrate


async def test_applies_once_and_records_checksum(tmp_path):
    conn = await connect(tmp_path / "t.db")
    assert await migrate(conn) == ["001_spine"]
    assert await migrate(conn) == []
    async with conn.execute("SELECT id FROM owners") as cur:
        assert [r[0] for r in await cur.fetchall()] == ["local"]
    await conn.close()


async def test_refuses_an_edited_migration(tmp_path):
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    await conn.execute("UPDATE schema_migrations SET checksum = 'x' WHERE version = '001'")
    with pytest.raises(MigrationChecksumError):
        await migrate(conn)
    await conn.close()


async def test_append_only_tables_reject_update_and_delete(tmp_path):
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    await conn.execute(
        "INSERT INTO tool_audit (owner_id, call_id, tool, args_hash, risk, decision, outcome,"
        " duration_ms, session_id, at) VALUES ('local','c','t','h','read','executed','ok',1,'s',1)"
    )
    for sql in ("UPDATE tool_audit SET tool = 'x'", "DELETE FROM tool_audit"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await conn.execute(sql)
    await conn.close()


async def test_connection_pragmas(tmp_path):
    conn = await connect(tmp_path / "t.db")
    async with conn.execute("PRAGMA journal_mode") as cur:
        assert (await cur.fetchone())[0] == "wal"
    async with conn.execute("PRAGMA foreign_keys") as cur:
        assert (await cur.fetchone())[0] == 1
    await conn.close()
