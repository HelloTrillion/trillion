"""A routine's own tools (the routines spec).

`routine_nothing` ends a routine's run with nothing to tell the user (AGT-6): no
notice, her reply not kept. The run sees it called; the tool itself changes nothing.
It is offered only to a routine's run, and refused anywhere else.

`routine_narrow` and `routine_widen` edit a routine's notes from the user's feedback
(AGT-7), as standing rules are kept (`tools/rules.py`): a narrowing that `narrows`
accepts stands at once, anything else is a card. They are offered only where the
routine belongs, its own conversation or today's day conversation for a day routine,
and refused anywhere else, with nobody watching, from a tapped ask, and after reading
outside content in the turn.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable

from trillion.brain.standing import MAX_RULE_CHARS, RuleError, clean, narrows, read_back
from trillion.scheduler.store import NOTES_MAX, Schedule, ScheduleStore
from trillion.spine.contract import (
    ASKS_BY_CARD,
    JSON,
    ActionSummary,
    BaseTool,
    Precondition,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.registry import ROUTINE_NOTHING

logger = logging.getLogger(__name__)

CATEGORY = "routine"
NOTHING = ROUTINE_NOTHING
NARROW, WIDEN = "routine_narrow", "routine_widen"
NOTES_TOOLS = frozenset({NARROW, WIDEN})
_ONLY_ROUTINES = "Only a routine's run can end with nothing to say."
# Nobody is there to have said it (a run), or the words may come from a page she read
# (a tapped ask): neither changes what a routine looks at.
_NOT_SAID = frozenset({"schedule", "routine", "ask", "refresh"})
_UNSAID = "a routine's notes change only from what the user says in its conversation"
_FROM_READING = (
    "a routine can't be changed after reading a page, a file or a message in this turn; "
    "tell the user what the change would be, then make it again"
)
_NOT_HERE = "not this routine's conversation: change a routine in its own conversation"
_WIDENS = "this adds to the routine; use routine_widen"
_FULL = (
    f"the routine's notes are full ({NOTES_MAX:,} characters): tell the user, and that "
    "they can delete the routine in Settings and set it up again"
)
_SCHEMA: JSON = {
    "type": "object",
    "properties": {
        "routine": {"type": "string", "pattern": r"^[a-f0-9]{8}$"},
        "text": {"type": "string", "minLength": 1, "maxLength": MAX_RULE_CHARS},
    },
    "required": ["routine", "text"],
    "additionalProperties": False,
}
# A conversation's kind, and whether it is today's day conversation.
Where = Callable[[str], Awaitable[tuple[str, bool]]]


def belongs(s: Schedule, kind: str, conversation_id: str, *, today: bool) -> bool:
    """Whether this conversation is where a routine is changed: an 'own' routine's
    conversation, or today's day conversation for a day routine."""
    if s.target is None or s.status != "active":
        return False
    if s.target == "own":
        return s.conversation_id == conversation_id
    return kind == "day" and today


def for_session(
    kind: str, conversation_id: str, routines: Iterable[Schedule], *, today: bool
) -> frozenset[str]:
    """Which routine tools a page's turn in this conversation is offered: the notes
    tools where a routine belongs. `routine_nothing` never is; it is a run's alone."""
    mine = any(belongs(s, kind, conversation_id, today=today) for s in routines)
    return NOTES_TOOLS if mine else frozenset()


