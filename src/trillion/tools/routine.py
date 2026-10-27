"""A routine's own tools (the routines spec).

`routine_nothing` ends a routine's run with nothing to tell the user (AGT-6): no
notice, her reply not kept. The run sees it called; the tool itself changes nothing.
It is offered only to a routine's run, and refused anywhere else.

`routine_narrow` and `routine_widen` edit a routine's notes from the user's feedback
(AGT-7), as standing rules are kept (`tools/rules.py`): a narrowing that
`narrows_routine` accepts stands at once, anything else is a card. They are offered only where the
routine belongs, its own conversation or today's day conversation for a day routine,
and refused anywhere else, with nobody watching, from a tapped ask, and after reading
outside content in the turn.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Iterable

from trillion.brain.standing import MAX_RULE_CHARS, RuleError, clean
from trillion.memory.present import inert
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
# Nobody is there to have said it (a run), the words may come from a page she read
# (a tapped ask), or from somewhere other than the routine's own page (a channel):
# none of them changes what a routine looks at.
_NOT_SAID = frozenset({"schedule", "routine", "ask", "refresh", "channel"})
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

# How feedback that only narrows a routine opens ("stop showing refunds", "less detail").
# How feedback that only narrows a routine opens. A verb opener ("stop", "never") must
# be followed by one of `_SHOWN`: "stop showing refunds". A noun opener ("less",
# "skip") takes only plain nouns after it: "less detail", "skip weekends".
_VERB_OPENERS = ("stop", "don't", "do not", "dont", "never")
_NOUN_OPENERS = ("no", "less", "fewer", "skip", "drop", "hide", "without", "exclude")
_SHOWN = frozenset(
    "show showing include including mention mentioning list listing report reporting "
    "tell telling".split()
)
# Every word that narrows, limits or negates, matched as the start of a word. A
# narrowing holds exactly one, its opener: a second turns it round ("stop skipping
# Slack", "fewer exclusions"), so it goes to a card.
_NARROWING_STEMS = (
    "stop", "never", "no", "not", "don", "less", "fewer", "skip", "hid", "drop", "exclu",
    "omit", "remov", "cut", "limit", "filter", "restrict", "strict", "without", "only",
    "ignor", "leav", "forget", "miss", "avoid", "block", "ban", "mute", "silenc", "narrow",
    "reduc", "trim", "shorter", "smaller",
)  # fmt: skip
_LEAD = re.compile(r"^(?:(?:please|from now on|ok|okay)\b[\s,]*)+")
# Plain words only: no `;`, `:`, `/` or quotes that could chain a second request on.
_PLAIN = re.compile(r"^[a-z0-9 ,.'$%-]+$")
_WORD = re.compile(r"[a-z0-9$%]+(?:'[a-z]+)?")
_MAX_WORDS = 10
# A web address names somewhere new to read.
_ADDRESS = re.compile(r"\bwww\.|\b[a-z0-9-]+\.[a-z]{2,}\b")
# Any word starting with one of these might widen what a run does: it chains on a
# second request, undoes what was left out, reads more, reaches for everything, or
# acts. Matched as a word start; the shortest only as whole words, so "anything" and
# "payments" are not taken for "any" and "pay".
_WIDENS_STEMS = (
    # Chaining.
    "plus", "then", "also", "more", "another", "extra", "well", "unless", "except",
    "instead", "rather", "however",
    # Undoing what was left out.
    "overlook", "just", "nothing", "everything",
    # Reading more, and everything at once.
    "read", "check", "look", "search", "fetch", "open", "watch", "pull", "scan", "every",
    "whole", "entire",
    # Acting.
    "send", "email", "post", "message", "share", "forward", "call", "book", "delete",
    "cancel",
)  # fmt: skip
_WIDENS_WORDS = frozenset("and or but too new all any see add buy pay".split())
# After a noun opener: verbs that could turn it round ("no keeping it short", "fewer
# charts, let the rest in"). Any word ending -ing or -ed is refused there too.
_NOUN_DENY = frozenset(
    "let allow keep get give bring make go take put use do have be is are was were "
    "turn start begin continue resume restore return undo unhide unmute unblock show "
    "include mention tell hold".split()
)


def _widens(word: str) -> bool:
    return word in _WIDENS_WORDS or word.startswith(_WIDENS_STEMS)


def _verbish(word: str) -> bool:
    """A word that may be a verb doing something: -ing or -ed ("anything" is not)."""
    if word.endswith("thing"):
        return False
    return word.endswith(("ing", "ed")) or word in _NOUN_DENY


def narrows_routine(text: str) -> bool:
    """Whether feedback only narrows what a routine looks at, so it stands at once.

    An allowlist, so it fails safe: a short plain sentence with exactly one narrowing
    word, its opener; after a verb opener, a word for showing ("stop showing X"); after
    a noun opener, nothing that reads as a verb. Nothing anywhere that could chain on,
    read more or act. Anything not plainly narrowing goes to a card (`routine_widen`)."""
    said = " ".join(text.replace("\u2019", "'").casefold().split()).rstrip(" .")
    said = _LEAD.sub("", said)
    if not _PLAIN.match(said) or _ADDRESS.search(said):
        return False
    words = _WORD.findall(said)
    if not words or len(words) > _MAX_WORDS or any(_widens(w) for w in words):
        return False
    # Exactly one narrowing word: the opener ("do not" counts once, as "not").
    if len([w for w in words if w.startswith(_NARROWING_STEMS)]) != 1:
        return False
    verb = next((o for o in _VERB_OPENERS if said.startswith(o + " ")), None)
    if verb is not None:
        rest = _WORD.findall(said[len(verb) :])
        return bool(rest) and rest[0] in _SHOWN
    noun = next((o for o in _NOUN_OPENERS if said.startswith(o + " ")), None)
    if noun is None:
        return False
    rest = _WORD.findall(said[len(noun) :])
    return bool(rest) and not any(_verbish(w) for w in rest)


def belongs(s: Schedule, kind: str, conversation_id: str, *, today: bool) -> bool:
    """Whether this conversation is where a routine is changed: an 'own' routine's
    conversation, or today's day conversation for a day routine."""
    if s.target is None or s.status != "active":
        return False
    if s.target == "own":
        return s.conversation_id == conversation_id
    return kind == "day" and today


