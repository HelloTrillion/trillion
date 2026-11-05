"""Feedback on what she speaks up about (the speaking up spec, D10, AGT-7).

`speak_up_narrow` and `speak_up_widen` add the user's words to a watcher's notes, as
`routine_narrow` and `routine_widen` do a routine's, with the same test
(`narrows_routine`): a plain narrowing ("stop telling me about newsletters") stands at
once; anything else, a sender to watch among them, is a card.

A heads-up is written from someone else's words (an invite, an email), so the turn
after one counts as read (MEM-5). There a narrowing does not stand at once either: it
goes to a card, as a standing rule does after a read, so an email can't change what
she watches by way of her own line. Nobody watching (a timed ask, a routine, a
refresh), a tapped ask and a channel never change them.

A note `watch: name@example.com` asks the email watcher to read that sender's mail too
(`attention/mail.py`); it can only come through a card.
"""

from __future__ import annotations

import logging

from trillion.attention.settings import NAMED, WATCHERS
from trillion.attention.store import AttentionStore
from trillion.brain.standing import MAX_RULE_CHARS, RuleError, clean
from trillion.memory.present import inert
from trillion.scheduler.store import NOTES_MAX
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
from trillion.tools.routine import narrows_routine

logger = logging.getLogger(__name__)

CATEGORY = "attention"
NARROW, WIDEN = "speak_up_narrow", "speak_up_widen"
# Where nobody said it: a run with nobody there, a tapped ask (its words may come from
# a page), a refresh, or a channel.
_NOT_SAID = frozenset({"schedule", "routine", "ask", "refresh", "channel"})
_UNSAID = "what she speaks up about changes only from what the user says to her"
_WIDENS = (
    "not kept: this could add to what she speaks up about, so it asks first. Call "
    "speak_up_widen with the same words."
)
_FROM_READING = (
    "not kept: something someone else wrote was read in this turn, so a change now "
    "asks first. Call speak_up_widen with the same words, and tell the user in one plain "
    "sentence that you've put it on a card."
)
_FULL = (
    f"the notes are full ({NOTES_MAX:,} characters): tell the user they can clear them in "
    "Settings, Speaking up"
)
_SCHEMA: JSON = {
    "type": "object",
    "properties": {
        "watcher": {"type": "string", "enum": list(WATCHERS)},
        "text": {"type": "string", "minLength": 1, "maxLength": MAX_RULE_CHARS},
    },
    "required": ["watcher", "text"],
    "additionalProperties": False,
}


def _refused(text: str, kind: str = "refused") -> ToolResult:
    return ToolResult(ok=False, content=text, effect="none", error_kind=kind)


def read_back(watcher: str, text: str) -> str:
    said = " ".join(text.split()).rstrip(" .")
    return f"Noted for {NAMED.get(watcher, watcher)}: {said}."


class _NotesTool(BaseTool):
    def __init__(self) -> None:
        self._store: AttentionStore | None = None
        self._owner = ""

    def bind(self, store: AttentionStore, owner: str) -> None:
        """Called once at startup with the running process's store."""
        self._store, self._owner = store, owner

    def _why_not(self, args: JSON, ctx: ToolContext) -> str | None:
        if self._store is None or ctx.owner_id != self._owner:
            return "speaking up is not available"
        if ctx.via in _NOT_SAID:
            return _UNSAID
        if str(args.get("watcher", "")) not in WATCHERS:
            return f"no such watcher; there are {', '.join(WATCHERS)}"
        try:
            clean(str(args.get("text", "")))
        except RuleError as e:
            return "the note is empty" if e.kind == "empty" else "the note is too long"
        return None

    async def _add(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        """Add the note; None when it was added, else why not."""
        why = self._why_not(args, ctx)
        if why is not None:
            return _refused(why)
        store = self._store
        if store is None or not await store.add_note(
            ctx.owner_id, str(args["watcher"]), clean(str(args["text"]))
        ):
            return _refused(_FULL, "full")
        return None


class SpeakUpNarrow(_NotesTool):
    definition = ToolDefinition(
        name=NARROW,
        description=(
            "When the user says you should stop, never or less often speak up about "
            "something ('stop telling me about newsletters'): add it to that watcher's "
            "notes at once. `watcher` is meetings, email or payments; their words, one "
            "sentence. Anything new to watch goes to speak_up_widen."
        ),
        input_schema=_SCHEMA,
        # Only ever narrows what she speaks up about (`narrows_routine`), so no card.
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    def target(self, args: JSON) -> str:
        return f"attention:{args.get('watcher', '')}"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = self._why_not(args, ctx)
        if why is not None:
            return Precondition.failed(why)
        if ctx.read_outside:
            return Precondition.failed(_FROM_READING)
        if not narrows_routine(str(args.get("text", ""))):
            return Precondition.failed(_WIDENS)
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        text = str(args["text"])
        # Asked again here, so nothing between the check and the write can widen it.
        if ctx.read_outside:
            return _refused(_FROM_READING, "asks_first")
        if not narrows_routine(text):
            return _refused(_WIDENS, "asks_first")
        refused = await self._add(args, ctx)
        if refused is not None:
            return refused
        watcher = str(args["watcher"])
        logger.info("speaking up: %s narrowed in %s", watcher, ctx.session_id)
        return ToolResult(
            ok=True,
            content=(
                "noted: it holds from the next check on. Read it back in one sentence: "
                f"{read_back(watcher, text)}"
            ),
            effect="confirmed",
        )


class SpeakUpWiden(_NotesTool):
    definition = ToolDefinition(
        name=WIDEN,
        description=(
            "When the user wants you to speak up about something new, or a change "
            "speak_up_narrow sent here: add it to that watcher's notes. `watcher` is "
            "meetings, email or payments; their words, one sentence. To watch a sender's "
            "email, the text is exactly 'watch: ' and their address, such as "
            "'watch: name@example.com'. " + ASKS_BY_CARD
        ),
        input_schema=_SCHEMA,
        # What is read with nobody watching, and sent to the model, grows: the user
        # approves it.
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        text = inert(" ".join(str(args.get("text", "")).split()))
        named = NAMED.get(str(args.get("watcher", "")), "speaking up")
        return ActionSummary(text=f"Speak up about {named}: {text}")

    def target(self, args: JSON) -> str:
        return f"attention:{args.get('watcher', '')}"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = self._why_not(args, ctx)
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        refused = await self._add(args, ctx)
        if refused is not None:
            return refused
        logger.info("speaking up: %s widened in %s", args["watcher"], ctx.session_id)
        return ToolResult(
            ok=True, content="added: it holds from the next check on", effect="confirmed"
        )
