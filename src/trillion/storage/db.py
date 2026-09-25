"""Open the one local database with the settings every caller needs."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

# aiosqlite logs every statement with its parameters at DEBUG: argument values, tool
# output, outcome details. Nothing in Trillion may log those, at any level.
_DRIVER_LOG_FLOOR = logging.INFO


async def connect(path: Path) -> aiosqlite.Connection:
    logging.getLogger("aiosqlite").setLevel(_DRIVER_LOG_FLOOR)
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
