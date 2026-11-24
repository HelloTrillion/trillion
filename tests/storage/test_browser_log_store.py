"""Her browser's task log never ends a transaction it didn't begin.

Connections are autocommit (`storage/db.py`), so the log's one-statement writes commit
by themselves. A `commit()` of its own, on a connection another store shares, would
end that store's `BEGIN IMMEDIATE` halfway: the first half committed, and the store's
own COMMIT then failing with "no transaction is active". In a browser task that failed
the whole attempt (CI, the chip test).
"""

from __future__ import annotations

from pathlib import Path

from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.browser_log_store import SqliteBrowserLog
from trillion.storage.sqlite.spine_store import SqliteSpineStore


async def test_a_log_write_inside_another_stores_transaction_leaves_it_whole(
    tmp_path: Path,
) -> None:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        spine, log = SqliteSpineStore(conn), SqliteBrowserLog(conn)
        async with spine._tx():
            await log.append("local", "0" * 16, 1, "started", None, None)
            await log.clear_old(2)
            assert conn.in_transaction
        # The spine store's COMMIT found its transaction, and the row went with it.
        assert not conn.in_transaction
        assert [r.event for r in await log.for_task("local", "0" * 16, 1)] == ["started"]
    finally:
        await conn.close()


async def test_on_its_own_a_log_write_is_on_the_disk_at_once(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    conn, other = await connect(path), await connect(path)
    try:
        await migrate(conn)
        await SqliteBrowserLog(conn).append("local", "0" * 16, 1, "started", None, None)
        assert not conn.in_transaction
        seen = await SqliteBrowserLog(other).for_task("local", "0" * 16, 1)
        assert [r.event for r in seen] == ["started"]
    finally:
        await conn.close()
        await other.close()
