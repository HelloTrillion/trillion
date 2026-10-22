"""The tool contract (SPN-1). `risk` has no default: a tool must say what it can do."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

JSON = Mapping[str, Any]
Via = Literal["voice", "text", "tap", "channel"]
Effect = Literal["confirmed", "none", "unknown"]


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
    # True once this turn has read something from outside (a web page, a file). What
    # she does with it after that is not something the user said (MEM-5, EVL-2).
    read_outside: bool = False


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
