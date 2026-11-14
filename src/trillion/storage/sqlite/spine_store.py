"""SpineStore on SQLite. One lock serializes writes so compare-and-set is exact."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from trillion.brain.standing import RuleEvent
from trillion.spine.audit import AuditRecord
from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.spine.deny_floor import fold
from trillion.spine.hashing import canonical_json
from trillion.spine.retention import SETTLED, slim
from trillion.spine.store import DecisionIn, DecisionRow, DeliveryRow

_PENDING_COLUMNS = (
    "id, owner_id, tool, args_json, args_hash, summary, target, risk, recoverable, session_id,"
    " created_turn, status, expires_at, represented_from, effect_status, outcome_detail,"
    " created_at, updated_at, precondition_token"
)
# Reads join the full arguments, which live apart from the frozen row (#20). Built only
# from the constant column list above, never from input.
_SELECT_PENDING = (
    "SELECT "  # noqa: S608
    + ", ".join(f"p.{c.strip()}" for c in _PENDING_COLUMNS.split(","))
    + ", x.args_json AS full_args_json, x.cleared_at AS args_cleared_at, x.detail AS detail"
    " FROM pending_actions p LEFT JOIN pending_action_args x ON x.pending_id = p.id"
)
# An audited call that ran: a card the user turned down is no use of anything.
_ALLOWED = frozenset({"executed"})
# What the user reads an action's status as (SUR-7): a proposal is waiting until it
# expires, and one retired by a newer request or a changed target was cancelled. The
# one parameter is the time now. Built only from constants.
_LIFECYCLE = (
    "CASE WHEN p.status = 'proposed' AND p.expires_at > ? THEN 'pending'"
    " WHEN p.status = 'proposed' THEN 'expired'"
    " WHEN p.status IN ('superseded', 'invalidated') THEN 'cancelled'"
    " ELSE p.status END"
)
# The frozen row only: its arguments are the slim copy (#20). The full ones, kept apart
# for thirty days, are never read for the Confirmations page.
_SELECT_SLIM = (
    "SELECT "  # noqa: S608
    + ", ".join(f"p.{c.strip()}" for c in _PENDING_COLUMNS.split(","))
    + ", NULL AS full_args_json, NULL AS args_cleared_at, NULL AS detail, "
    + _LIFECYCLE
    + " AS lifecycle FROM pending_actions p WHERE p.owner_id = ? AND p.created_at >= ?"
)


def _row_to_pending(r: aiosqlite.Row) -> PendingAction:
    # The full arguments when they are still kept; otherwise the frozen row's copy,
    # which is the slim one — or, for a row written before #20, the full one.
    full = r["full_args_json"]
    args: dict[str, Any] = json.loads(str(full if full is not None else r["args_json"]))
    cleared = full is None and r["args_cleared_at"] is not None
    return PendingAction(
        id=str(r["id"]),
        owner_id=str(r["owner_id"]),
        tool=str(r["tool"]),
        args=args,
        args_hash=str(r["args_hash"]),
        summary=str(r["summary"]),
        target=str(r["target"]),
        risk=Risk(r["risk"]),
        recoverable=bool(r["recoverable"]),
        session_id=str(r["session_id"]),
        created_turn=int(r["created_turn"]),
        status=Status(r["status"]),
        created_at=int(r["created_at"]),
        expires_at=int(r["expires_at"]),
        represented_from=(
            str(r["represented_from"]) if r["represented_from"] is not None else None
        ),
        effect_status=(str(r["effect_status"]) if r["effect_status"] is not None else None),
        outcome_detail=(str(r["outcome_detail"]) if r["outcome_detail"] is not None else None),
        precondition_token=(
            str(r["precondition_token"]) if r["precondition_token"] is not None else None
        ),
        args_cleared=cleared,
        updated_at=int(r["updated_at"]),
        # What the card shows in full (SPN-2a): kept, and cleared, with the arguments.
        detail=str(r["detail"]) if r["detail"] is not None else "",
    )


class SqliteSpineStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()
        # Told after a card is raised or moves on: the helpers panel's "needs you".
        self.changed: Callable[[], None] | None = None

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
            except BaseException:
                # Also when cancelled while BEGIN was still on its way: the database
                # thread runs it regardless, after the caller has gone. The rollback is
                # queued behind it, and shielded so a second cancellation cannot skip
                # it, so a transaction is never left open on the connection. Rolling
                # back when none began does nothing.
                await asyncio.shield(self._conn.rollback())
                raise
            await self._conn.execute("COMMIT")

    async def _decision(
        self, conn: aiosqlite.Connection, owner_id: str, pending_id: str, d: DecisionIn, at: int
    ) -> None:
        await conn.execute(
            "INSERT INTO decisions (owner_id, pending_id, event, decided_by, via, at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, pending_id, d.event, d.decided_by, d.via, at),
        )

    async def insert_pending(
        self, a: PendingAction, decision: DecisionIn, *, supersede: DecisionIn | None = None
    ) -> list[str]:
        """Save a new proposal. With `supersede`, first retire every open proposal from the
        same session for the same tool and target, in the same transaction (#4): two
        proposals racing for one file can never both stay open. Targets are compared
        folded, as the file system compares them, so `Notes.txt` and `notes.txt` are one.
        Returns the ids retired."""
        retired: list[str] = []
        async with self._tx() as conn:
            if supersede is not None:
                async with conn.execute(
                    "SELECT id, target FROM pending_actions WHERE owner_id = ?"
                    " AND session_id = ? AND tool = ? AND status = ?",
                    (a.owner_id, a.session_id, a.tool, Status.PROPOSED.value),
                ) as cur:
                    rows = await cur.fetchall()
                for r in rows:
                    if fold(str(r["target"])) != fold(a.target):
                        continue
                    if await self._set_status(
                        conn,
                        str(r["id"]),
                        Status.PROPOSED,
                        Status.SUPERSEDED,
                        now_ms=a.created_at,
                        decision=supersede,
                    ):
                        retired.append(str(r["id"]))
            await conn.execute(
                f"INSERT INTO pending_actions ({_PENDING_COLUMNS})"  # noqa: S608
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    a.id,
                    a.owner_id,
                    a.tool,
                    canonical_json(slim(a.args)),  # the frozen copy is the slim one
                    a.args_hash,  # ...and this still covers the full arguments
                    a.summary,
                    a.target,
                    a.risk.value,
                    int(a.recoverable),
                    a.session_id,
                    a.created_turn,
                    a.status.value,
                    a.expires_at,
                    a.represented_from,
                    a.effect_status,
                    a.outcome_detail,
                    a.created_at,
                    a.created_at,
                    a.precondition_token,
                ),
            )
            await conn.execute(
                "INSERT INTO pending_action_args (pending_id, owner_id, args_json, detail)"
                " VALUES (?, ?, ?, ?)",
                (a.id, a.owner_id, canonical_json(a.args), a.detail or None),
            )
            await self._decision(conn, a.owner_id, a.id, decision, a.created_at)
        if self.changed is not None:
            self.changed()
        return retired

    async def owner_known(self, owner_id: str) -> bool:
        async with self._conn.execute("SELECT 1 FROM owners WHERE id = ?", (owner_id,)) as cur:
            return await cur.fetchone() is not None

    async def get_pending(self, pending_id: str) -> PendingAction | None:
        async with self._conn.execute(
            f"{_SELECT_PENDING} WHERE p.id = ?",
            (pending_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_pending(row) if row else None

    async def transition(
        self,
        pending_id: str,
        expected: Status,
        new: Status,
        *,
        now_ms: int,
        decision: DecisionIn,
        effect_status: str | None = None,
        outcome_detail: str | None = None,
    ) -> bool:
        async with self._tx() as conn:
            moved = await self._set_status(
                conn,
                pending_id,
                expected,
                new,
                now_ms=now_ms,
                decision=decision,
                effect_status=effect_status,
                outcome_detail=outcome_detail,
            )
        if moved and self.changed is not None:
            self.changed()
        return moved

    async def _set_status(
        self,
        conn: aiosqlite.Connection,
        pending_id: str,
        expected: Status,
        new: Status,
        *,
        now_ms: int,
        decision: DecisionIn,
        effect_status: str | None = None,
        outcome_detail: str | None = None,
    ) -> bool:
        """The one statement that changes where an action stands, with the decision that
        moved it (never-list #11, scripts/check_one_writer.py). Compare-and-set: False
        means it was not at `expected`. Runs inside the caller's transaction."""
        cur = await conn.execute(
            "UPDATE pending_actions SET status = ?, updated_at = ?,"
            " effect_status = COALESCE(?, effect_status),"
            " outcome_detail = COALESCE(?, outcome_detail)"
            " WHERE id = ? AND status = ?",
            (new.value, now_ms, effect_status, outcome_detail, pending_id, expected.value),
        )
        if cur.rowcount != 1:
            return False
        async with conn.execute(
            "SELECT owner_id FROM pending_actions WHERE id = ?", (pending_id,)
        ) as found:
            row = await found.fetchone()
        if row is None:  # pragma: no cover - the UPDATE above just matched this row
            raise RuntimeError(f"pending action {pending_id!r} vanished mid-transaction")
        await self._decision(conn, str(row["owner_id"]), pending_id, decision, now_ms)
        return True

    async def open_for_target(
        self, owner_id: str, session_id: str, tool: str, target: str
    ) -> list[PendingAction]:
        async with self._conn.execute(
            f"{_SELECT_PENDING}"
            " WHERE p.owner_id = ? AND p.session_id = ? AND p.tool = ? AND p.target = ?"
            " AND p.status = ?",
            (owner_id, session_id, tool, target, Status.PROPOSED.value),
        ) as cur:
            return [_row_to_pending(r) for r in await cur.fetchall()]

    async def list_by_status(self, status: Status) -> list[PendingAction]:
        async with self._conn.execute(
            f"{_SELECT_PENDING} WHERE p.status = ?",
            (status.value,),
        ) as cur:
            return [_row_to_pending(r) for r in await cur.fetchall()]

    async def list_represented_from(self, pending_id: str) -> list[PendingAction]:
        async with self._conn.execute(
            f"{_SELECT_PENDING} WHERE p.represented_from = ?",
            (pending_id,),
        ) as cur:
            return [_row_to_pending(r) for r in await cur.fetchall()]

    async def clear_settled_args(self, *, settled_before_ms: int, now_ms: int) -> int:
        """Clear the full arguments of every action that settled before the cutoff.

        Clearing is the only change `pending_action_args` accepts. The frozen row, its
        outcome, its decisions and its audit records are not touched."""
        statuses = sorted(s.value for s in SETTLED)
        marks = ", ".join("?" for _ in statuses)
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE pending_action_args SET args_json = NULL, detail = NULL,"  # noqa: S608
                " cleared_at = ?"
                " WHERE args_json IS NOT NULL AND pending_id IN ("
                f"  SELECT id FROM pending_actions WHERE status IN ({marks})"
                "   AND updated_at <= ?)",
                (now_ms, *statuses, settled_before_ms),
            )
            return int(cur.rowcount or 0)

    async def recent(
        self, owner_id: str, *, since_ms: int, limit: int, words: Sequence[str] = ()
    ) -> list[PendingAction]:
        """The owner's actions proposed since a moment, newest first (SPN-17).

        `words` narrows them to those whose summary contains any one of the words,
        ignoring case. Each is matched as plain text: no character is a wildcard."""
        marks = " OR ".join("p.summary LIKE ? ESCAPE '\\'" for _ in words)
        patterns = [
            "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            for w in words
        ]
        async with self._conn.execute(
            f"{_SELECT_PENDING} WHERE p.owner_id = ? AND p.created_at >= ?"
            + (f" AND ({marks})" if words else "")
            + " ORDER BY p.created_at DESC, p.id LIMIT ?",
            (owner_id, since_ms, *patterns, limit),
        ) as cur:
            return [_row_to_pending(r) for r in await cur.fetchall()]

    async def confirmations(
        self,
        owner_id: str,
        *,
        since_ms: int,
        now_ms: int,
        lifecycle: str | None = None,
        limit: int,
    ) -> list[tuple[str, PendingAction, DeliveryRow | None, list[DecisionRow]]]:
        """The owner's actions proposed since a moment, newest first, for the
        Confirmations page (SUR-7): each with what the user reads its status as, when
        its card was shown, and its decisions in order. `lifecycle` keeps only one
        status. The arguments are the frozen slim copy, never the full ones."""
        async with self._conn.execute(
            f"SELECT * FROM ({_SELECT_SLIM}) WHERE (? IS NULL OR lifecycle = ?)"  # noqa: S608
            " ORDER BY created_at DESC, id LIMIT ?",
            (now_ms, owner_id, since_ms, lifecycle, lifecycle, limit),
        ) as cur:
            rows = [(str(r["lifecycle"]), _row_to_pending(r)) for r in await cur.fetchall()]
        if not rows:
            return []
        ids = [a.id for _, a in rows]
        # One placeholder per id: the statement's text never holds a value.
        marks = ", ".join("?" for _ in ids)
        delivered: dict[str, DeliveryRow] = {}
        async with self._conn.execute(
            "SELECT pending_id, surface, delivered_at FROM decision_deliveries"  # noqa: S608
            f" WHERE pending_id IN ({marks})",
            ids,
        ) as cur:
            for r in await cur.fetchall():
                delivered[str(r["pending_id"])] = DeliveryRow(
                    pending_id=str(r["pending_id"]),
                    surface=str(r["surface"]),
                    delivered_at=int(r["delivered_at"]),
                )
        decided: dict[str, list[DecisionRow]] = {i: [] for i in ids}
        async with self._conn.execute(
            "SELECT pending_id, event, decided_by, via, at FROM decisions"  # noqa: S608
            f" WHERE pending_id IN ({marks}) ORDER BY id",
            ids,
        ) as cur:
            for r in await cur.fetchall():
                decided[str(r["pending_id"])].append(
                    DecisionRow(
                        pending_id=str(r["pending_id"]),
                        event=str(r["event"]),
                        decided_by=str(r["decided_by"]),
                        via=str(r["via"]),
                        at=int(r["at"]),
                    )
                )
        return [(life, a, delivered.get(a.id), decided[a.id]) for life, a in rows]

    async def confirmation_counts(
        self, owner_id: str, *, since_ms: int, now_ms: int
    ) -> dict[str, int]:
        """How many of the owner's actions since a moment are in each status, as the
        user reads it (SUR-7)."""
        async with self._conn.execute(
            f"SELECT lifecycle, COUNT(*) AS n FROM ({_SELECT_SLIM}) GROUP BY lifecycle",  # noqa: S608
            (now_ms, owner_id, since_ms),
        ) as cur:
            return {str(r["lifecycle"]): int(r["n"]) for r in await cur.fetchall()}

    async def last_decisions_since(self, events: Sequence[str], since_ms: int) -> list[DecisionRow]:
        # One placeholder per event: the statement's text never holds a value.
        marks = ", ".join("?" for _ in events)
        async with self._conn.execute(
            "SELECT d.pending_id, d.event, d.decided_by, d.via, d.at FROM decisions d"  # noqa: S608
            f" WHERE d.event IN ({marks}) AND d.at >= ? AND d.id ="
            " (SELECT MAX(id) FROM decisions WHERE pending_id = d.pending_id) ORDER BY d.id",
            (*events, since_ms),
        ) as cur:
            rows = await cur.fetchall()
            return [
                DecisionRow(
                    pending_id=str(r["pending_id"]),
                    event=str(r["event"]),
                    decided_by=str(r["decided_by"]),
                    via=str(r["via"]),
                    at=int(r["at"]),
                )
                for r in rows
            ]

    async def list_decisions(self, pending_id: str) -> list[DecisionRow]:
        async with self._conn.execute(
            "SELECT pending_id, event, decided_by, via, at FROM decisions"
            " WHERE pending_id = ? ORDER BY id",
            (pending_id,),
        ) as cur:
            return [
                DecisionRow(
                    pending_id=str(r["pending_id"]),
                    event=str(r["event"]),
                    decided_by=str(r["decided_by"]),
                    via=str(r["via"]),
                    at=int(r["at"]),
                )
                for r in await cur.fetchall()
            ]

    async def mark_delivered(self, pending_id: str, owner_id: str, surface: str, at: int) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "INSERT OR IGNORE INTO decision_deliveries"
                " (pending_id, owner_id, surface, delivered_at) VALUES (?, ?, ?, ?)",
                (pending_id, owner_id, surface, at),
            )
            return cur.rowcount == 1

    async def get_delivery(self, pending_id: str) -> DeliveryRow | None:
        async with self._conn.execute(
            "SELECT pending_id, surface, delivered_at FROM decision_deliveries"
            " WHERE pending_id = ?",
            (pending_id,),
        ) as cur:
            r = await cur.fetchone()
        if r is None:
            return None
        return DeliveryRow(
            pending_id=str(r["pending_id"]),
            surface=str(r["surface"]),
            delivered_at=int(r["delivered_at"]),
        )

    async def append_audit(self, r: AuditRecord) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO tool_audit (owner_id, call_id, tool, args_hash, risk, decision,"
                " outcome, duration_ms, session_id, at, neutralized)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.owner_id,
                    r.call_id,
                    r.tool,
                    r.args_hash,
                    r.risk,
                    r.decision,
                    r.outcome,
                    r.duration_ms,
                    r.session_id,
                    r.at,
                    r.neutralized,
                ),
            )

    async def append_rule_event(self, owner_id: str, e: RuleEvent) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO rule_events (owner_id, rule_id, text, event, how, session_id, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (owner_id, e.rule_id, e.text, e.event, e.how, e.session_id, e.at),
            )

    async def rule_events(
        self, owner_id: str, *, since_ms: int, limit: int, words: Sequence[str] = ()
    ) -> list[RuleEvent]:
        """The owner's rules kept and dropped since a moment, newest first.

        `words` narrows them as `recent` does, against the rule's text."""
        marks = " OR ".join("text LIKE ? ESCAPE '\\'" for _ in words)
        patterns = [
            "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            for w in words
        ]
        # Built only from constant pieces; every word is bound as a parameter.
        async with self._conn.execute(
            "SELECT rule_id, text, event, how, session_id, at FROM rule_events"  # noqa: S608
            " WHERE owner_id = ? AND at >= ?"
            + (f" AND ({marks})" if words else "")
            + " ORDER BY at DESC, id DESC LIMIT ?",
            (owner_id, since_ms, *patterns, limit),
        ) as cur:
            return [
                RuleEvent(
                    rule_id=str(r["rule_id"]),
                    text=str(r["text"]),
                    event="dropped" if r["event"] == "dropped" else "kept",
                    how="said" if r["how"] == "said" else "card",
                    session_id=str(r["session_id"]),
                    at=int(r["at"]),
                )
                for r in await cur.fetchall()
            ]

    async def recent_tools(self, owner_id: str, since_ms: int) -> list[tuple[str, str, int]]:
        """(tool, session id, at) of the owner's calls since a moment that the router let
        through and ran. Newest first.

        The audit has no index on its time, and needs none: rows are appended as calls
        happen, so it is read back from the newest and stops at the first one older."""
        found: list[tuple[str, str, int]] = []
        async with self._conn.execute(
            "SELECT owner_id, tool, decision, session_id, at FROM tool_audit ORDER BY id DESC"
        ) as cur:
            while rows := await cur.fetchmany(256):
                for r in rows:
                    at = int(r["at"])
                    if at < since_ms:
                        return found
                    if r["owner_id"] == owner_id and r["decision"] in _ALLOWED:
                        found.append((str(r["tool"]), str(r["session_id"]), at))
        return found

    async def list_audit(self) -> list[AuditRecord]:
        async with self._conn.execute(
            "SELECT owner_id, call_id, tool, args_hash, risk, decision, outcome, duration_ms,"
            " session_id, at, neutralized FROM tool_audit ORDER BY id"
        ) as cur:
            return [
                AuditRecord(
                    owner_id=str(r["owner_id"]),
                    call_id=str(r["call_id"]),
                    tool=str(r["tool"]),
                    args_hash=str(r["args_hash"]),
                    risk=str(r["risk"]),
                    decision=str(r["decision"]),
                    outcome=str(r["outcome"]),
                    duration_ms=int(r["duration_ms"]),
                    session_id=str(r["session_id"]),
                    at=int(r["at"]),
                    neutralized=None if r["neutralized"] is None else str(r["neutralized"]),
                )
                for r in await cur.fetchall()
            ]
