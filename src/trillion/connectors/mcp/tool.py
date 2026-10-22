"""One tool of a connected MCP server, as a Trillion tool the spine gates (CON-5).

Nothing here decides what may run. The tool declares a risk, the one the user gave
it or `irreversible`, and the router does the rest: asks first, freezes the
arguments, checks protected paths, records the decision and the outcome. A
server's own hint that a tool only reads is shown to the user, never trusted.

What a server returns is wrapped as untrusted like any tool result, and a turn
that has read it is treated like a turn that read a web page.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from trillion.connectors.mcp.config import ServerConfig
from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

CATEGORY = "mcp"
MAX_DESCRIPTION = 900
MAX_RESULT = 60_000
_PATHY = re.compile(r"path|file|dir|folder", re.IGNORECASE)
# Top-level schema keywords the spine refuses (they let arguments through that the
# card would never show).
_OPEN = ("patternProperties", "unevaluatedProperties", "$ref")


class Unusable(ValueError):
    """A tool this server offers that cannot be offered safely. Says why."""


@dataclass(frozen=True, kw_only=True)
class Offered:
    """What a server says about one of its tools."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    read_only_hint: bool = False


@dataclass(frozen=True, kw_only=True)
class Outcome:
    ok: bool
    text: str
    # Whether the server answered at all: an answer is evidence, silence is not.
    answered: bool = True


Call = Callable[[str, JSON, float], Awaitable[Outcome]]


def tool_name(server: str, tool: str, taken: set[str]) -> str:
    """`mcp_<server>_<tool>`, in the characters and length the spine allows, never
    the same as one already taken."""
    clean = re.sub(r"[^a-z0-9_]+", "_", f"mcp_{server}_{tool}".lower()).strip("_")
    name = clean[:64]
    if name in taken or len(clean) > 64:
        digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:8]
        name = f"{clean[:55]}_{digest}"
    return name


def schema_for(raw: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The tool's arguments as the spine takes them: an object that allows nothing it
    does not name. Returns it and the arguments that may hold a path."""
    schema = json.loads(json.dumps(dict(raw or {})))  # a deep, plain copy
    schema.pop("$schema", None)
    if schema.get("type", "object") != "object":
        raise Unusable("its arguments are not an object")
    for key in _OPEN:
        if key in schema:
            raise Unusable(f"its arguments use {key!r}, which the card could not show")
    schema["type"] = "object"
    schema.setdefault("properties", {})
    if not isinstance(schema["properties"], dict):
        raise Unusable("its arguments are malformed")
    # Nothing it does not name: every argument that reaches the server is one the
    # user saw on the card.
    schema["additionalProperties"] = False
    pathy = tuple(sorted(p for p in schema["properties"] if _PATHY.search(p)))
    return schema, pathy


def _brief(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= 120 else text[:117] + "…"


class McpTool(BaseTool):
    def __init__(self, server: ServerConfig, offered: Offered, name: str, call: Call) -> None:
        schema, pathy = schema_for(offered.input_schema)
        hint = (
            " The server says it only reads; it still asks unless you said so."
            if (offered.read_only_hint and server.risk_of(offered.name).value != "read")
            else ""
        )
        description = (offered.description or offered.name).strip()
        self.definition = ToolDefinition(
            name=name,
            description=(
                f"[{server.name}, a connected MCP server] {description}"[:MAX_DESCRIPTION]
            ),
            input_schema=schema,
            risk=server.risk_of(offered.name),
            category=CATEGORY,
            timeout_s=server.timeout_s,
            outside_path_args=pathy,
        )
        self.server = server.name
        self.remote_name = offered.name
        self.read_only_hint = offered.read_only_hint
        self.hint = hint
        self._call = call

    def describe(self, args: JSON) -> ActionSummary:
        """The card: which server, which tool, and every argument it will be sent."""
        shown = ", ".join(f"{k}: {_brief(v)}" for k, v in args.items())
        return ActionSummary(
            text=f"Use {self.server}'s {self.remote_name}" + (f" with {shown}" if shown else "")
        )

    def target(self, args: JSON) -> str:
        return f"mcp:{self.server}:{self.remote_name}"

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        out = await self._call(self.remote_name, args, self.definition.timeout_s)
        text = out.text if len(out.text) <= MAX_RESULT else out.text[:MAX_RESULT] + "\n[cut]"
        if out.ok:
            return ToolResult(ok=True, content=text or "(no output)", effect="confirmed")
        # The server reported a failure, or never answered: something may have
        # happened, so the outcome is unknown, never a guess either way.
        return ToolResult(
            ok=False,
            content=text or "the server did not answer",
            effect="unknown",
            error_kind="mcp_error" if out.answered else "unreachable",
        )
