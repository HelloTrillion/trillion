#!/usr/bin/env python3
"""Fail the build if two code paths write the same decision field (never-list #11).

Exactly one code path decides where an action stands. Two writers of one field are
how a "temporary" second implementation outlives the decision: both look right,
and they drift. This reads the source, finds every SQL statement that changes a
column, and checks it against the table below.

- A decision field is changed by exactly one function, its owner.
- A decision table's rows are born in exactly one function.
- An append-only table is only ever inserted into, by its one writer.
- The one writer is reached from one place: an action's status moves only through
  the router's `_move`, and proposals are saved only by its `_propose`.
- Every other column a statement changes is listed as not a decision, with why.
  A new one fails until someone says which it is: that is the point.
- A user's file that decides what she may reach is written by one module only:
  nothing else names the file or the function that says where it is. This is a
  check on names, not on what code does: a path built some other way would pass it.

    python3 scripts/check_one_writer.py [--selftest]
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "trillion"

SPINE_STORE = "storage/sqlite/spine_store.py:SqliteSpineStore"

# Decision fields, and the one function that may change each.
DECISIONS: dict[str, str] = {
    "pending_actions.status": f"{SPINE_STORE}._set_status",
    "pending_actions.effect_status": f"{SPINE_STORE}._set_status",
    "pending_actions.outcome_detail": f"{SPINE_STORE}._set_status",
}
# Where a decision table's rows are born.
BIRTHS: dict[str, str] = {
    "pending_actions": f"{SPINE_STORE}.insert_pending",
}
# Tables that are only ever appended to (and refuse edits in the database itself).
APPEND_ONLY: dict[str, str] = {
    "decisions": f"{SPINE_STORE}._decision",
    "tool_audit": f"{SPINE_STORE}.append_audit",
    "rule_events": f"{SPINE_STORE}.append_rule_event",
}
# The store methods that change a decision, and the only places that may call them.
CALLERS: dict[str, frozenset[str]] = {
    "transition": frozenset({"spine/router.py:Router._move"}),
    "insert_pending": frozenset({"spine/router.py:Router._propose"}),
    "_set_status": frozenset({f"{SPINE_STORE}.transition", f"{SPINE_STORE}.insert_pending"}),
}
_MIRRORED = "copied from the memory's file, which is the truth and decides (MEM-1)"
# Columns that are changed but decide nothing, and why.
NOT_DECISIONS: dict[str, str] = {
    "*.updated_at": "when the row last changed",
    "pending_action_args.args_json": "cleared for retention once settled (SPN-9)",
    "pending_action_args.cleared_at": "when it was cleared",
    "turn_timings.played_ms": "a measurement (ONB-11)",
    "conversations.title": "a label for the thread list; decides nothing (SUR-1)",
    "conversations.title_source": "who named it, so her title never replaces the user's",
    "conversations.last_message_at": "recency for sorting the thread list",
    "conversations.deleted_at": "soft delete (SPN-9); hides a conversation, decides no action",
    # History is otherwise only appended to. The one exception, in
    # SqliteConversationStore.amend_tail: her last spoken reply trimmed to what was heard,
    # and only while it is still the conversation's last message, so the model has
    # never been sent it (VOI-9, D8). The raw provider turn goes with it.
    **{
        f"messages.{c}": "her last spoken reply, trimmed to what was heard before the model"
        " was sent it (VOI-9, D8): it approves nothing"
        for c in ("blocks_json", "raw_json")
    },
    "preferences.value_json": "a setting the user chose",
    "boards.board_json": "what her board shows (SUR-1): it approves nothing",
    "memories.file_missing": "whether the file is on disk: read from the disk, not decided",
    **{
        f"schedules.{c}": "a reminder's own progress (AGT-4): it approves nothing"
        for c in ("status", "next_due_ms")
    },
    "schedules.paused_ms": "whether a routine runs for now (the routines spec): approves nothing",
    "schedules.failures": "a routine's failed runs in a row: a count, it approves nothing",
    "schedules.conversation_id": "where a routine's board goes: it approves nothing",
    "schedules.notes": "what a routine looks at, from the user's feedback: it approves nothing",
    **{
        f"task_runs.{c}": "a scheduled run's progress (AGT-4): it approves nothing"
        for c in (
            "status claimed_by claimed_ms started_ms finished_ms attempts detail result_notice"
        ).split()
    },
    **{
        f"notices.{c}": "whether a notice reached the user (SUR-9): it approves nothing"
        for c in ("status", "delivered_ms", "delivered_to", "notified_ms")
    },
    **{
        f"attention_finds.{c}": "whether a watcher's find was told, once (D5): approves nothing"
        for c in ("status", "notice_id", "dismissed_ms")
    },
    **{
        f"attention_state.{c}": "a watcher's place and the user's notes on it: approves nothing"
        for c in ("last_check_ms", "cursor", "last_error", "notes")
    },
    **{
        f"memories.{c}": _MIRRORED
        for c in (
            "type text source moves status version created_at as_of supersedes "
            "superseded_by file_digest"
        ).split()
    },
}

# Files that decide what she may reach, the function in paths.py that says where
# each is, and the one module that may read or write it.
FILE_OWNERS: dict[str, tuple[str, str]] = {
    "folders.json": ("folders_config_path", "files/shares.py"),
}
_PATHS = "paths.py"

_UPDATE = re.compile(r"\bUPDATE\s+(\w+)\s+SET\s+(.+?)(?=\s+WHERE\b|$)", re.S)
_UPSERT = re.compile(
    r"\bINSERT\s+INTO\s+(\w+)\b.*?\bDO\s+UPDATE\s+SET\s+(.+?)(?=\s+WHERE\b|$)", re.S
)
_INSERT = re.compile(r"\bINSERT\s+INTO\s+(\w+)\b")
_COLUMN = re.compile(r"(?<![\w.])(\w+)\s*=")


@dataclass(frozen=True)
class Write:
    table: str
    column: str  # "" for a row inserted
    where: str  # file:Class.function
    line: int


@dataclass(frozen=True)
class Call:
    method: str
    where: str
    line: int


@dataclass(frozen=True)
class FileUse:
    file: str
    how: str  # the name or the function that named it
    rel: str
    line: int


def _text(node: ast.AST) -> str | None:
    """A string literal, or an f-string with its holes as {}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
            for v in node.values
        )
    return None


