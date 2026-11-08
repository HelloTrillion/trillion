import os
import sqlite3
from pathlib import Path

import pytest

from trillion.storage.db import connect
from trillion.storage.migrate import MigrationChecksumError, migrate, names


async def test_applies_once_and_records_checksum(tmp_path):
    conn = await connect(tmp_path / "t.db")
    assert await migrate(conn) == names()
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


@pytest.mark.skipif(os.name != "nt", reason="Windows access lists (SUR-3c)")
async def test_the_database_is_private_to_the_user_on_windows(tmp_path):
    """The folder and the file get access lists of their own; -wal and -shm inherit."""
    from trillion import owner_only

    home = tmp_path / "home"
    home.mkdir()
    conn = await connect(home / "t.db")
    try:
        await migrate(conn)
        for f in [home, home / "t.db", home / "t.db-wal", home / "t.db-shm"]:
            if f.exists():
                assert owner_only.exposed(f) == [], (f, owner_only._read(f))
        for f in [home, home / "t.db"]:
            assert owner_only._read(f).startswith("D:P"), f  # nothing inherited from above
    finally:
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
# change the schema, add a new file named by the time (YYYYMMDDHHMM_name.sql). Each
# shipped migration's SHA-256 is in its own file here, so a new one adds a file.
SHIPPED = Path(__file__).parent / "shipped_migrations"


def test_shipped_migrations_never_change():
    import hashlib
    from importlib.resources import files

    found = {
        entry.name: hashlib.sha256(entry.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        for entry in (files("trillion.storage") / "migrations").iterdir()
        if entry.name.endswith(".sql")
    }
    shipped = {
        f"{p.stem}.sql": p.read_text(encoding="utf-8").strip() for p in SHIPPED.glob("*.sha256")
    }
    assert found == shipped, (
        "a shipped migration changed, or a new one has no checksum file. A new migration "
        "adds tests/unit/shipped_migrations/<its name>.sha256 holding its SHA-256."
    )


async def test_a_migration_named_by_time_runs_after_every_numbered_one(tmp_path):
    """Two branches can't take the same next number when new ones are named by time."""
    src = tmp_path / "migrations"
    src.mkdir()
    (src / "001_first.sql").write_text("CREATE TABLE a (x);", encoding="utf-8")
    (src / "202611071200_later.sql").write_text("CREATE TABLE c (x);", encoding="utf-8")
    (src / "202611071130_earlier.sql").write_text("CREATE TABLE b (x);", encoding="utf-8")
    (src / "02_bad_number.sql").write_text("CREATE TABLE z (x);", encoding="utf-8")
    assert names(source=src) == ["001_first", "202611071130_earlier", "202611071200_later"]
    assert names(after="001", source=src) == ["202611071130_earlier", "202611071200_later"]
    conn = await connect(tmp_path / "t.db")
    assert await migrate(conn, src) == names(source=src)
    assert await migrate(conn, src) == []
    await conn.close()
