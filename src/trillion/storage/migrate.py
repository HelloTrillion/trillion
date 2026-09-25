"""Apply numbered SQL migrations once each, and refuse to run over an edited one."""

from __future__ import annotations

import hashlib
import re
import time
from importlib.resources import files
from importlib.resources.abc import Traversable

import aiosqlite

_FILE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class MigrationChecksumError(RuntimeError):
    pass


def _bundled() -> Traversable:
    return files("trillion.storage") / "migrations"


async def migrate(conn: aiosqlite.Connection, source: Traversable | None = None) -> list[str]:
    """Apply pending migrations in order; return the names applied."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, name TEXT NOT NULL,"
        " checksum TEXT NOT NULL, applied_at INTEGER NOT NULL)"
    )
    async with conn.execute("SELECT version, checksum FROM schema_migrations") as cur:
        applied = {row[0]: row[1] for row in await cur.fetchall()}

    done: list[str] = []
    entries = sorted(
        (e for e in (source or _bundled()).iterdir() if _FILE.match(e.name)),
        key=lambda e: e.name,
    )
    for entry in entries:
        m = _FILE.match(entry.name)
        if m is None:  # pragma: no cover - excluded by the iterdir filter above
            raise MigrationChecksumError(f"{entry.name!r} does not match the migration pattern")
        version, name = m.group(1), m.group(2)
        sql = entry.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if version in applied:
            if applied[version] != checksum:
                raise MigrationChecksumError(
                    f"migration {entry.name} changed after it was applied; add a new migration"
                )
            continue
        now = time.time_ns() // 1_000_000
        # version, name and checksum are regex- or hash-derived, never user input.
        record = (
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) "  # noqa: S608
            f"VALUES ('{version}', '{name}', '{checksum}', {now});"
        )
        try:
            await conn.executescript(f"BEGIN;\n{sql}\n{record}\nCOMMIT;")
        except Exception:
            await conn.execute("ROLLBACK")
            raise
        done.append(f"{version}_{name}")
    return done
