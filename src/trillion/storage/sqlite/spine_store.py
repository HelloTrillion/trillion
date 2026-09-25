"""SpineStore on SQLite. One lock serializes writes so compare-and-set is exact."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from trillion.spine.audit import AuditRecord
from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.spine.hashing import canonical_json
from trillion.spine.store import DecisionIn, DecisionRow, DeliveryRow

_PENDING_COLUMNS = (
    "id, owner_id, tool, args_json, args_hash, summary, target, risk, recoverable, session_id,"
    " created_turn, status, expires_at, represented_from, effect_status, outcome_detail,"
    " created_at, updated_at, precondition_token"
)


def _row_to_pending(r: aiosqlite.Row) -> PendingAction:
    args: dict[str, Any] = json.loads(str(r["args_json"]))
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
    )


class SqliteSpineStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                await self._conn.execute("ROLLBACK")
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

    async def insert_pending(self, a: PendingAction, decision: DecisionIn) -> None:
        async with self._tx() as conn:
            await conn.execute(
                f"INSERT INTO pending_actions ({_PENDING_COLUMNS})"  # noqa: S608
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    a.id,
                    a.owner_id,
                    a.tool,
                    canonical_json(a.args),
                    a.args_hash,
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
            await self._decision(conn, a.owner_id, a.id, decision, a.created_at)

    async def get_pending(self, pending_id: str) -> PendingAction | None:
        async with self._conn.execute(
            f"SELECT {_PENDING_COLUMNS} FROM pending_actions WHERE id = ?",  # noqa: S608
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
            ) as c2:
                row = await c2.fetchone()
            if row is None:  # pragma: no cover - the UPDATE above just matched this row
                raise RuntimeError(f"pending action {pending_id!r} vanished mid-transaction")
            await self._decision(conn, str(row["owner_id"]), pending_id, decision, now_ms)
            return True

    async def open_for_target(
        self, owner_id: str, session_id: str, tool: str, target: str
    ) -> list[PendingAction]:
        async with self._conn.execute(
            f"SELECT {_PENDING_COLUMNS} FROM pending_actions"  # noqa: S608
            " WHERE owner_id = ? AND session_id = ? AND tool = ? AND target = ? AND status = ?",
            (owner_id, session_id, tool, target, Status.PROPOSED.value),
        ) as cur:
            return [_row_to_pending(r) for r in await cur.fetchall()]

    async def list_by_status(self, status: Status) -> list[PendingAction]:
        async with self._conn.execute(
            f"SELECT {_PENDING_COLUMNS} FROM pending_actions WHERE status = ?",  # noqa: S608
            (status.value,),
        ) as cur:
            return [_row_to_pending(r) for r in await cur.fetchall()]

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
                " outcome, duration_ms, session_id, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )

    async def list_audit(self) -> list[AuditRecord]:
        async with self._conn.execute(
            "SELECT owner_id, call_id, tool, args_hash, risk, decision, outcome, duration_ms,"
            " session_id, at FROM tool_audit ORDER BY id"
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
                )
                for r in await cur.fetchall()
            ]
