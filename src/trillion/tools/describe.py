"""`describe_self`: what she can actually do, generated from what is actually loaded (BRN-6).

"What can you do?" should never be answered from the model's imagination. This
tool reads the live registry, so a tool that is not installed is not claimed and
one that is installed is not forgotten.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from trillion import __version__
from trillion.spine.contract import (
    GATED,
    JSON,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

NAME = "describe_self"
_LINE = 160


def _first_line(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= _LINE else line[: _LINE - 1] + "…"


def _asks(d: ToolDefinition) -> str:
    if d.risk not in GATED:
        return "runs at once"
    if d.risk is Risk.SPEND or not d.recoverable:
        return "calling it shows an approval card; only a tap on the card approves it"
    return "calling it shows an approval card; a yes or a tap approves it"


class DescribeSelf(BaseTool):
    definition = ToolDefinition(
        name=NAME,
        description=(
            "List what you can really do right now: your tools and which show an approval card, "
            "the folders shared with you, the model you are running on, and the version. "
            "Call this before answering what you can or cannot do."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        category="meta",
        timeout_s=5.0,
    )

    def __init__(self) -> None:
        self._definitions: Callable[[], Sequence[ToolDefinition]] = lambda: ()
        self._facts: Callable[[], Mapping[str, Any]] = dict
        self._names: Callable[[], Mapping[Path, str]] = dict

    def bind(
        self,
        definitions: Callable[[], Sequence[ToolDefinition]],
        facts: Callable[[], Mapping[str, Any]],
        names: Callable[[], Mapping[Path, str]] = dict,
    ) -> None:
        """Called once at startup with the live registry, the runtime's own facts and
        the shares' names by folder."""
        self._definitions = definitions
        self._facts = facts
        self._names = names

    def _shared(self, ctx: ToolContext) -> list[dict[str, str]]:
        """The folders this call reaches, as the context has them now, by name."""
        names = self._names()
        read_only = set(ctx.read_only_roots)
        out: list[dict[str, str]] = []
        for root in ctx.granted_roots:
            entry = {"path": str(root)}
            if root in names:
                entry = {"name": names[root], **entry}
            entry["access"] = "read only" if root in read_only else "read and change"
            out.append(entry)
        return out

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        tools = [
            {
                "name": d.name,
                "does": _first_line(d.description),
                "kind": d.risk.value,
                "confirmation": _asks(d),
            }
            for d in sorted(self._definitions(), key=lambda d: d.name)
        ]
        report = {
            "version": __version__,
            **self._facts(),
            "shared_folders": self._shared(ctx),
            "tools": tools,
            "note": (
                "This list is complete. Anything not listed here, you cannot do: say so "
                "plainly rather than guessing."
            ),
        }
        return ToolResult(ok=True, content=json.dumps(report, ensure_ascii=False), effect="none")
