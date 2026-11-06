"""The helper tools (the your helpers spec, AGT-1, AGT-2, AGT-7).

`helper_create` makes a helper on a card, and saves its first line to its chat as
hers. `helper_update` changes its name or job on a card, `helper_narrow` and
`helper_widen` teach it from the user's feedback as routines are taught
(`tools/routine.py`): a narrowing stands at once, anything else is a card. These three
work only in the helper's own chat. `helper_ask` hands it work: a timed ask due now,
which the scheduler runs in its chat. `helper_suggest` keeps the offer she makes when
a need keeps coming back, for the page to show.

Only she dispatches (AGT-2): the router refuses `helper_create`, `helper_ask` and
`helper_suggest` in a helper's chat or run (`HELPER_NEVER`), and they check again here.
Nobody's run gets any of them: the category is never unattended (`UNATTENDED_NEVER`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from trillion.brain.standing import MAX_RULE_CHARS, RuleError, clean
from trillion.brain.types import Msg, TextBlock
from trillion.helpers.look import COLORS, ICONS, pick
from trillion.helpers.needs import label
from trillion.helpers.store import JOB_MAX, MAX_HELPERS, NAME_MAX, NOTES_MAX, HelperStore, OwnHelper
from trillion.memory.present import inert
from trillion.scheduler.store import Schedule, ScheduleStore
from trillion.scheduler.when import WhenError, zone_of
from trillion.spend.types import Work
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
from trillion.tools.schedule import MAX_ASK, MAX_TITLE

logger = logging.getLogger(__name__)

CATEGORY = "helper"
CREATE, UPDATE = "helper_create", "helper_update"
NARROW, WIDEN = "helper_narrow", "helper_widen"
ASK, SUGGEST = "helper_ask", "helper_suggest"
NAMES = (CREATE, UPDATE, NARROW, WIDEN, ASK, SUGGEST)
# The offer she made, for the page to show (`{area, at_ms, name, job}`).
OFFER_PREF = "helper_offer"
# The areas the user said no to: never offered again.
DECLINED_PREF = "helper_declined"
# When her host note last said to offer one: at most once a day, offered or not.
NOTED_PREF = "helper_offer_noted"
# A handoff's title: the helper's name and as much of the request as fits.
TITLE_ASK = MAX_TITLE
# A name is plain words: nothing that reads as markup or code.
_NOT_IN_NAMES = frozenset("<>`")
_PLAIN_NAME = "a helper's name is plain words: no symbols or line breaks"
AREA_MAX = 64
WORK: Work = "helpers"

# Nobody is there to have said it (a run), the words may come from a page she read
# (a tapped ask), or from somewhere other than the app (a channel).
_NOT_SAID = frozenset({"schedule", "routine", "ask", "refresh", "channel"})
_UNSAID = "a helper is made or changed only from what the user says"
_FROM_READING = (
    "a helper can't be made or changed after reading a page, a file or a message in this "
    "turn; tell the user what it would be, then make it again"
)
_ASK_FROM_READING = (
    "a helper can't be handed work after reading a page, a file or a message in this "
    "turn; tell the user what you'd ask it, then ask again"
)
_NOT_FOR_HELPERS = "only Trillion makes helpers or hands them work"
_NOT_HERE = "not this helper's chat: change a helper in its own chat"
_WIDENS = "this adds to what the helper does; use helper_widen"
_NO_SUCH = "no such helper"
_UNAVAILABLE = "helpers are not available"
_FULL = f"there are {MAX_HELPERS} helpers: suggest deleting one"
_NOT_OFFERED = "no helper is being offered now"
_ID: dict[str, Any] = {"type": "string", "pattern": r"^[a-f0-9]{32}$"}
_NAME: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": NAME_MAX,
    "description": "A short name for what it looks after, such as 'Invoices'.",
}
_JOB: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": JOB_MAX,
    "description": (
        "One full sentence in the helper's own voice, starting with 'I': what it does "
        "for the user, such as 'I keep track of unpaid invoices and remind you who owes "
        "you money.'"
    ),
}
_ICON: dict[str, Any] = {
    "type": "string",
    "enum": list(ICONS),
    "description": "Its picture's icon: one that fits the job.",
}
_COLOR: dict[str, Any] = {
    "type": "string",
    "enum": list(COLORS),
    "description": "Its picture's colour.",
}
_NOTES_SCHEMA: JSON = {
    "type": "object",
    "properties": {
        "helper": _ID,
        "text": {"type": "string", "minLength": 1, "maxLength": MAX_RULE_CHARS},
    },
    "required": ["helper", "text"],
    "additionalProperties": False,
}

OfferOpen = Callable[[str, str], Awaitable[bool]]
OfferClaim = Callable[[str, str], bool]


class Conversations(Protocol):
    async def append(
        self,
        conversation_id: str,
        owner_id: str,
        kind: Literal["assistant"],
        msg: Msg,
        now_ms: int,
    ) -> int: ...


class Preferences(Protocol):
    async def put(self, owner_id: str, key: str, value: Any, now_ms: int) -> None: ...


def first_line(name: str, job: str) -> str:
    """A new helper's first message in its chat, as hers."""
    return f"Hi, I'm {inert(name)}. {inert(job)} Tell me anything I should know to start."


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _refused(text: str, kind: str = "refused") -> ToolResult:
    return ToolResult(ok=False, content=text, effect="none", error_kind=kind)


