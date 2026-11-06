"""A helper as the panel shows it (the helpers panel spec, What a helper is)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

# `own`: a helper the user made (the your helpers spec).
HelperKind = Literal["routine", "ask", "watcher", "own"]
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
class RoutineLine:
    """One of a helper's routines, listed under it rather than as a row of its own."""

    # Its schedule's id: the routine routes act on it.
    id: str
    name: str
    # None while it, or its helper, is paused.
    next_ms: int | None
    paused: bool

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "next_ms": self.next_ms, "paused": self.paused}


@dataclass(frozen=True, kw_only=True)
class Helper:
    # `routine:<schedule_id>`, `ask:<schedule_id>`, `watcher:<name>` or `own:<id>`.
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
    # What the page acts on, said outright rather than read from `state`: a routine
    # the user paused, or a helper of theirs (Resume, not Pause); a watcher switched
    # on, and its source connected (Turn on, or Connect). None where they don't apply.
    paused: bool = False
    switched_on: bool | None = None
    connected: bool | None = None
    # A helper of the user's: what it has learned, and its routines. None and empty
    # for any other kind.
    notes: str | None = None
    routines: tuple[RoutineLine, ...] = ()
    # A helper of the user's: its picture (`helpers/look.py`). None for any other kind.
    icon: str | None = None
    color: str | None = None

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
            "paused": self.paused,
            "switched_on": self.switched_on,
            "connected": self.connected,
            "notes": self.notes,
            "routines": [r.to_json() for r in self.routines],
            "icon": self.icon,
            "color": self.color,
        }


def to_json(helpers: Sequence[Helper]) -> dict[str, Any]:
    """The list as `GET /api/helpers` and `helpers.changed` carry it. `needs_you` counts
    helpers, not the things they wait on."""
    return {
        "helpers": [h.to_json() for h in helpers],
        "needs_you": sum(1 for h in helpers if h.state == "needs_you"),
    }
