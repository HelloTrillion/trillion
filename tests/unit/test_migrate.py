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
        "007_turn_timings",
        "008_schedules",
        "009_turn_drafts",
        "010_conversation_titles",
        "011_boards",
        "012_rule_events",
        "013_day_conversations",
        "014_routines",
        "015_attention",
        "016_decision_events",
        "017_usage_spend",
        "018_attention_deferred",
        "019_helpers",
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
    "007_turn_timings.sql": "94fd5927476868590c692dd4d25eb6759cbdb97028490934b91c8174c31143e7",
    "008_schedules.sql": "6cf1aff31f5f9a4f182452af311525b2ed1ee79f31c958063c718ab1a276ec12",
    "009_turn_drafts.sql": "ebdb53c705c7a5c7ffc12dc6d18e279ffb452c867de9603122b96bcad165b5a6",
    "010_conversation_titles.sql": (
        "93bc8f7ed4b938ae7751a0ddd19e36e0ce54771a327bc747eceff35444edd769"
    ),
    "011_boards.sql": "e76da2a7b12317962aa0cf242920324717eb36852b1bfd3127d0c4d537684e6a",
    "012_rule_events.sql": "ce8dce7c93dfce7db8a9c7fed4676954d371a9a1277e45bf6867cb60f645154f",
    "013_day_conversations.sql": (
        "613e24ac9ccd3341281cba89b58e5352d32632474ee570a101c5c69e6dbb7b19"
    ),
    "014_routines.sql": "8f464a56fa9e92e11293af638c5f14e72da1bf8517858e17fac9af055588bedf",
    "015_attention.sql": "cce2497b7bb1199dffb1f7925dbe37c8c8d621a19cbd416d27497a023bb58ea6",
    "016_decision_events.sql": ("7ed1dbea2c94cd2255016b0afba2045a98b11a7448650c25e529cc0c71cf52bb"),
    "017_usage_spend.sql": "14f30b86a751145bcaf72fa291f509d54e0c2545be606df1ee38d331c6e410e5",
    "018_attention_deferred.sql": (
        "521447edaea29b579b5dde70a3e573e2826531ea653abce865e80318f1d98d8d"
    ),
    "019_helpers.sql": ("18a4cd696fdc50d98912e1e453bebdfeff84b3c22b5b6314409e5cb7dff1100a"),
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
