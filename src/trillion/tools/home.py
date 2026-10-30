"""Her Home tool (the Home spec, D13): `home_arrange` reorders Home's widgets, takes one
off or adds it back.

It runs at once and never asks: it only moves tiles on the user's own Home, every
change shows there and is undone with one tap, so even after a read from outside it
runs. It is never offered to a run nobody is watching (the board's category), and is
refused there and from a channel, where nobody would see the change. Every open page
is told (`{"type": "home", "by": "her"}`), so the page can open Home to show it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Protocol

from trillion.spine.contract import (
    JSON,
    BaseTool,
    Precondition,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.workspace.home import BY_TILE, IDS, TILES, Layout, arrange

ARRANGE = "home_arrange"
# The board's category: never offered to a run nobody is watching, never replayed.
CATEGORY = "workspace"
# Nobody there to see it: a timed ask, a routine, a tapped ask, a refresh, a channel.
_UNWATCHED = frozenset({"schedule", "routine", "ask", "refresh", "channel"})
_UNWATCHED_WHY = "Home is arranged only while the user is there to see it"
_UNAVAILABLE = "Home is not available"
_BOTH = "a widget can't be added and removed in one call"
_IDS: JSON = {
    "type": "array",
    "items": {"type": "string", "enum": list(IDS)},
    "maxItems": len(IDS),
    "uniqueItems": True,
}


class _Arrange(Protocol):
    def __call__(
        self, change: Callable[[Layout], Layout], *, by: str | None = None
    ) -> Awaitable[tuple[Layout, bool]]: ...


def _told(layout: Layout, changed: bool) -> str:
    def named(ids: tuple[str, ...]) -> list[dict[str, str]]:
        return [{"id": i, "title": BY_TILE[i].title} for i in ids]

    return json.dumps(
        {
            "changed": changed,
            "shown": named(layout.shown()),
            "removed": named(layout.gone()),
        },
        ensure_ascii=False,
    )


class HomeArrange(BaseTool):
    definition = ToolDefinition(
        name=ARRANGE,
        description=(
            "Arrange Home, the user's morning screen of briefing widgets, when they ask: "
            "`order` puts the widgets named first, in that order, and the rest after them "
            "as they were; `remove` takes widgets off; `add` puts removed ones back; "
            "`reset` true restores the default. No arguments changes nothing and says the "
            "layout. It runs at once and the user can undo it on Home; their day board "
            "follows Home. The widgets: " + ", ".join(f"{t.id} ({t.title})" for t in TILES) + "."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "order": _IDS,
                "remove": _IDS,
                "add": _IDS,
                "reset": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        # Only moves tiles on the user's own Home, each change undone with one tap.
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=5.0,
        trusted_output=True,
    )

    def __init__(self) -> None:
        self._read: Callable[[], Awaitable[Layout]] | None = None
        self._arrange: _Arrange | None = None
        self._owner = ""

    def bind(
        self, read: Callable[[], Awaitable[Layout]], arrange_home: _Arrange, owner_id: str
    ) -> None:
        """Called once at startup with how the core reads and changes the layout."""
        self._read, self._arrange, self._owner = read, arrange_home, owner_id

    def target(self, args: JSON) -> str:
        return "home"

    def _why_not(self, args: JSON, ctx: ToolContext) -> str | None:
        if self._read is None or self._arrange is None or ctx.owner_id != self._owner:
            return _UNAVAILABLE
        if ctx.via in _UNWATCHED:
            return _UNWATCHED_WHY
        if set(args.get("add") or ()) & set(args.get("remove") or ()):
            return _BOTH
        return None

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = self._why_not(args, ctx)
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        # Asked again here: nothing between the check and the change may let it run.
        why = self._why_not(args, ctx)
        read, change = self._read, self._arrange
        if why is not None or read is None or change is None:
            return ToolResult(
                ok=False, content=why or _UNAVAILABLE, effect="none", error_kind="refused"
            )
        if not any(args.get(k) for k in ("order", "remove", "add", "reset")):
            # A write that changed nothing still ends in a known state: confirmed.
            return ToolResult(ok=True, content=_told(await read(), False), effect="confirmed")
        layout, changed = await change(
            lambda was: arrange(
                was,
                order=[str(i) for i in args.get("order") or ()],
                remove=[str(i) for i in args.get("remove") or ()],
                add=[str(i) for i in args.get("add") or ()],
                reset=args.get("reset") is True,
            ),
            by="her",
        )
        return ToolResult(ok=True, content=_told(layout, changed), effect="confirmed")