def _controls(raw: str) -> bool:
    """Any control or format character, a line break or a tab among them."""
    return any(unicodedata.category(c) in ("Cc", "Cf") for c in raw)


def _name_wrong(value: Any) -> str | None:
    raw = str(value or "")
    if _controls(raw) or any(c in _NOT_IN_NAMES for c in raw):
        return _PLAIN_NAME
    name = _one_line(raw)
    if not name:
        return "the name is empty"
    if len(name) > NAME_MAX:
        return f"the name is too long: keep it to {NAME_MAX} characters"
    return None


def _job_wrong(value: Any) -> str | None:
    raw = str(value or "")
    if _controls(raw):
        return "a helper's job is one plain line: no line breaks"
    job = _one_line(raw)
    if not job:
        return "the job is empty: say what it does"
    if len(job) > JOB_MAX:
        return f"the job is too long: keep it to {JOB_MAX} characters, one sentence"
    return None


def plain(name: Any, job: Any) -> str | None:
    """Why a name and job can't be a helper's, if they can't: the same check a card's
    and an offer's get."""
    return _name_wrong(name) or _job_wrong(job)


def card_look(args: JSON) -> dict[str, str]:
    """The picture a create card shows: the one its tap makes, from the same approved
    arguments."""
    icon, color = pick(
        _one_line(args.get("name")), _one_line(args.get("job")), args.get("icon"), args.get("color")
    )
    return {"icon": icon, "color": color}


async def make(
    store: HelperStore,
    conversations: Conversations | None,
    owner_id: str,
    name: str,
    job: str,
    now_ms: int,
    *,
    icon: str | None = None,
    color: str | None = None,
) -> OwnHelper | Literal["full", "taken"]:
    """Make a helper, and save its first line to its chat as hers: on a card's tap or
    an offer's. Its picture is the one given, else picked from the job and name."""
    made = await store.create(owner_id, name, job, now_ms, icon=icon, color=color)
    if isinstance(made, str):
        return made
    if conversations is not None:
        hello = Msg(role="assistant", blocks=(TextBlock(text=first_line(name, job)),))
        await conversations.append(made.conversation_id, owner_id, "assistant", hello, now_ms)
    return made


def _taken(name: str) -> str:
    return f"there's already a helper called {inert(name)}: suggest using it"


async def _never_held(work: Work) -> bool:
    return False


async def _never_offered(session_id: str, area: str) -> bool:
    return False


def _never_claimed(session_id: str, area: str) -> bool:
    return False


