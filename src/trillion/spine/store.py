"""What the spine needs from storage. The router depends on this, never on SQLite."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from trillion.spine.audit import AuditRecord
from trillion.spine.confirm import PendingAction, Status

# The decision event the user's Dismiss on an unseen decision appends (SPN-17).
DISMISSED = "dismissed"


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
    async def insert_pending(
        self, a: PendingAction, decision: DecisionIn, *, supersede: DecisionIn | None = None
    ) -> list[str]: ...

    async def owner_known(self, owner_id: str) -> bool: ...

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

    async def list_represented_from(self, pending_id: str) -> list[PendingAction]:
        """The actions re-presented from this one (SPN-2b)."""
        ...

    async def last_decisions_since(self, events: Sequence[str], since_ms: int) -> list[DecisionRow]:
        """Each action whose latest decision is one of `events`, made at or after
        `since_ms`: that decision."""
        ...

    async def missed(
        self, owner_id: str, *, since_ms: int, now_ms: int, limit: int
    ) -> list[PendingAction]:
        """The owner's unseen decisions (SPN-17): each action that expired unanswered,
        at or after `since_ms`, and was neither asked for again nor dismissed. Newest
        first. One still `proposed` past its expiry counts as expired."""
        ...

    async def dismiss(self, pending_id: str, owner_id: str, decision: DecisionIn, at: int) -> bool:
        """Record that the user dismissed an expired, unanswered action from Needs you:
        a decision appended, once, while it is still one. False when it is not."""
        ...

    async def clear_settled_args(self, *, settled_before_ms: int, now_ms: int) -> int:
        """Clear the full arguments of actions that settled before the cutoff (#20)."""
        ...

    async def list_decisions(self, pending_id: str) -> list[DecisionRow]: ...

    async def mark_delivered(
        self, pending_id: str, owner_id: str, surface: str, at: int
    ) -> bool: ...

    async def get_delivery(self, pending_id: str) -> DeliveryRow | None: ...

    async def append_audit(self, r: AuditRecord) -> None: ...

    async def list_audit(self) -> list[AuditRecord]: ...
