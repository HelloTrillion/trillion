"""Open the one local database with the settings every caller needs."""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

import aiosqlite

from trillion import owner_only

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
    _make_private(path)
    return conn


def _make_private(path: Path) -> None:
    """Pending actions and outcomes hold argument values: owner-only, whatever the umask.

    SQLite creates the -wal and -shm files with the database file's mode. Windows
    ignores POSIX modes: there the folder and the file get an access list of their own,
    the user's alone, and the -wal and -shm files inherit the folder's (SUR-3c).
    `restrict` does nothing elsewhere."""
    owner_only.restrict(path.parent)
    owner_only.restrict(path)
    if os.name == "nt":
        return
    os.chmod(path.parent, 0o700)
    os.chmod(path, 0o600)
    for suffix in ("-wal", "-shm"):
        with contextlib.suppress(FileNotFoundError):
            os.chmod(f"{path}{suffix}", 0o600)
