"""A helper as the panel shows it (the helpers panel spec, What a helper is)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

HelperKind = Literal["routine", "ask", "watcher"]
# Paused and capped helpers sit in Idle on the page, saying so.
HelperState = Literal["needs_you", "working", "idle", "paused", "capped"]
# `nothing`: a run that had nothing for the user (AGT-6).
Outcome = Literal["succeeded", "skipped", "failed", "nothing"]
WaitingType = Literal["approval", "question", "step"]


@dataclass(frozen=True, kw_only=True)
class LastRun:
    at_ms: int
    outcome: Outcome
    # Why it failed or was skipped, in the run log's short words; None otherwise.
    reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {"at_ms": self.at_ms, "outcome": self.outcome, "reason": self.reason}


@dataclass(frozen=True, kw_only=True)
class Waiting:
    """Something from this helper that needs the user: a card, a question or a step."""

    type: WaitingType
    id: str
    text: str
    # When it began waiting, when that is known: Needs you lists the oldest first.
    at_ms: int | None

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "text": self.text, "at_ms": self.at_ms}


@dataclass(frozen=True, kw_only=True)
class Helper:
    # `routine:<schedule_id>`, `ask:<schedule_id>` or `watcher:<name>`.
    id: str
    kind: HelperKind
    name: str
    job: str
    state: HelperState
    # A short line while working, else None.
    doing: str | None
    # When the current run began, while working.
    since_ms: int | None
    last_run: LastRun | None
    # Its next scheduled time; a watcher's next check.
    next_ms: int | None
    waiting: tuple[Waiting, ...]
    # The conversation "Talk to it" opens, when one exists yet.
    conversation_id: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "job": self.job,
            "state": self.state,
            "doing": self.doing,
            "since_ms": self.since_ms,
            "last_run": self.last_run.to_json() if self.last_run is not None else None,
            "next_ms": self.next_ms,
            "waiting": [w.to_json() for w in self.waiting],
            "conversation_id": self.conversation_id,
        }


def to_json(helpers: Sequence[Helper]) -> dict[str, Any]:
    """The list as `GET /api/helpers` and `helpers.changed` carry it. `needs_you` counts
    helpers, not the things they wait on."""
    return {
        "helpers": [h.to_json() for h in helpers],
        "needs_you": sum(1 for h in helpers if h.state == "needs_you"),
    }