class RoutineNothing(BaseTool):
    definition = ToolDefinition(
        name=NOTHING,
        description=(
            "End this routine's run with nothing to tell the user: nothing worth their "
            "attention was found. Then say nothing. No arguments."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=5.0,
        # Her own words back, quoting nothing she read.
        trusted_output=True,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        if ctx.via != "routine":
            return ToolResult(ok=False, content=_ONLY_ROUTINES, effect="none", error_kind="invalid")
        return ToolResult(ok=True, content="Nothing to tell the user. Say nothing.", effect="none")


def _refused(text: str, kind: str = "refused") -> ToolResult:
    return ToolResult(ok=False, content=text, effect="none", error_kind=kind)


class _NotesTool(BaseTool):
    def __init__(self) -> None:
        self._store: ScheduleStore | None = None
        self._where: Where | None = None
        self._owner = ""

    def bind(self, store: ScheduleStore, where: Where, owner: str) -> None:
        """Called once at startup with the running process's store, and how to tell
        what a conversation is."""
        self._store, self._where, self._owner = store, where, owner

    async def _why_not(self, args: JSON, ctx: ToolContext) -> str | None:
        """Why this call may not change the routine, or None when it may."""
        store, where = self._store, self._where
        if store is None or where is None or ctx.owner_id != self._owner:
            return "routines are not available"
        if ctx.via in _NOT_SAID:
            return _UNSAID
        if ctx.read_outside:
            return _FROM_READING
        try:
            clean(str(args.get("text", "")))
        except RuleError as e:
            return "the note is empty" if e.kind == "empty" else "the note is too long"
        s = await store.get(str(args.get("routine", "")))
        if s is None or s.owner_id != ctx.owner_id or s.target is None or s.status != "active":
            return "no such routine"
        kind, today = await where(ctx.session_id)
        if not belongs(s, kind, ctx.session_id, today=today):
            return _NOT_HERE
        return None

    async def _add(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        """Add the note, or say why not. None when it was added."""
        why = await self._why_not(args, ctx)
        if why is not None:
            return _refused(why)
        store = self._store
        routine = str(args["routine"])
        if store is None or not await store.add_note(
            ctx.owner_id, routine, clean(str(args["text"]))
        ):
            return _refused(_FULL, "full")
        return None


class RoutineNarrow(_NotesTool):
    definition = ToolDefinition(
        name=NARROW,
        description=(
            "In a routine's conversation, when the user says it should stop, never or less "
            "often show something ('don't include refunds'): add it to the routine's notes "
            "at once. `routine` is its id from schedule_list; their words, one sentence. "
            "Anything new it should look at goes to routine_widen."
        ),
        input_schema=_SCHEMA,
        # Only ever narrows what a routine looks at (`narrows`), so it needs no card.
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = await self._why_not(args, ctx)
        if why is not None:
            return Precondition.failed(why)
        if not narrows(str(args.get("text", ""))):
            return Precondition.failed(_WIDENS)
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        text = str(args["text"])
        # Asked again here, so nothing between the check and the write can widen it.
        if not narrows(text):
            return _refused(_WIDENS, "asks_first")
        refused = await self._add(args, ctx)
        if refused is not None:
            return refused
        # Recorded: the note is in the routine for all to see, and the call in the audit.
        logger.info("routine %s narrowed in %s", args["routine"], ctx.session_id)
        return ToolResult(
            ok=True,
            content=(
                "noted: the routine follows it from its next run on. Read it back in one "
                f"sentence: {read_back(text)}"
            ),
            effect="confirmed",
        )


class RoutineWiden(_NotesTool):
    definition = ToolDefinition(
        name=WIDEN,
        description=(
            "In a routine's conversation, when the user wants it to look at something new "
            "(a source, a kind of thing): add it to the routine's notes. `routine` is its id "
            "from schedule_list; their words, one sentence. " + ASKS_BY_CARD
        ),
        input_schema=_SCHEMA,
        # What a routine reads with nobody watching grows: the user approves it.
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        text = " ".join(str(args.get("text", "")).split())
        return ActionSummary(text=f"Add to this routine: {text}")

    def target(self, args: JSON) -> str:
        return f"routine:{args.get('routine', '')}"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = await self._why_not(args, ctx)
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        refused = await self._add(args, ctx)
        if refused is not None:
            return refused
        logger.info("routine %s widened in %s", args["routine"], ctx.session_id)
        return ToolResult(
            ok=True,
            content="added: the routine follows it from its next run on",
            effect="confirmed",
        )
