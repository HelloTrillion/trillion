import os
import sqlite3

import pytest

from trillion.storage.db import connect
from trillion.storage.migrate import MigrationChecksumError, migrate


async def test_applies_once_and_records_checksum(tmp_path):
    conn = await connect(tmp_path / "t.db")
    assert await migrate(conn) == ["001_spine", "002_args_retention"]
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


@pytest.mark.skipif(os.name == "nt", reason="Windows uses ACLs, not POSIX modes (SUR-3c)")
async def test_the_database_is_private_to_the_user(tmp_path):
    """Pending actions hold argument values (file contents, amounts): owner-only."""
    import os
    import stat

    home = tmp_path / "home"
    home.mkdir(mode=0o755)
    old = os.umask(0o022)
    try:
        conn = await connect(home / "t.db")
    finally:
        os.umask(old)
    try:
        await migrate(conn)
        files = [home / "t.db", home / "t.db-wal", home / "t.db-shm"]
        present = [f for f in files if f.exists()]
        assert home / "t.db" in present
        for f in present:
            assert stat.S_IMODE(f.stat().st_mode) == 0o600, f
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
    finally:
        await conn.close()
