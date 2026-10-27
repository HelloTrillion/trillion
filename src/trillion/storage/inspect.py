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


def timings(path: Path, owner_id: str, limit: int) -> list[dict[str, Any]]:
    """The newest turns' timings, oldest first (ONB-11). Empty if there are none yet,
    or the database is older than the table."""
    if not path.exists():
        return []
    try:
        with contextlib.closing(_read_only(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM turn_timings WHERE owner_id = ? ORDER BY id DESC LIMIT ?",
                (owner_id, max(limit, 0)),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for row in reversed(rows):
        item = dict(row)
        try:
            calls = json.loads(item.pop("calls_json") or "[]")
        except ValueError:
            calls = []
        item["calls"] = calls if isinstance(calls, list) else []
        out.append(item)
    return out


def _soonest(conn: sqlite3.Connection, owner_id: str) -> Any:
    """The next one due, never a paused routine's: its time waits until it is resumed.
    A database from before routines has no pause to read."""
    query = (
        "SELECT next_due_ms, zone FROM schedules WHERE owner_id = ? AND status = 'active'"
        " AND next_due_ms IS NOT NULL{} ORDER BY next_due_ms LIMIT 1"
    )
    try:
        return conn.execute(query.format(" AND paused_ms IS NULL"), (owner_id,)).fetchone()
    except sqlite3.OperationalError:
        return conn.execute(query.format(""), (owner_id,)).fetchone()


def schedules(path: Path, owner_id: str, since_ms: int) -> dict[str, Any] | None:
    """Reminders and timed asks (AGT-4): how many are active, the next one due, and how
    many runs were blocked or failed since a moment. None with no database, or one from
    before the tables."""
    if not path.exists():
        return None
    try:
        with contextlib.closing(_read_only(path)) as conn:
            (active,) = conn.execute(
                "SELECT count(*) FROM schedules WHERE owner_id = ? AND status = 'active'",
                (owner_id,),
            ).fetchone()
            soonest = _soonest(conn, owner_id)
            (troubled,) = conn.execute(
                "SELECT count(*) FROM task_runs r JOIN schedules s ON s.id = r.schedule_id"
                " WHERE s.owner_id = ? AND r.status IN ('blocked', 'failed')"
                " AND COALESCE(r.finished_ms, r.claimed_ms, r.due_ms) >= ?",
                (owner_id, since_ms),
            ).fetchone()
    except sqlite3.Error:
        return None
    return {
        "active": int(active),
        "next_ms": int(soonest[0]) if soonest is not None else None,
        "zone": str(soonest[1]) if soonest is not None else "",
        "troubled": int(troubled),
    }
