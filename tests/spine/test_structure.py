"""Guarantees about the code itself, not its behaviour."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import aiosqlite
import pytest

from tests.spine.conftest import Spine
from tests.spine.fakes import MARKER

SRC = Path(__file__).resolve().parents[2] / "src" / "trillion"

ROUTER = "spine/router.py"
# Database connections are not tools. Only storage code may call `.execute` on them.
DB_RECEIVERS = frozenset({"conn", "self._conn"})


def test_only_the_router_calls_execute() -> None:
    offenders = []
    for py in sorted(SRC.rglob("*.py")):
        rel = py.relative_to(SRC).as_posix()
        if rel == ROUTER:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Any reference, not just a call: handing `tool.execute` to something else counts.
            if isinstance(node, ast.Attribute) and node.attr == "execute":
                receiver = ast.unparse(node.value)
                if rel.startswith("storage/") and receiver in DB_RECEIVERS:
                    continue
                offenders.append(f"{rel}:{node.lineno} {receiver}.execute")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and any(
                    isinstance(a, ast.Constant) and a.value == "execute" for a in node.args[1:2]
                )
            ):
                offenders.append(f"{rel}:{node.lineno} getattr(..., 'execute')")
    assert offenders == [], "outside spine/router.py:\n" + "\n".join(offenders)


def test_the_router_really_is_the_caller() -> None:
    """Guards the test above from passing because the file moved or was renamed."""
    tree = ast.parse((SRC / ROUTER).read_text(encoding="utf-8"))
    calls = [
        ast.unparse(n.func)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "execute"
    ]
    assert "tool.execute" in calls


def test_no_hard_delete_anywhere_in_source_or_migrations() -> None:
    pattern = re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE)
    files = list(SRC.rglob("*.py")) + list(SRC.rglob("*.sql"))
    assert any(p.suffix == ".sql" for p in files)
    hits = [str(p.relative_to(SRC)) for p in files if pattern.search(p.read_text(encoding="utf-8"))]
    assert hits == []


async def test_decision_and_audit_tables_refuse_edits(spine: Spine) -> None:
    r = await spine.call("file_create", {"path": str(spine.granted / "a.txt"), "content": "x"})
    assert r.status == "confirmation_required"
    assert await spine.router.mark_delivered(r.pending_id or "", "local", "web")
    assert await spine.store.list_audit()
    conn = spine.store._conn
    for table in ("tool_audit", "decisions", "decision_deliveries"):
        for sql in (f"UPDATE {table} SET owner_id = 'x'", f"DELETE FROM {table}"):  # noqa: S608
            with pytest.raises(aiosqlite.Error, match="append-only"):
                await conn.execute(sql)


async def test_audit_records_hashes_never_values(spine: Spine) -> None:
    secret = MARKER
    await spine.call("file_create", {"path": str(spine.granted / "a.txt"), "content": secret})
    await spine.call("file_read", {"path": str(spine.granted / f"{secret}.txt")})
    records = await spine.store.list_audit()
    assert len(records) == 2
    for rec in records:
        assert secret not in repr(rec)
        assert re.fullmatch(r"[0-9a-f]{64}", rec.args_hash)


def test_the_log_check_sees_the_call_phase(caplog: pytest.LogCaptureFixture) -> None:
    """The autouse leak check reads records by phase; prove the call phase is really there."""
    logging.getLogger("trillion.probe").debug("probe")
    assert [r.getMessage() for r in caplog.get_records("call")] == ["probe"]


async def test_the_database_driver_does_not_log_statements(spine: Spine) -> None:
    assert logging.getLogger("aiosqlite").getEffectiveLevel() > logging.DEBUG


FROZEN_COLUMNS = (
    "args_json",
    "id",
    "owner_id",
    "tool",
    "args_hash",
    "summary",
    "target",
    "risk",
    "recoverable",
    "session_id",
    "created_turn",
    "created_at",
    "represented_from",
    "precondition_token",
)


async def test_what_the_user_approves_cannot_be_edited_in_the_database(spine: Spine) -> None:
    """Only the lifecycle columns move. The proposal itself is frozen at the storage layer."""
    doc = spine.granted / "doc.md"
    doc.write_text("v1")
    r = await spine.call("file_overwrite", {"path": str(doc), "content": "x"})
    other = await spine.call("slow_irreversible", {})
    pid = r.pending_id or ""
    conn = spine.store._conn
    await conn.execute("INSERT INTO owners (id, created_at) VALUES ('other', 0)")
    values: dict[str, object] = {
        "owner_id": "other",
        "risk": "spend",
        "recoverable": 1,
        "created_turn": 99,
        "created_at": 1,
        "represented_from": other.pending_id,
    }
    for col in FROZEN_COLUMNS:
        value = values.get(col, "tampered")
        with pytest.raises(aiosqlite.Error, match="frozen"):
            await conn.execute(
                f"UPDATE pending_actions SET {col} = ? WHERE id = ?",  # noqa: S608
                (value, pid),
            )
    a = await spine.store.get_pending(pid)
    assert a is not None and a.summary == r.summary and a.args["content"] == "x"
    # The lifecycle still moves.
    res = await spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap"))
    assert res.status == "succeeded"
