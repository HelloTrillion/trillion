import os
import sqlite3

import pytest

from trillion.storage.db import connect
from trillion.storage.migrate import MigrationChecksumError, migrate


async def test_applies_once_and_records_checksum(tmp_path):
    conn = await connect(tmp_path / "t.db")
    assert await migrate(conn) == [
        "001_spine",
        "002_args_retention",
        "003_conversations",
        "004_preferences",
        "005_memory",
        "006_compactions",
    ]
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


# Migrations that have shipped. A database that applied one refuses to open if the
# file later changes by a single byte, comments included, so these are frozen: to
# change the schema, add a new numbered file. A new migration adds a line here.
SHIPPED = {
    "001_spine.sql": "74e5901760ef1de981c06f0a94cc2a0dd3cd9aa9800e87028886520b8ff671f1",
    "002_args_retention.sql": "d226e1823fa839252e5d984fd14a7fd1d402fa54d795a4419bfa817711889bec",
    "003_conversations.sql": "7b495ef2389ec7513caf9ca71be306adfcf03f0e765847dbe003087836ad6ba8",
    "004_preferences.sql": "70d48ef6ba5422c4bb15b894697ad7c320e509880ce12ab7b789abfd6302c2c5",
    "005_memory.sql": "e710a7ddf4f2a545866466441160e9f8a2c43bc9fc4a03580cd96badf64374e1",
    "006_compactions.sql": "ba00340cbba9ea010e9bce8f36e053b65b126620af234aeab07ee6a7e8ee2210",
}


def test_shipped_migrations_never_change():
    import hashlib
    from importlib.resources import files

    found = {
        entry.name: hashlib.sha256(entry.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        for entry in (files("trillion.storage") / "migrations").iterdir()
        if entry.name.endswith(".sql")
    }
    assert found == SHIPPED, "a shipped migration changed, or a new one is not listed here"
