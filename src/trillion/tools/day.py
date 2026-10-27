"""Your day (SUR-1): open today's day conversation, where she builds the day board.

It asks the page this conversation is open on to switch to the day, which is all
the "Your day" entry does; the page then asks the host to build it. Nothing in the
world changes, so it runs at once. Like the board tools it is never offered to a run
nobody is watching: there is no page there to open.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from trillion.spine.contract import JSON, BaseTool, Risk, ToolContext, ToolDefinition, ToolResult

DAY_OPEN = "day_open"
# Where the call was made: a chat the page switches away from, the day itself, or a
# conversation with no page open on it.
Where = Literal["opened", "here", "no_page"]
OpenDay = Callable[[str], Awaitable[Where]]

OPENING = "Opening your day."
HERE = "This is your day already; rebuild the board here."
_NO_PAGE = "There is no page open to show the day on."


class DayOpen(BaseTool):
    definition = ToolDefinition(
        name=DAY_OPEN,
        description=(
            "Open today's day conversation, where the user's day is built on the board from "
            "what they connected. Call it when they ask about their day. No arguments."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        # The board's category: never offered where no page is watching.
        category="workspace",
        timeout_s=5.0,
    )

    def __init__(self) -> None:
        self._open: OpenDay | None = None
        self._owner = ""

    def bind(self, open_day: OpenDay, owner_id: str) -> None:
        """Called once at startup with how to reach a conversation's page."""
        self._open, self._owner = open_day, owner_id

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        if self._open is None or ctx.owner_id != self._owner:
            return ToolResult(ok=False, content=_NO_PAGE, effect="none", error_kind="no_page")
        where = await self._open(ctx.session_id)
        if where == "no_page":
            return ToolResult(ok=False, content=_NO_PAGE, effect="none", error_kind="no_page")
        return ToolResult(ok=True, content=OPENING if where == "opened" else HERE, effect="none")
