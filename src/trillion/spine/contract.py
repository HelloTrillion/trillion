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
    recoverable: bool = True
    trusted_output: bool = False


@dataclass(frozen=True, kw_only=True)
class ToolContext:
    owner_id: str
    session_id: str
    turn: int
    call_id: str
    via: Via
    granted_roots: tuple[Path, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ToolCall:
    name: str
    args: JSON


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

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        return None