def mine(
    kind: str, conversation_id: str, routines: Iterable[Schedule], *, today: bool
) -> Schedule | None:
    """The routine this conversation belongs to, if any."""
    return next((s for s in routines if belongs(s, kind, conversation_id, today=today)), None)


def for_session(
    kind: str, conversation_id: str, routines: Iterable[Schedule], *, today: bool
) -> frozenset[str]:
    """Which routine tools a page's turn in this conversation is offered: the notes
    tools where a routine belongs. `routine_nothing` never is; it is a run's alone."""
    found = mine(kind, conversation_id, routines, today=today)
    return NOTES_TOOLS if found is not None else frozenset()


def read_back(s: Schedule, text: str) -> str:
    """Her one sentence for a note just kept: the routine's name and the words as said."""
    said = " ".join(text.split()).rstrip(" .")
    return f'Noted for "{inert(s.title)}": {said}.'


def named(s: Schedule) -> str:
    """How her context names the routine she is in: its id and its name, on one line,
    with nothing in the name that reads as markup or closes its quotes."""
    title = inert(s.title).replace('"', "'")
    return f'{s.id} "{title}"'


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
        # Each routine's name, as its last check found it, for its card.
        self._titles: dict[str, str] = {}

    def bind(self, store: ScheduleStore, where: Where, owner: str) -> None:
        """Called once at startup with the running process's store, and how to tell
        what a conversation is."""
        self._store, self._where, self._owner = store, where, owner

    async def _check(self, args: JSON, ctx: ToolContext) -> Schedule | str:
        """The routine this call may change, or why it may not."""
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
        # The one routine this conversation names in her context, and only that one.
        here = mine(kind, ctx.session_id, await store.active(ctx.owner_id), today=today)
        if here is None or here.id != s.id:
            return _NOT_HERE
        self._titles[s.id] = s.title
        return s

    async def _why_not(self, args: JSON, ctx: ToolContext) -> str | None:
        checked = await self._check(args, ctx)
        return checked if isinstance(checked, str) else None

    async def _add(self, args: JSON, ctx: ToolContext) -> Schedule | ToolResult:
        """Add the note: the routine it was added to, or why not."""
        checked = await self._check(args, ctx)
        if isinstance(checked, str):
            return _refused(checked)
        store = self._store
        if store is None or not await store.add_note(
            ctx.owner_id, checked.id, clean(str(args["text"]))
        ):
            return _refused(_FULL, "full")
        return checked


class RoutineNarrow(_NotesTool):
    definition = ToolDefinition(
        name=NARROW,
        description=(
            "In a routine's conversation, when the user says it should stop, never or less "
            "often show something ('don't include refunds'): add it to the routine's notes "
            "at once. `routine` is the id in your context ('routine: id ...'); their words, "
            "one sentence. "
            "Anything new it should look at goes to routine_widen."
        ),
        input_schema=_SCHEMA,
        # Only ever narrows what a routine looks at (`narrows_routine`), so no card.
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = await self._why_not(args, ctx)
        if why is not None:
            return Precondition.failed(why)
        if not narrows_routine(str(args.get("text", ""))):
            return Precondition.failed(_WIDENS)
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        text = str(args["text"])
        # Asked again here, so nothing between the check and the write can widen it.
        if not narrows_routine(text):
            return _refused(_WIDENS, "asks_first")
        added = await self._add(args, ctx)
        if isinstance(added, ToolResult):
            return added
        # Recorded: the note is in the routine for all to see, and the call in the audit.
        logger.info("routine %s narrowed in %s", added.id, ctx.session_id)
        return ToolResult(
            ok=True,
            content=(
                "noted: the routine follows it from its next run on. Read it back in one "
                f"sentence: {read_back(added, text)}"
            ),
            effect="confirmed",
        )


class RoutineWiden(_NotesTool):
    definition = ToolDefinition(
        name=WIDEN,
        description=(
            "In a routine's conversation, when the user wants it to look at something new "
            "(a source, a kind of thing): add it to the routine's notes. `routine` is the id "
            "in your context ('routine: id ...'); their words, one sentence. " + ASKS_BY_CARD
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
        # Named by its check, which runs before the card is made.
        title = self._titles.get(str(args.get("routine", "")), "this routine")
        return ActionSummary(text=f"Add to {title}: {text}")

    def target(self, args: JSON) -> str:
        return f"routine:{args.get('routine', '')}"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = await self._why_not(args, ctx)
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        added = await self._add(args, ctx)
        if isinstance(added, ToolResult):
            return added
        logger.info("routine %s widened in %s", added.id, ctx.session_id)
        return ToolResult(
            ok=True,
            content="added: the routine follows it from its next run on",
            effect="confirmed",
        )