class HelperTool(BaseTool):
    def __init__(self) -> None:
        self._helpers: HelperStore | None = None
        self._schedules: ScheduleStore | None = None
        self._conversations: Conversations | None = None
        self._preferences: Preferences | None = None
        self._clock: Callable[[], int] = lambda: 0
        self._zone: Callable[[], str | None] = lambda: None
        self._held: Callable[[Work], Awaitable[bool]] = _never_held
        self._offer_open: OfferOpen = _never_offered
        self._offer_claim: OfferClaim = _never_claimed
        self._offer_lock = asyncio.Lock()
        self._owner = ""
        # Each helper's name, as its last check found it, for its card: `describe` has
        # no store, so it relies on the router running the precheck first.
        self._names: dict[str, str] = {}

    def bind(
        self,
        helpers: HelperStore,
        schedules: ScheduleStore,
        *,
        clock: Callable[[], int],
        owner: str,
        conversations: Conversations | None = None,
        preferences: Preferences | None = None,
        zone: Callable[[], str | None] | None = None,
        held: Callable[[Work], Awaitable[bool]] | None = None,
        offer_open: OfferOpen | None = None,
        offer_claim: OfferClaim | None = None,
        offer_lock: asyncio.Lock | None = None,
    ) -> None:
        """Called once at startup with the running process's stores and clock; `held`
        says when background work waits at a spend cap, `offer_open` when a helper is
        being offered for an area in a conversation, `offer_claim` the same and takes it,
        so it is offered once. `offer_lock` is held while the offer is saved, as the
        page's answer holds it."""
        self._helpers, self._schedules = helpers, schedules
        self._clock, self._owner = clock, owner
        self._conversations, self._preferences = conversations, preferences
        if zone is not None:
            self._zone = zone
        if held is not None:
            self._held = held
        if offer_open is not None:
            self._offer_open = offer_open
        if offer_claim is not None:
            self._offer_claim = offer_claim
        if offer_lock is not None:
            self._offer_lock = offer_lock

    def _store(self, ctx: ToolContext) -> HelperStore | None:
        return self._helpers if ctx.owner_id == self._owner else None

    @staticmethod
    def _said(ctx: ToolContext) -> str | None:
        """Why the user can't be taken to have said this here, if they can't."""
        if ctx.via in _NOT_SAID:
            return _UNSAID
        if ctx.read_outside:
            return _FROM_READING
        return None

    async def _own(self, args: JSON, ctx: ToolContext) -> OwnHelper | str:
        """The helper whose own chat this is, named in the call, or why not."""
        store = self._store(ctx)
        if store is None:
            return _UNAVAILABLE
        helper_id = str(args.get("helper", ""))
        h = await store.get(ctx.owner_id, helper_id)
        if h is None:
            return _NO_SUCH
        if ctx.helper_id != h.id:
            return _NOT_HERE
        self._names[h.id] = h.name
        return h


