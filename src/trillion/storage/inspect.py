"""Look at the database without changing it, for diagnostics (ONB-5).

Opened read-only, with the standard library's synchronous driver: nothing here
can create the file, apply a migration or write a row.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

_MIGRATION = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class Unreadable(Exception):
    """The file is there but is not a database this build can read."""


@dataclass(frozen=True, kw_only=True)
class Schema:
    # False when SQLite's own quick check found damage.
    sound: bool
    # Version to checksum, as recorded when each migration was applied.
    applied: dict[str, str]


def shipped() -> dict[str, str]:
    """Version to checksum, for every migration this build carries."""
    out: dict[str, str] = {}
    for entry in (files("trillion.storage") / "migrations").iterdir():
        m = _MIGRATION.match(entry.name)
        if m:
            sql = entry.read_text(encoding="utf-8")
            out[m.group(1)] = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    return out


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2.0)


def schema(path: Path) -> Schema:
    try:
        with contextlib.closing(_read_only(path)) as conn:
            sound = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    except sqlite3.Error as e:
        raise Unreadable(type(e).__name__) from e
    return Schema(sound=sound, applied={str(v): str(c) for v, c in rows})


def preferences(path: Path, owner_id: str) -> dict[str, Any]:
    """The owner's saved settings. Empty if there is no database or it cannot be read."""
    if not path.exists():
        return {}
    try:
        with contextlib.closing(_read_only(path)) as conn:
            rows = conn.execute(
                "SELECT key, value_json FROM preferences WHERE owner_id = ?", (owner_id,)
            ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, Any] = {}
    for key, raw in rows:
        with contextlib.suppress(ValueError):
            out[str(key)] = json.loads(raw)
    return out
