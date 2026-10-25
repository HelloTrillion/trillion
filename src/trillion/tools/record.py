"""The decision record, read back (SPN-17): what happened to each action that asked first.

She answers "did that ever reach me?" from here, not from the conversation, which
may have been summarized, cleared or never seen. Every line comes from the
database: when the card was proposed, whether it was ever shown on a screen, who
answered and how, and the outcome as the spine recorded it.

It only reads. The summaries can quote what a file or a page said, so the result
reaches the model as untrusted text like any other tool result.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import (
    JSON,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.store import DecisionRow, DeliveryRow

RECORD = "decision_record"
DAY_MS = 86_400_000
DEFAULT_DAYS, MAX_DAYS = 30, 365
DEFAULT_LIMIT, MAX_LIMIT = 10, 25
# Words too common to narrow anything: "the file I asked for" should find the file.
_SKIP = frozenset(
    "the and for was did that this with from file files have has any ever get got "
    "asked ask about into onto your you".split()
)
_WORD = re.compile(r"[^\W_]+(?:[._-][^\W_]+)*")


def words(about: str) -> list[str]:
    """The words worth matching: three letters or more, not a filler word, at most eight."""
    found = [w for w in _WORD.findall(about.lower()) if len(w) >= 3 and w not in _SKIP]
    return list(dict.fromkeys(found))[:8]


# How a decision arrived, in the words she should use.
_VIA = {
    "tap": "a tap on the card",
    "voice": "spoken",
    "text": "typed",
    "channel": "a message",
    "ask": "a tap on a widget",
}
_OUTCOME = {
    Status.SUCCEEDED: "done",
    Status.FAILED: "failed",
    Status.UNKNOWN: "unknown: it may or may not have happened",
    Status.REJECTED: "declined, so nothing was done",
    Status.SUPERSEDED: "replaced by a newer request before anyone answered",
    Status.EXPIRED: "expired without an answer, so nothing was done",
    Status.INVALIDATED: "cancelled because what it would change had changed",
    Status.APPROVED: "approved, not yet started",
    Status.EXECUTING: "approved and running",
}


class Record(Protocol):
    async def recent(
        self, owner_id: str, *, since_ms: int, limit: int, words: Sequence[str] = ()
    ) -> list[PendingAction]: ...

    async def list_decisions(self, pending_id: str) -> list[DecisionRow]: ...

    async def get_delivery(self, pending_id: str) -> DeliveryRow | None: ...


def _when(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%a %d %b %Y %H:%M")


def describe(
    a: PendingAction, delivery: DeliveryRow | None, decisions: list[DecisionRow], now_ms: int
) -> str:
    """One action as a short paragraph of facts, each one read from the record."""
    lines = [f"{a.id}: {a.summary}", f"  asked: {_when(a.created_at)}"]
    lines.append(
        f"  shown: on screen at {_when(delivery.delivered_at)}"
        if delivery is not None
        else "  shown: never; the card was not displayed on any screen"
    )
    answer = next((d for d in decisions if d.event in ("approved", "rejected")), None)
    if answer is not None:
        how = _VIA.get(answer.via, answer.via)
        verb = "approved" if answer.event == "approved" else "declined"
        lines.append(f"  answer: {verb} ({how}) at {_when(answer.at)}")
    elif any(d.event == "cancelled" for d in decisions):
        lines.append("  answer: withdrawn before anyone answered")
    else:
        lines.append("  answer: none")
    if a.status is Status.PROPOSED and a.expires_at <= now_ms:
        outcome = "expired without an answer, so nothing was done"
    elif a.status is Status.PROPOSED and delivery is None:
        # Never "waiting for your yes" about a card nobody has seen (SPN-17).
        outcome = "not answered, and the user has not seen it: its card was never shown"
    elif a.status is Status.PROPOSED:
        outcome = "waiting for an answer"
    else:
        outcome = _OUTCOME.get(a.status, a.status.value)
    if a.outcome_detail and a.status in (Status.FAILED, Status.UNKNOWN, Status.INVALIDATED):
        outcome += f" ({a.outcome_detail})"
    lines.append(f"  outcome: {outcome}")
    return "\n".join(lines)


class DecisionRecord(BaseTool):
    definition = ToolDefinition(
        name=RECORD,
        description=(
            "Look up what happened to actions that needed the user's yes: what was asked, "
            "whether the card was ever shown, who answered and how, and the outcome. It reads "
            "the app's permanent record, not the conversation, so use it whenever the user asks "
            "whether something was approved, done, seen or is still waiting, even if this "
            "conversation never mentioned it. Newest first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "about": {
                    "type": "string",
                    "maxLength": 100,
                    "description": (
                        "Words from what was asked, such as a file name. Any one of them "
                        "matching is enough. Leave it out to see everything recent."
                    ),
                },
                "days": {"type": "integer", "minimum": 1, "maximum": MAX_DAYS},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "additionalProperties": False,
        },
        risk=Risk.READ,
        category="record",
        timeout_s=10.0,
    )

    def __init__(self) -> None:
        self._record: Record | None = None
        self._owner = ""
        self._clock: Callable[[], int] | None = None

    def bind(self, record: Record, owner_id: str, clock: Callable[[], int]) -> None:
        """Called once at startup with the running process's store."""
        self._record, self._owner, self._clock = record, owner_id, clock

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        record, clock = self._record, self._clock
        if record is None or clock is None or ctx.owner_id != self._owner:
            return ToolResult(
                ok=False, content="the record is not available", effect="none", error_kind="disk"
            )
        days = min(int(args.get("days") or DEFAULT_DAYS), MAX_DAYS)
        limit = min(int(args.get("limit") or DEFAULT_LIMIT), MAX_LIMIT)
        about = " ".join(str(args.get("about") or "").split())[:100]
        now = clock()
        found = await record.recent(
            ctx.owner_id, since_ms=now - days * DAY_MS, limit=limit, words=words(about)
        )
        if not found:
            narrowed = f" matching {about!r}" if about else ""
            return ToolResult(
                ok=True,
                content=f"no action needed an answer in the last {days} days{narrowed}",
                effect="none",
            )
        parts = [
            describe(a, await record.get_delivery(a.id), await record.list_decisions(a.id), now)
            for a in found
        ]
        return ToolResult(ok=True, content="\n\n".join(parts), effect="none")
