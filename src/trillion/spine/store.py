"""What the spine needs from storage. The router depends on this, never on SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trillion.spine.audit import AuditRecord
from trillion.spine.confirm import PendingAction, Status


@dataclass(frozen=True, kw_only=True)
class DecisionIn:
    event: str
    decided_by: str
    via: str


@dataclass(frozen=True, kw_only=True)
class DecisionRow:
    pending_id: str
    event: str
    decided_by: str
    via: str
    at: int


@dataclass(frozen=True, kw_only=True)
class DeliveryRow:
    pending_id: str
    surface: str
    delivered_at: int


class SpineStore(Protocol):
    async def insert_pending(self, a: PendingAction, decision: DecisionIn) -> None: ...

    async def get_pending(self, pending_id: str) -> PendingAction | None: ...

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
        """Compare-and-set the status and append the decision, atomically."""
        ...

    async def open_for_target(
        self, owner_id: str, session_id: str, tool: str, target: str
    ) -> list[PendingAction]: ...

    async def list_by_status(self, status: Status) -> list[PendingAction]: ...

    async def list_decisions(self, pending_id: str) -> list[DecisionRow]: ...

    async def mark_delivered(
        self, pending_id: str, owner_id: str, surface: str, at: int
    ) -> bool: ...

    async def get_delivery(self, pending_id: str) -> DeliveryRow | None: ...

    async def append_audit(self, r: AuditRecord) -> None: ...

    async def list_audit(self) -> list[AuditRecord]: ...