class HelperCreate(HelperTool):
    definition = ToolDefinition(
        name=CREATE,
        description=(
            "Make a helper: a specialist with one job and a chat of its own, when the user "
            "asks for one. Draft a short `name`, and the `job` as one full sentence in its "
            "own voice starting with 'I', from what they said, and pick an `icon` and "
            "`color` for its picture that fit the job. Not "
            "when a helper's job already covers it: suggest using that one. " + ASKS_BY_CARD
        ),
        input_schema={
            "type": "object",
            "properties": {"name": _NAME, "job": _JOB, "icon": _ICON, "color": _COLOR},
            "required": ["name", "job"],
            "additionalProperties": False,
        },
        # A new specialist that works on its own: the user approves it.
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        name, job = _one_line(args.get("name")), _one_line(args.get("job"))
        return ActionSummary(text=f"New helper: {name}. {job}")

    def target(self, args: JSON) -> str:
        return f"helper:{_one_line(args.get('name')).casefold()}"

    async def _why_not(self, args: JSON, ctx: ToolContext) -> str | None:
        store = self._store(ctx)
        if store is None:
            return _UNAVAILABLE
        if ctx.helper_id is not None:
            return _NOT_FOR_HELPERS
        said = self._said(ctx)
        if said is not None:
            return said
        wrong = plain(args.get("name"), args.get("job"))
        if wrong is not None:
            return wrong
        name = _one_line(args.get("name"))
        have = await store.all(ctx.owner_id)
        same = next((h for h in have if h.name.casefold() == name.casefold()), None)
        if same is not None:
            return _taken(same.name)
        if len(have) >= MAX_HELPERS:
            return _FULL
        return None

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = await self._why_not(args, ctx)
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        # Asked again here, so nothing between the card and the tap gets past it.
        why = await self._why_not(args, ctx)
        store = self._store(ctx)
        if why is not None or store is None:
            return _refused(why or _UNAVAILABLE)
        name, job = _one_line(args["name"]), _one_line(args["job"])
        look = card_look(args)
        made = await make(
            store,
            self._conversations,
            ctx.owner_id,
            name,
            job,
            self._clock(),
            icon=look["icon"],
            color=look["color"],
        )
        if made == "full":
            return _refused(_FULL, "full")
        if made == "taken":
            return _refused(_taken(name), "taken")
        logger.info("helper %s made in %s", made.id, ctx.session_id)
        return ToolResult(
            ok=True,
            content=json.dumps({"helper_id": made.id, "conversation_id": made.conversation_id}),
            effect="confirmed",
        )


class HelperUpdate(HelperTool):
    definition = ToolDefinition(
        name=UPDATE,
        description=(
            "In a helper's own chat, when the user wants its name or job changed: the new "
            "`name`, `job` or both. `helper` is the id in your context. " + ASKS_BY_CARD
        ),
        input_schema={
            "type": "object",
            "properties": {"helper": _ID, "name": _NAME, "job": _JOB},
            "required": ["helper"],
            "additionalProperties": False,
        },
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        # Named by its check, which runs before the card is made.
        old = self._names.get(str(args.get("helper", "")), "this helper")
        parts = []
        if args.get("name") is not None:
            parts.append(f"name to {_one_line(args['name'])}")
        if args.get("job") is not None:
            parts.append(f"job to {_one_line(args['job'])}")
        return ActionSummary(text=f"Change {old}: {', '.join(parts)}")

    def target(self, args: JSON) -> str:
        return f"helper:{args.get('helper', '')}"

    async def _check(self, args: JSON, ctx: ToolContext) -> OwnHelper | str:
        said = self._said(ctx)
        if said is not None:
            return said
        name, job = args.get("name"), args.get("job")
        if name is None and job is None:
            return "say the new name, the new job or both"
        if name is not None and (wrong := _name_wrong(name)) is not None:
            return wrong
        if job is not None and (wrong := _job_wrong(job)) is not None:
            return wrong
        h = await self._own(args, ctx)
        if isinstance(h, str):
            return h
        store = self._store(ctx)
        if name is not None and store is not None:
            key = _one_line(name).casefold()
            for other in await store.all(ctx.owner_id):
                if other.id != h.id and other.name.casefold() == key:
                    return _taken(other.name)
        return h

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        checked = await self._check(args, ctx)
        if isinstance(checked, str):
            return Precondition.failed(checked)
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        checked = await self._check(args, ctx)
        store = self._store(ctx)
        if isinstance(checked, str) or store is None:
            return _refused(checked if isinstance(checked, str) else _UNAVAILABLE)
        name = _one_line(args["name"]) if args.get("name") is not None else None
        job = _one_line(args["job"]) if args.get("job") is not None else None
        done = await store.update(ctx.owner_id, checked.id, name=name, job=job)
        if done == "taken":
            return _refused(_taken(name or ""), "taken")
        if done is None:
            return _refused(_NO_SUCH, "not_found")
        logger.info("helper %s changed in %s", done.id, ctx.session_id)
        return ToolResult(
            ok=True,
            content=f"changed: it is {inert(done.name)}, {inert(done.job)}",
            effect="confirmed",
        )


def _read_back(name: str, text: str) -> str:
    said = " ".join(text.split()).rstrip(" .")
    return f"Noted for {inert(name)}: {said}."


class _NotesTool(HelperTool):
    async def _check(self, args: JSON, ctx: ToolContext) -> OwnHelper | str:
        said = self._said(ctx)
        if said is not None:
            return said
        try:
            clean(str(args.get("text", "")))
        except RuleError as e:
            return "the note is empty" if e.kind == "empty" else "the note is too long"
        return await self._own(args, ctx)

    async def _why_not(self, args: JSON, ctx: ToolContext) -> str | None:
        checked = await self._check(args, ctx)
        return checked if isinstance(checked, str) else None

    async def _add(self, args: JSON, ctx: ToolContext) -> OwnHelper | ToolResult:
        """Add the note: the helper it was added to, or why not."""
        checked = await self._check(args, ctx)
        if isinstance(checked, str):
            return _refused(checked)
        store = self._store(ctx)
        if store is None or not await store.add_note(
            ctx.owner_id, checked.id, clean(str(args["text"]))
        ):
            full = (
                f"{inert(checked.name)}'s notes are full ({NOTES_MAX:,} characters): tell "
                "the user, and that they can delete the helper and make it again"
            )
            return _refused(full, "full")
        return checked


class HelperNarrow(_NotesTool):
    definition = ToolDefinition(
        name=NARROW,
        description=(
            "In a helper's own chat, when the user says it should stop, never or less often "
            "do or show something ('don't include refunds'): add it to its notes at once. "
            "`helper` is the id in your context; their words, one sentence. "
            "Anything new it should do or look at goes to helper_widen."
        ),
        input_schema=_NOTES_SCHEMA,
        # Only ever narrows what a helper does (`narrows_routine`), so no card.
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
        logger.info("helper %s narrowed in %s", added.id, ctx.session_id)
        return ToolResult(
            ok=True,
            content=(
                "noted: it follows it from now on. Read it back in one sentence: "
                + _read_back(added.name, text)
            ),
            effect="confirmed",
        )


class HelperWiden(_NotesTool):
    definition = ToolDefinition(
        name=WIDEN,
        description=(
            "In a helper's own chat, when the user wants it to do or look at something new: "
            "add it to its notes. `helper` is the id in your context; their words, one "
            "sentence. " + ASKS_BY_CARD
        ),
        input_schema=_NOTES_SCHEMA,
        # What a helper does with nobody watching grows: the user approves it.
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        text = " ".join(str(args.get("text", "")).split())
        # Named by its check, which runs before the card is made.
        name = self._names.get(str(args.get("helper", "")), "this helper")
        return ActionSummary(text=f"Add to {name}: {text}")

    def target(self, args: JSON) -> str:
        return f"helper:{args.get('helper', '')}"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = await self._why_not(args, ctx)
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        added = await self._add(args, ctx)
        if isinstance(added, ToolResult):
            return added
        logger.info("helper %s widened in %s", added.id, ctx.session_id)
        return ToolResult(
            ok=True,
            content=(
                "added: it follows it from now on. Read it back in one sentence: "
                + _read_back(added.name, str(args["text"]))
            ),
            effect="confirmed",
        )


def _title(name: str, request: str) -> str:
    """A handoff's title, within the scheduler's limit: the name, then the request."""
    room = max(TITLE_ASK - len(name) - 2, 1)
    return f"{name}: {_one_line(request)[:room]}"


class HelperAsk(HelperTool):
    definition = ToolDefinition(
        name=ASK,
        description=(
            "Hand a helper work that is its job: it works on `request` in its own chat in "
            "the background, and the answer comes back to this conversation by itself. "
            "`helper` is its id from your context; `request` is what the user asked, in "
            "full. Never wait for it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "helper": _ID,
                "request": {"type": "string", "minLength": 1, "maxLength": MAX_ASK},
            },
            "required": ["helper", "request"],
            "additionalProperties": False,
        },
        # A run of her own tools in the background, as any timed ask: no card.
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    async def _check(self, args: JSON, ctx: ToolContext) -> OwnHelper | str:
        store = self._store(ctx)
        if store is None or self._schedules is None:
            return _UNAVAILABLE
        if ctx.helper_id is not None:
            return _NOT_FOR_HELPERS
        if ctx.read_outside:
            # What she read must not set a helper to work with nobody watching.
            return _ASK_FROM_READING
        if not str(args.get("request", "")).strip():
            return "the request is empty: say what it should do"
        if len(str(args.get("request", "")).strip()) > MAX_ASK:
            return f"the request is too long: keep it under {MAX_ASK} characters"
        h = await store.get(ctx.owner_id, str(args.get("helper", "")))
        if h is None:
            return _NO_SUCH
        name = inert(h.name)
        if h.paused_ms is not None:
            return f"{name} is paused: answer yourself and say so in a few words"
        if await self._held(WORK):
            return f"{name} is at its spend cap: answer yourself and say so"
        return h

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        checked = await self._check(args, ctx)
        if isinstance(checked, str):
            return Precondition.failed(checked)
        return Precondition.passed()

    def _at(self, now_ms: int) -> tuple[str, str]:
        """(zone, local time) for now: the user's zone, else UTC."""
        try:
            zone = zone_of(self._zone() or "").key
        except WhenError:
            zone = "UTC"
        local = datetime.fromtimestamp(now_ms / 1000, ZoneInfo(zone))
        return zone, local.strftime("%Y-%m-%dT%H:%M")

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        checked = await self._check(args, ctx)
        schedules = self._schedules
        if isinstance(checked, str) or schedules is None:
            kind = "from_reading" if checked == _ASK_FROM_READING else "refused"
            return _refused(checked if isinstance(checked, str) else _UNAVAILABLE, kind)
        request = str(args["request"]).strip()
        now = self._clock()
        zone, at_local = self._at(now)
        s = Schedule(
            id=secrets.token_hex(4),
            owner_id=ctx.owner_id,
            kind="ask",
            title=_title(checked.name, request),
            body=request,
            zone=zone,
            at_local=at_local,
            repeat=None,
            # Due now: the scheduler's next tick runs it.
            next_due_ms=now,
            status="active",
            created_ms=now,
            created_in=ctx.session_id,
            helper_id=checked.id,
        )
        await schedules.create(s)
        name = inert(checked.name)
        logger.info("helper %s asked in %s", checked.id, ctx.session_id)
        return ToolResult(
            ok=True,
            content=(
                f"asked {name}: say 'Asking {name}' in one line and carry on; the answer "
                "comes back by itself"
            ),
            effect="confirmed",
        )


def _offered(args: JSON, ctx: ToolContext) -> tuple[str, str]:
    """The name and job an offer shows. After an outside read they are fixed words from
    the area alone: what she read must not write a helper's instructions, even on a card
    the user taps."""
    if not ctx.read_outside:
        return str(args.get("name") or ""), str(args.get("job") or "")
    what = _one_line(label(str(args.get("area", ""))))
    suffix = " helper"
    head = what[: NAME_MAX - len(suffix)]
    return head[:1].upper() + head[1:] + suffix, (
        f"I keep an eye on your {what} and tell you what needs you."
    )


class HelperSuggest(HelperTool):
    definition = ToolDefinition(
        name=SUGGEST,
        description=(
            "Only when your context says to offer a helper for an area: offer one, once, "
            "with the `area` it names and a drafted `name` and one-sentence `job`. The "
            "user sees the offer with Create; say nothing more about it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "area": {"type": "string", "minLength": 1, "maxLength": AREA_MAX},
                "name": _NAME,
                "job": _JOB,
            },
            "required": ["area", "name", "job"],
            "additionalProperties": False,
        },
        # Makes nothing: it keeps the offer, and only a tap makes the helper.
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    async def _why_not(self, args: JSON, ctx: ToolContext) -> str | None:
        if self._store(ctx) is None or self._preferences is None:
            return _UNAVAILABLE
        if ctx.helper_id is not None:
            return _NOT_FOR_HELPERS
        if not await self._offer_open(ctx.session_id, str(args.get("area", ""))):
            return _NOT_OFFERED
        return plain(*_offered(args, ctx))

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = await self._why_not(args, ctx)
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        why = await self._why_not(args, ctx)
        prefs = self._preferences
        if why is not None or prefs is None:
            return _refused(why or _UNAVAILABLE)
        area = str(args["area"])
        async with self._offer_lock:
            # Taken here, with nothing awaited between the look and the taking: a second
            # call in the same reply finds it gone.
            if not self._offer_claim(ctx.session_id, area):
                return _refused(_NOT_OFFERED)
            now = self._clock()
            name, job = _offered(args, ctx)
            offer = {
                "area": area,
                "at_ms": now,
                "name": _one_line(name),
                "job": _one_line(job),
            }
            await prefs.put(ctx.owner_id, OFFER_PREF, offer, now)
        return ToolResult(ok=True, content="offered: say nothing more about it", effect="confirmed")
