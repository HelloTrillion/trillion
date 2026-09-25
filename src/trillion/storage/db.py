"""Open the one local database with the settings every caller needs."""

from __future__ import annotations

from pathlib import Path

import aiosqlite


async def connect(path: Path) -> aiosqlite.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # isolation_level=None: autocommit; multi-statement units use explicit BEGIN.
    conn = await aiosqlite.connect(path, isolation_level=None)
    conn.row_factory = aiosqlite.Row
    for pragma in (
        "PRAGMA journal_mode=WAL",
        "PRAGMA foreign_keys=ON",
        "PRAGMA busy_timeout=5000",
        "PRAGMA synchronous=NORMAL",
    ):
        await conn.execute(pragma)
    return conn
