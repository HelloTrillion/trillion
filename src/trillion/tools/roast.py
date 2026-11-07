"""Roast my week: the true facts of the user's last seven days, for her to roast them with.

A read of this machine's own records (see `trillion/roast/week.py`); nothing in the
world changes, so it runs at once. How to roast is said here, in the description, which
is hers to follow; the facts come back as a result like any other, never as orders.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from trillion.roast.week import Week, facts
from trillion.spine.contract import JSON, BaseTool, Risk, ToolContext, ToolDefinition, ToolResult
from trillion.storage.sqlite.week_store import read_week

if TYPE_CHECKING:
    from trillion.api.runtime import Runtime

WEEK_ROAST = "week_roast"
CATEGORY = "roast"
ReadWeek = Callable[[], Awaitable[Week]]

_NOT_READY = "The week's records can't be read right now."
_QUIET = "No messages from them in the last seven days."


class WeekRoast(BaseTool):
    definition = ToolDefinition(
        name=WEEK_ROAST,
        description=(
            "Read the true facts of the user's last seven days with you, so you can roast "
            "their week: how much they talked to you and when, the latest night, what the "
            "conversations were about, how often they cut you off, the yeses and noes, the "
            "reminders set and cancelled, helpers made, and what you cost them. Call it when "
            "they ask you to roast their week, or roast them. No arguments.\n"
            "Then roast the week, in your own voice, dry and deadpan: four to six short "
            "sentences, each built on one fact it returns, with its number or time. Nothing "
            "it doesn't say: no invented habits, no guesses at why. Needle the plans and "
            "habits, never the person; no other person's name, no email, nothing private "
            "past a conversation's title, so they can share it. End with one line of "
            "grudging credit. No list, no heading, no preamble. If it finds nothing, say so in "
            "one dry line."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=10.0,
    )

    def __init__(self) -> None:
        self._read: ReadWeek | None = None
        self._owner = ""

    def bind(self, read: ReadWeek, owner_id: str) -> None:
        """How to read the week, and whose it is. Tests call this directly."""
        self._read, self._owner = read, owner_id

    async def attach(self, rt: Runtime) -> None:
        """Called once the runtime is built: the tool connects itself (no runtime.py edit)."""
        from trillion.api.runtime import OWNER

        conn = rt.read_conn
        if conn is None:
            return
        # On the conversations' connection: it only reads, so it never waits on a write.
        self.bind(lambda: read_week(conn, OWNER, rt.clock(), rt.zone() or ""), OWNER)

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        if self._read is None or ctx.owner_id != self._owner:
            return ToolResult(ok=False, content=_NOT_READY, effect="none", error_kind="unavailable")
        try:
            week = await self._read()
        except Exception:
            return ToolResult(ok=False, content=_NOT_READY, effect="none", error_kind="unavailable")
        if week.empty:
            return ToolResult(ok=True, content=_QUIET, effect="none")
        return ToolResult(ok=True, content="\n".join(facts(week)), effect="none")
