"""SPN-2: the pending-action lifecycle, as a pure decision function.

The router persists what `decide` returns. Nothing here does I/O, so every
combination of state, event and context can be tested exhaustively.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from trillion.spine.contract import Risk, ToolContext, ToolDefinition

EXPIRY_MS = 10 * 60 * 1000

Event = Literal["confirm", "cancel"]


class Status(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


FINAL: frozenset[Status] = frozenset(
    {Status.SUCCEEDED, Status.FAILED, Status.UNKNOWN, Status.REJECTED}
)
REPRESENTABLE: frozenset[Status] = frozenset({Status.EXPIRED, Status.INVALIDATED})


class Refusal(StrEnum):
    NOT_FOUND = "not_found"
    WRONG_SESSION = "wrong_session"
    SAME_TURN = "same_turn"
    CHANNEL = "channel"
    TAP_REQUIRED = "tap_required"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    ALREADY_RESOLVED = "already_resolved"
    NOT_REPRESENTABLE = "not_representable"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGS = "invalid_args"
    DENIED_BY_FLOOR = "denied_by_floor"
    LOOP_GUARD = "loop_guard"
    PRECONDITION_FAILED = "precondition_failed"
    PRECONDITION_CHANGED = "precondition_changed"
    # The card was never shown on a screen, so the user cannot have seen what it says.
    NOT_SHOWN = "not_shown"
    # Nobody by that owner id exists here. Refused before anything else happens (#4).
    UNKNOWN_OWNER = "unknown_owner"
    # A timed ask running unattended: nobody is there to see a card or answer one (D5).
    UNATTENDED = "unattended"
    # A tap on a widget's ask: she wrote its words, maybe from a page she read.
    ASK = "ask"


@dataclass(frozen=True)
class Rejection:
    reason: Refusal
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class PendingAction:
    id: str
    owner_id: str
    tool: str
    args: dict[str, Any]
    args_hash: str
    summary: str
    target: str
    risk: Risk
    recoverable: bool
    session_id: str
    created_turn: int
    status: Status
    created_at: int
    expires_at: int
    represented_from: str | None
    effect_status: str | None = None
    outcome_detail: str | None = None
    precondition_token: str | None = None
    # When the row last changed. None for an action not yet saved.
    updated_at: int | None = None
    # True once retention has cleared the full arguments (#20): `args` is then the slim
    # copy, with long values replaced by their fingerprints, and the action cannot run.
    args_cleared: bool = False


_STATUS_REFUSAL = {
    Status.EXPIRED: Refusal.EXPIRED,
    Status.SUPERSEDED: Refusal.SUPERSEDED,
    Status.INVALIDATED: Refusal.PRECONDITION_CHANGED,
}


def decide(a: PendingAction, event: Event, ctx: ToolContext, now_ms: int) -> Status | Rejection:
    """Return the next status for `a`, or why the event is refused."""
    if a.owner_id != ctx.owner_id:
        return Rejection(Refusal.NOT_FOUND)
    if a.session_id != ctx.session_id:
        return Rejection(Refusal.WRONG_SESSION, "only the session that raised it can resolve it")
    if a.status is not Status.PROPOSED:
        return Rejection(_STATUS_REFUSAL.get(a.status, Refusal.ALREADY_RESOLVED), a.status.value)
    if now_ms >= a.expires_at:
        return Rejection(Refusal.EXPIRED, "re-present it to ask again")
    if ctx.via in ("schedule", "refresh", "routine"):
        # Nobody is there to say yes or no. The router refuses these first; this is the
        # same rule where a decision is made, should another caller ever reach it.
        return Rejection(Refusal.UNATTENDED, "nobody is here to answer it")
    if event == "cancel":
        return Status.REJECTED
    if ctx.via == "ask":
        return Rejection(Refusal.ASK, "a tap on a widget can't approve; use the card")
    if ctx.via == "channel":
        return Rejection(Refusal.CHANNEL, "a channel message can never close a gate")
    if ctx.turn <= a.created_turn:
        return Rejection(Refusal.SAME_TURN, "ask the user; confirm on their next turn")
    if (a.risk is Risk.SPEND or not a.recoverable) and ctx.via != "tap":
        return Rejection(Refusal.TAP_REQUIRED, "this action needs a tap on the confirmation card")
    return Status.APPROVED


_ID_ONLY = {
    "type": "object",
    "properties": {"pending_id": {"type": "string", "minLength": 1}},
    "required": ["pending_id"],
    "additionalProperties": False,
}

CONFIRM_TOOL = ToolDefinition(
    name="confirm_pending_action",
    description=(
        "Execute the pending action the user just approved, on a turn after you proposed it. "
        "It runs exactly what was proposed; it takes no other arguments."
    ),
    input_schema=_ID_ONLY,
    risk=Risk.WRITE,
    category="meta",
    timeout_s=60.0,
    trusted_output=True,
)

CANCEL_TOOL = ToolDefinition(
    name="cancel_pending_action",
    description="Discard a pending action the user declined.",
    input_schema=_ID_ONLY,
    risk=Risk.WRITE,
    category="meta",
    timeout_s=5.0,
    trusted_output=True,
)

REPRESENT_TOOL = ToolDefinition(
    name="represent_pending_action",
    description=(
        "Ask again for an action that expired, was invalidated, or that a check after a "
        "crash proved never went out. It shows a new card, which the user must approve like "
        "any other; it never runs anything. Call it only when the user asks for that action "
        "again, and find its pending_id with decision_record first."
    ),
    input_schema=_ID_ONLY,
    risk=Risk.WRITE,
    category="meta",
    timeout_s=30.0,
    trusted_output=True,
)

META_TOOLS: dict[str, ToolDefinition] = {
    CONFIRM_TOOL.name: CONFIRM_TOOL,
    CANCEL_TOOL.name: CANCEL_TOOL,
    REPRESENT_TOOL.name: REPRESENT_TOOL,
}