def writes_in(sql: str) -> list[tuple[str, str]]:
    """(table, column) for each column a statement changes; (table, "") for a new row."""
    out: list[tuple[str, str]] = []
    for table, clause in _UPDATE.findall(sql):
        out += [(table, c) for c in _COLUMN.findall(clause)]
    for table, clause in _UPSERT.findall(sql):
        out += [(table, c) for c in _COLUMN.findall(clause)]
    out += [(table, "") for table in _INSERT.findall(sql)]
    return out


class _Scan(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.scope: list[str] = []
        self.writes: list[Write] = []
        self.calls: list[Call] = []
        self.files: list[FileUse] = []
        self.docstrings: set[int] = set()

    def _where(self) -> str:
        return f"{self.rel}:{'.'.join(self.scope) or '<module>'}"

    def _enter(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _enter

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        self._sql(node)  # not deeper: its parts are not statements of their own

    def visit_Constant(self, node: ast.Constant) -> None:
        self._sql(node)
        # A docstring may say which file a module is about; code that names it may not.
        if isinstance(node.value, str) and id(node) not in self.docstrings:
            for file in FILE_OWNERS:
                if file in node.value:
                    self.files.append(FileUse(file, repr(file), self.rel, node.lineno))

    def visit_Name(self, node: ast.Name) -> None:
        self._named(node.id, node.lineno)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._named(node.attr, node.lineno)
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        self._named(node.name, node.lineno)

    def _named(self, name: str, line: int) -> None:
        for file, (function, _) in FILE_OWNERS.items():
            if name == function:
                self.files.append(FileUse(file, f"{function}()", self.rel, line))

    def _sql(self, node: ast.AST) -> None:
        text = _text(node)
        if text:
            for table, column in writes_in(text):
                self.writes.append(Write(table, column, self._where(), node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in CALLERS:
            self.calls.append(Call(node.func.attr, self._where(), node.lineno))
        self.generic_visit(node)


def _docstrings(tree: ast.Module) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def scan_source(source: str, rel: str) -> tuple[list[Write], list[Call], list[FileUse]]:
    scan = _Scan(rel)
    tree = ast.parse(source)
    scan.docstrings = _docstrings(tree)
    scan.visit(tree)
    return scan.writes, scan.calls, scan.files


def problems(
    writes: list[Write], calls: list[Call], files: list[FileUse] | None = None
) -> list[str]:
    out: list[str] = []
    for f in files or []:
        _, owner = FILE_OWNERS[f.file]
        if f.rel not in (owner, _PATHS):
            out.append(f"  {f.rel}:{f.line}  names {f.file} ({f.how}); only {owner} may")
    changers: dict[str, set[str]] = defaultdict(set)
    for w in writes:
        at = f"{w.where.split(':')[0]}:{w.line}"
        if w.table in APPEND_ONLY:
            if w.column:
                out.append(f"  {at}  changes {w.table}.{w.column}: that table is append-only")
            elif w.where != APPEND_ONLY[w.table]:
                out.append(f"  {at}  inserts into {w.table}; only {APPEND_ONLY[w.table]} may")
            continue
        if not w.column:
            owner = BIRTHS.get(w.table)
            if owner is not None and w.where != owner:
                out.append(f"  {at}  creates a {w.table} row; only {owner} may")
            continue
        field = f"{w.table}.{w.column}"
        if field in DECISIONS:
            changers[field].add(w.where)
            if w.where != DECISIONS[field]:
                out.append(f"  {at}  changes {field}; only {DECISIONS[field]} may")
        elif field not in NOT_DECISIONS and f"*.{w.column}" not in NOT_DECISIONS:
            out.append(
                f"  {at}  changes {field}, which is not classified. Add it to DECISIONS "
                "(with its one writer) or NOT_DECISIONS (with why) in scripts/check_one_writer.py"
            )
    for field, owner in DECISIONS.items():
        if owner not in changers[field]:
            out.append(f"  {field}: its writer {owner} was not found; update this check")
    for c in calls:
        if c.where not in CALLERS[c.method]:
            allowed = ", ".join(sorted(CALLERS[c.method]))
            out.append(f"  {c.where.split(':')[0]}:{c.line}  calls {c.method}; only {allowed} may")
    return out


def scan(root: Path) -> list[str]:
    writes: list[Write] = []
    calls: list[Call] = []
    files: list[FileUse] = []
    for py in sorted(root.rglob("*.py")):
        w, c, f = scan_source(py.read_text(encoding="utf-8"), py.relative_to(root).as_posix())
        writes += w
        calls += c
        files += f
    return problems(writes, calls, files)


# ── Self-test: a good store passes, and each way of breaking the rule fails ──

_GOOD = """
class SqliteSpineStore:
    async def insert_pending(self, conn):
        await conn.execute("INSERT INTO pending_actions (id, status) VALUES (?, ?)")
        await self._set_status(conn)
    async def transition(self, conn):
        return await self._set_status(conn)
    async def _set_status(self, conn):
        await conn.execute(
            "UPDATE pending_actions SET status = ?, updated_at = ?,"
            " effect_status = COALESCE(?, effect_status), outcome_detail = ?"
            " WHERE id = ? AND status = ?"
        )
    async def _decision(self, conn):
        await conn.execute("INSERT INTO decisions (event) VALUES (?)")
    async def append_audit(self, conn):
        await conn.execute("INSERT INTO tool_audit (outcome) VALUES (?)")
"""
_ROUTER = """
class Router:
    async def _move(self):
        return await self._store.transition()
    async def _propose(self):
        await self._store.insert_pending()
"""
_BAD = {
    "a second writer of the status": """
class SqliteSpineStore:
    async def expire_all(self, conn):
        await conn.execute("UPDATE pending_actions SET status = 'expired' WHERE status = ?")
""",
    "an upsert that changes the status": """
async def restore(conn):
    await conn.execute(
        "INSERT INTO pending_actions (id) VALUES (?) "
        "ON CONFLICT (id) DO UPDATE SET status = excluded.status"
    )
""",
    "an edit to an append-only table": """
async def fix(conn):
    await conn.execute("UPDATE decisions SET event = ? WHERE id = ?")
""",
    "a second place rows are inserted into the record": """
async def backfill(conn):
    await conn.execute(f"INSERT INTO decisions ({COLUMNS}) VALUES (?)")
""",
    "the status moved around the router's one path": """
class Router:
    async def _cancel_quietly(self):
        await self._store.transition()
""",
    "a column nobody classified": """
async def reroute(conn):
    await conn.execute("UPDATE pending_actions SET tool = ? WHERE id = ?")
""",
    "a second writer of folders.json": """
def save(data):
    (paths.home() / "folders.json").write_text(data)
""",
    "the shares file reached around its owner": """
from trillion.paths import folders_config_path
""",
    "the shares file's path read around its owner": """
def save(data):
    paths.folders_config_path().write_text(data)
""",
}
# A docstring that only says which file a module is about is not a second writer.
_FILE_GOOD = '''
"""Routes for the shares in folders.json, through trillion.files.shares."""
def path():
    return paths.folders_config_path()
'''


def selftest() -> int:
    good_w, good_c, _ = scan_source(_GOOD, "storage/sqlite/spine_store.py")
    router_w, router_c, _ = scan_source(_ROUTER, "spine/router.py")
    _, _, owner_f = scan_source(_FILE_GOOD, "files/shares.py")
    _, _, doc_f = scan_source(_FILE_GOOD.split("def")[0], "api/folders.py")
    failures = []
    baseline = problems(good_w + router_w, good_c + router_c, owner_f + doc_f)
    if baseline:
        failures.append("  the good fixture was refused:\n" + "\n".join(baseline))
    for name, source in _BAD.items():
        w, c, f = scan_source(source, "spine/router.py" if "Router" in source else "elsewhere.py")
        if not problems(good_w + router_w + w, good_c + router_c + c, f):
            failures.append(f"  not caught: {name}")
    # And the real owner disappearing is noticed, not silently passed.
    if not problems(router_w, router_c):
        failures.append("  not caught: the status writer was removed")
    if failures:
        print("One-writer self-test FAILED:\n" + "\n".join(failures))
        return 1
    print("One-writer self-test passed.")
    return 0


def main() -> int:
    found = scan(SRC)
    if found:
        print("One-writer check FAILED (never-list #11):\n" + "\n".join(found))
        return 1
    print("One-writer check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
