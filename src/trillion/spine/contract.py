"""The tool contract (SPN-1). `risk` has no default: a tool must say what it can do."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

JSON = Mapping[str, Any]
# "schedule" is a timed ask running with nobody there (AGT-9): it can never raise or
# settle a card. "ask" is a turn started by tapping a widget's ask: typed words in
# every way but one, they can never approve (its words may come from a page she read).
# "refresh" replays a live widget's reads with nobody there: reads only, never a card.
# "routine" is a routine's run, with nobody there: all a timed ask may do, and its board
# on its own conversation (`ToolContext.routine_target`).
Via = Literal["voice", "text", "tap", "channel", "schedule", "ask", "refresh", "routine"]
Effect = Literal["confirmed", "none", "unknown"]
# Whose voice said a spoken turn, when an owner's voice is enrolled (§5.3a): theirs,
# not sure (between the thresholds, or it could not be scored), someone else's, or a
# recording of something already heard. Set by the session from the speaker check.
Speaker = Literal["owner", "unsure", "other", "replay"]


class Risk(StrEnum):
    READ = "read"
    WRITE = "write"
    IRREVERSIBLE = "irreversible"
    SPEND = "spend"


GATED: frozenset[Risk] = frozenset({Risk.IRREVERSIBLE, Risk.SPEND})


@dataclass(frozen=True, kw_only=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: JSON
    risk: Risk
    category: str
    timeout_s: float
    path_args: tuple[str, ...] = ()
    # Arguments that may name a path, but not one confined to the granted folders: a
    # connected server's own files, or an id that only looks like a path. Any value
    # that reads as a path is still refused if it touches anything protected (SEC-10).
    outside_path_args: tuple[str, ...] = ()
    # Whether a tool that is not a read changes what its paths name, so a `read` share
    # refuses it (D11). Only a first-party tool that names a folder without writing in it
    # (sharing one) may say False; the registry holds every other tool to True.
    writes_paths: bool = True
    recoverable: bool = True
    trusted_output: bool = False
    # Wrapped as untrusted like any result, but escaped reversibly instead of neutralized
    # (spine/lossless.py): the model's view carries the same guarantees and decodes back
    # exactly, so a read → write round trip cannot rewrite the user's file. First-party
    # tools only.
    lossless_output: bool = False


@dataclass(frozen=True, kw_only=True)
class ToolContext:
    owner_id: str
    session_id: str
    turn: int
    call_id: str
    via: Via
    granted_roots: tuple[Path, ...] = ()
    # The shares among them that are `read` only: the deny floor refuses any tool that is
    # not a read on a path there (the shared folders spec, D11).
    read_only_roots: tuple[Path, ...] = ()
    # True once this turn has read something from outside (a web page, a file). What
    # she does with it after that is not something the user said (MEM-5, EVL-2).
    read_outside: bool = False
    # A routine's run: the one conversation it may show a board on. Set by the host
    # that starts the run, never from anything the model says.
    routine_target: str | None = None
    # A helper's chat or run: which helper.
    # Set by the host for a helper's chat or run, never from anything the model says.
    helper_id: str | None = None
    # The approved action this run carries out, set by the router on the confirmed path
    # and in every check of it. A tool puts it where the provider keeps it, so a check
    # after a crash can find what was sent (OPS-1). None on the direct path.
    action_id: str | None = None
    # When that action was proposed, in epoch milliseconds: a check looks no earlier
    # than this. None on the direct path.
    proposed_ms: int | None = None
    # The turn answers a host note (a card's tap, a background read, the day's build),
    # not words of the user's. Set by the session from what the turn answers, never from
    # anything the model says. She may not stay quiet on such a turn (VOI-7).
    host_turn: bool = False
    # The user's words this turn end in a question mark. Set by the session, never from
    # anything the model says. She may not stay quiet after a question (VOI-7).
    asked: bool = False
    # Who said this turn (SPN-14a, SPN-18, PER-13). None when the turn was typed or a
    # tap, or when nobody's voice is enrolled: the session is the anchor then (SPN-14).
    # Anything but "owner" can't approve a card, and only `GUEST_TOOLS` run for it. Set
    # by the session from the speaker check, never from anything the model says.
    speaker: Speaker | None = None

    @property
    def private(self) -> bool:
        """The voice this turn is not the enrolled owner's (PER-13)."""
        return self.speaker is not None and self.speaker != "owner"


@dataclass(frozen=True, kw_only=True)
class ToolCall:
    name: str
    args: JSON


# Said in the description of every tool that shows a card (#32). "Asks the user first"
# read to a small model as an instruction to ask in chat before calling, so the user
# answered twice. Calling the tool is the asking.
ASKS_BY_CARD = (
    "Calling it puts an approval card on the user's screen, so call it directly; "
    "never ask in chat first."
)


@dataclass(frozen=True, kw_only=True)
class ToolResult:
    ok: bool
    content: str
    effect: Effect
    error_kind: str | None = None


# The error_kind a check gives when it could not reach the provider: tried again later (OPS-1).
UNREACHABLE = "unreachable"


@dataclass(frozen=True, kw_only=True)
class ActionSummary:
    """What the user approves, composed by the host from frozen arguments (SPN-2a)."""

    text: str


@dataclass(frozen=True, kw_only=True)
class Precondition:
    ok: bool
    reason: str = ""

    @classmethod
    def passed(cls) -> Precondition:
        return cls(ok=True)

    @classmethod
    def failed(cls, reason: str) -> Precondition:
        return cls(ok=False, reason=reason)


class Tool(Protocol):
    definition: ToolDefinition

    def describe(self, args: JSON) -> ActionSummary: ...

    def target(self, args: JSON) -> str: ...

    def fingerprint(self, args: JSON) -> str | None:
        """The state of the target when the action is proposed, or None to opt out.

        Recorded with the proposal and compared again after approval: if it differs,
        the target changed since the user was asked, and nothing runs."""
        ...

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition: ...

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult: ...

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None: ...


class BaseTool:
    """Defaults for the optional parts of `Tool`. Gated tools must override `describe`."""

    definition: ToolDefinition

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text=f"Run {self.definition.name}")

    def target(self, args: JSON) -> str:
        return self.definition.name

    def fingerprint(self, args: JSON) -> str | None:
        return None

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        return None
