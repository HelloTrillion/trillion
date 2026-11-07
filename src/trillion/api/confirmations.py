"""Settings → Confirmations (SUR-7): the decision record, on a page.

Every action that asked for the user's yes, newest first: what it was, when its card
was shown, who answered and how, and the outcome, each read from the record (SPN-17,
SPN-17a). "Did that ever reach me?" is answered here without her.

It only reads. There is one route, a GET, behind the session token like every /api/
route (SPN-11). Nothing here can approve, decline or retry anything: a card is
answered on the card, in the conversation that raised it (SPN-14). The name and the
summary are the host's (SPN-2a), and the arguments are the frozen slim copy, never
the full ones kept for thirty days (SPN-17c).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from trillion.api.runtime import OWNER, Runtime
from trillion.api.session import ROUTINE_LABEL
from trillion.spine import labels
from trillion.spine.confirm import FINAL, PendingAction, Status
from trillion.spine.retention import slim
from trillion.spine.store import DecisionRow, DeliveryRow
from trillion.tools.record import NOT_SENT
from trillion.tools.schedule import ASK as ASK_SCHEDULE

DAY_MS = 86_400_000
DEFAULT_DAYS, MAX_DAYS = 30, 365
DEFAULT_LIMIT, MAX_LIMIT = 200, 500
# The longest recorded reason sent: enough to say why, never a whole tool result.
DETAIL_LIMIT = 300

# What the user reads an action's status as, in the order the page offers them.
LIFECYCLE = (
    "pending",
    "approved",
    "executing",
    "succeeded",
    "failed",
    "unknown",
    "rejected",
    "expired",
    "cancelled",
)
Filter = Literal[
    "all",
    "pending",
    "approved",
    "executing",
    "succeeded",
    "failed",
    "unknown",
    "rejected",
    "expired",
    "cancelled",
]
# The ones a page may tell the user why they did not happen, or did not finish.
_WITH_DETAIL = frozenset({Status.FAILED, Status.UNKNOWN, Status.INVALIDATED})


def why_cancelled(a: PendingAction, decisions: list[DecisionRow]) -> str | None:
    """Why an action that never ran was cancelled, as a code the page words."""
    if a.status is Status.SUPERSEDED:
        return "superseded"
    if a.status is Status.INVALIDATED:
        return "not_sent" if decisions and decisions[-1].event == NOT_SENT else "changed"
    return None


def answered(a: PendingAction, decisions: list[DecisionRow]) -> DecisionRow | None:
    """The decision that moved it on from waiting: the first after its proposal."""
    return next((d for d in decisions if d.event != "proposed"), None)


def who(a: PendingAction, d: DecisionRow) -> str:
    """Who decided: the conversation that asked, the app itself, or another session."""
    if d.decided_by == "system":
        return "system"
    return "asking_conversation" if d.decided_by == a.session_id else "another_session"


def row(
    rt: Runtime,
    life: str,
    a: PendingAction,
    delivery: DeliveryRow | None,
    decisions: list[DecisionRow],
) -> dict[str, Any]:
    reg = rt.registry
    label = labels.tool_label(a.tool, labels.connected(reg.definition(a.tool), reg.get(a.tool)))
    if a.tool == ASK_SCHEDULE and a.args.get("routine") in ("day", "own"):
        # A routine, not a one-off, as its card said.
        label = ROUTINE_LABEL
    decided = answered(a, decisions)
    finished = decisions[-1] if a.status in FINAL and decisions else None
    detail = (a.outcome_detail or "")[:DETAIL_LIMIT] if a.status in _WITH_DETAIL else ""
    return {
        "id": a.id,
        "tool": a.tool,
        # Both from the host: the friendly name and the frozen summary (SPN-2a).
        "tool_label": label,
        "summary": a.summary,
        "reason": labels.reason(a.risk, a.recoverable),
        "status": life,
        "created_at": a.created_at,
        "expires_at": a.expires_at,
        # Written once, when its card was on a screen (SPN-17); null if it never was.
        "delivered_at": delivery.delivered_at if delivery is not None else None,
        "surface": delivery.surface if delivery is not None else None,
        "decided_at": decided.at if decided is not None else None,
        "decided_by": decided.decided_by if decided is not None else None,
        "decided_by_kind": who(a, decided) if decided is not None else None,
        "decision": decided.event if decided is not None else None,
        "channel": decided.via if decided is not None else None,
        "finished_at": finished.at if finished is not None else None,
        "effect_status": a.effect_status,
        "detail": detail,
        "cancelled_reason": why_cancelled(a, decisions),
        # The conversation that raised it, where its card is: set for one still waiting.
        "conversation_id": None,
        "args_hash": a.args_hash,
        # The frozen slim copy, slimmed again for a row written before #20.
        "args": slim(a.args),
        "history": [
            {"event": d.event, "at": d.at, "channel": d.via, "by": who(a, d)} for d in decisions
        ],
    }


def create_confirmations_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/api/confirmations")
    async def read(
        status: Filter = "all",
        days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = DEFAULT_DAYS,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        now = rt.clock()
        since = now - days * DAY_MS
        store = rt.spine_store
        found = await store.confirmations(
            OWNER,
            since_ms=since,
            now_ms=now,
            lifecycle=None if status == "all" else status,
            limit=limit,
        )
        counts = await store.confirmation_counts(OWNER, since_ms=since, now_ms=now)
        actions = []
        for life, a, delivery, decisions in found:
            r = row(rt, life, a, delivery, decisions)
            if life == "pending" and await rt.conversations.exists(a.session_id, OWNER):
                r["conversation_id"] = a.session_id
            actions.append(r)
        return {
            "now_ms": now,
            "days": days,
            "status": status,
            "counts": {k: counts.get(k, 0) for k in LIFECYCLE} | {"all": sum(counts.values())},
            "actions": actions,
        }

    return router


# Mounted by `trillion.api.routes.plugged`: nothing to add to app.py.
ROUTES = (create_confirmations_router,)
