"""Reminders and timed asks (AGT-9): set one, list them, cancel one.

`reminder_set` runs at once: it only delivers words she wrote, and is cheap to
cancel. It is refused after she has read outside content in the turn, so a page or
an email can't plant a reminder that speaks in her voice; `schedule_cancel` is
refused then too, so one can't undo what the user set. `ask_schedule` is a
standing instruction that will run on its own, so it shows a card (D6).

`when` is the user's local date and time. She works it out from the time in her
context and confirms what the tool says back.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any, Literal

from trillion.scheduler.store import Kind, Schedule, ScheduleStore, Target
from trillion.scheduler.when import (
    Repeat,
    WhenError,
    next_after,
    read_repeat,
    repeat_words,
    spoken,
    summary,
    zone_of,
)
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

REMINDER, ASK, LIST, CANCEL = "reminder_set", "ask_schedule", "schedule_list", "schedule_cancel"
CATEGORY = "schedule"
# A time this little in the past is the clocks disagreeing, not a mistake.
DRIFT_MS = 60_000
MAX_SAY, MAX_ASK, MAX_TITLE = 500, 1000, 80
_ID = r"^[a-f0-9]{8}$"
_WHEN: dict[str, Any] = {
    "type": "string",
    "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$",
    "description": "The user's local date and time, YYYY-MM-DDTHH:MM. The first, if it repeats.",
}
_TITLE: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_TITLE,
    "description": "A few words to list it by, such as 'Call the dentist'.",
}
_REPEAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "every": {"type": "string", "enum": ["day", "weekday", "week", "month"]},
        "days": {
            "type": "array",
            "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
            "minItems": 1,
        },
        "day": {"type": "integer", "minimum": 1, "maximum": 31},
    },
    "required": ["every"],
    "additionalProperties": False,
    "description": (
        "Leave out to run once. week needs days; month needs day (past a month's end it"
        " means the last day)."
    ),
}
# Where a routine's board lands, as its card says it (the routines spec).
_LANDS: dict[str, str] = {"day": "on Your day", "own": "in its own conversation"}
_FROM_READING = (
    "a reminder can't be set after reading a page, a file or a message in this turn; "
    "tell the user what it would be for, then set it again"
)
_CANCEL_FROM_READING = (
    "a reminder or timed ask can't be cancelled after reading a page, a file or a message"
    " in this turn; tell the user which one it is: they can cancel it in Settings →"
    " Reminders, or ask you again"
)
_NO_SUCH_ROUTINE = 'routine must be "day" or "own", or left out'


def _routine(args: JSON) -> Target | Literal[False] | None:
    """Where a routine lands, None for a plain timed ask, False for anything else."""
    value = args.get("routine")
    if value is None:
        return None
    if value == "day":
        return "day"
    if value == "own":
        return "own"
    return False


def _refused(text: str, kind: str = "refused") -> ToolResult:
    return ToolResult(ok=False, content=text, effect="none", error_kind=kind)


def _new_id() -> str:
    return secrets.token_hex(4)


class _ScheduleTool(BaseTool):
    def __init__(self) -> None:
        self._store: ScheduleStore | None = None
        self._zone: Callable[[], str | None] = lambda: None
        self._clock: Callable[[], int] = lambda: 0
        self._owner = ""

    def bind(
        self,
        store: ScheduleStore,
        *,
        zone: Callable[[], str | None],
        clock: Callable[[], int],
        owner: str,
    ) -> None:
        """Called once at startup with the running process's store, zone and clock."""
        self._store, self._zone, self._clock, self._owner = store, zone, clock, owner

    def _for(self, ctx: ToolContext) -> ScheduleStore | None:
        return self._store if ctx.owner_id == self._owner else None

    def _plan(self, args: JSON, body_key: str, limit: int) -> tuple[str, Repeat | None, int]:
        """(zone, repeat, first due) for what she asked, or WhenError saying why not."""
        zone = self._zone()
        if not zone:
            raise WhenError(
                "the user's timezone is not known: ask them to set it in Settings → Names and tone"
            )
        # Kept by its canonical name, so every system reads the same zone back.
        zone = zone_of(zone).key
        body = str(args.get(body_key, "")).strip()
        if not body:
            raise WhenError(f"{body_key} is empty: say what it is for")
        if len(body) > limit:
            raise WhenError(f"{body_key} is too long: keep it under {limit} characters")
        title = " ".join(str(args.get("title", "")).split())
        if not title or len(title) > MAX_TITLE:
            raise WhenError(f"title must be 1 to {MAX_TITLE} characters")
        repeat = read_repeat(args.get("repeat"))
        first = next_after(str(args.get("when", "")), repeat, zone, self._clock() - DRIFT_MS)
        if first is None:
            raise WhenError("that time has passed: give a time in the future")
        return zone, repeat, first

    async def _create(
        self,
        store: ScheduleStore,
        kind: Kind,
        args: JSON,
        body_key: str,
        limit: int,
        ctx: ToolContext,
        target: Target | None = None,
    ) -> ToolResult:
        try:
            zone, repeat, first = self._plan(args, body_key, limit)
        except WhenError as e:
            return _refused(str(e))
        # An own routine set in a helper's chat is the helper's: it runs there, as it.
        helper = ctx.helper_id if target == "own" else None
        s = Schedule(
            id=_new_id(),
            owner_id=ctx.owner_id,
            kind=kind,
            title=" ".join(str(args["title"]).split()),
            body=str(args[body_key]).strip(),
            zone=zone,
            at_local=str(args["when"]),
            repeat=repeat,
            next_due_ms=first,
            status="active",
            created_ms=self._clock(),
            created_in=ctx.session_id,
            target=target,
            conversation_id=ctx.session_id if helper is not None else None,
            helper_id=helper,
        )
        await store.create(s)
        what = "reminder" if kind == "reminder" else "timed ask"
        if target is not None:
            what = "routine"
        return ToolResult(
            ok=True,
            content=(
                f"{what} {s.id} set for {spoken(first, zone)} ({zone}), {repeat_words(repeat)}."
                " Tell the user that exact time."
            ),
            effect="confirmed",
        )


class ReminderSet(_ScheduleTool):
    definition = ToolDefinition(
        name=REMINDER,
        description=(
            "Remind the user at a time, once or repeating: at that time your words in `say`"
            " are shown to them, and spoken if your voice is on. Use it whenever they ask to"
            " be reminded; never save a reminder as a memory. Runs at once."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "when": _WHEN,
                "say": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_SAY,
                    "description": "What you will say then, to the user, in your own words.",
                },
                "title": _TITLE,
                "repeat": _REPEAT,
            },
            "required": ["when", "say", "title"],
            "additionalProperties": False,
        },
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        store = self._for(ctx)
        if store is None:
            return _refused("reminders are not available", "unavailable")
        if ctx.read_outside:
            return _refused(_FROM_READING, "from_reading")
        return await self._create(store, "reminder", args, "say", MAX_SAY, ctx)


class AskSchedule(_ScheduleTool):
    definition = ToolDefinition(
        name=ASK,
        description=(
            "Do something for the user at a time, once or repeating, such as 'every weekday"
            " at 9, check my email and tell me what's urgent'. At that time you run `ask`"
            " with only the tools that never ask first, and what you find is delivered to"
            " them. Set `routine` to build a board each time. " + ASKS_BY_CARD
        ),
        input_schema={
            "type": "object",
            "properties": {
                "when": _WHEN,
                "ask": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_ASK,
                    "description": "The request, as the user put it.",
                },
                "title": _TITLE,
                "repeat": _REPEAT,
                "routine": {
                    "type": "string",
                    "enum": ["day", "own"],
                    "description": "day: builds their day board. own: its own conversation.",
                },
            },
            "required": ["when", "ask", "title"],
            "additionalProperties": False,
        },
        # A standing instruction that runs on its own: a yes or a tap approves it (D6).
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        ask = " ".join(str(args.get("ask", "")).split())
        try:
            zone, repeat, _ = self._plan(args, "ask", MAX_ASK)
            when = summary(str(args["when"]), repeat, zone)
        except WhenError:
            when = str(args.get("when", ""))
        lands = _LANDS.get(str(args.get("routine")))
        # The card says it is a routine, its name and where it lands: what is approved.
        title = " ".join(str(args.get("title", "")).split())
        tail = f' (a routine, "{title}", {lands})' if lands is not None else ""
        return ActionSummary(text=f"{when}: {ask}{tail}")

    def target(self, args: JSON) -> str:
        return f"schedule:{' '.join(str(args.get('title', '')).split()).lower()}"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if self._for(ctx) is None:
            return Precondition.failed("timed asks are not available")
        if _routine(args) is False:
            return Precondition.failed(_NO_SUCH_ROUTINE)
        try:
            self._plan(args, "ask", MAX_ASK)
        except WhenError as e:
            return Precondition.failed(str(e))
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        store = self._for(ctx)
        if store is None:
            return _refused("timed asks are not available", "unavailable")
        target = _routine(args)
        if target is False:
            return _refused(_NO_SUCH_ROUTINE)
        return await self._create(store, "ask", args, "ask", MAX_ASK, ctx, target)


class ScheduleList(_ScheduleTool):
    definition = ToolDefinition(
        name=LIST,
        description=(
            "List the user's reminders, timed asks and routines: each one's id, what it is,"
            " when it runs next and how it repeats, and how its last run went."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=10.0,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        store = self._for(ctx)
        if store is None:
            return _refused("reminders are not available", "unavailable")
        active = await store.active(ctx.owner_id)
        if not active:
            return ToolResult(ok=True, content="nothing is set", effect="none")
        last = await store.last_results(ctx.owner_id)
        lines = []
        for s in active:
            what = "reminder" if s.kind == "reminder" else "timed ask"
            if s.target is not None:
                what = f"routine, {_LANDS[s.target]}"
            try:
                nxt = spoken(s.next_due_ms, s.zone) if s.next_due_ms is not None else "nothing more"
                when = summary(s.at_local, s.repeat, s.zone)
            except WhenError:
                # Its zone left this computer's zone data: listed all the same.
                nxt, when = "unknown", f"{s.at_local} ({s.zone}), {repeat_words(s.repeat)}"
            run = last.get(s.id)
            ran = run.status if run is not None else "none yet"
            # A paused routine's due time is not moving: saying it would mislead.
            coming = "paused" if s.paused_ms is not None else f"next {nxt}"
            lines.append(f"- {s.id} ({what}) {s.title}: {when}; {coming}; last run {ran}")
        return ToolResult(ok=True, content="\n".join(lines), effect="none")


class ScheduleCancel(_ScheduleTool):
    definition = ToolDefinition(
        name=CANCEL,
        description="Cancel one reminder or timed ask, by its id from schedule_list. Runs at once.",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string", "pattern": _ID}},
            "required": ["id"],
            "additionalProperties": False,
        },
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=10.0,
        trusted_output=True,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        store = self._for(ctx)
        if store is None:
            return _refused("reminders are not available", "unavailable")
        if ctx.read_outside:
            # What she read must not undo what the user set, an approved timed ask least.
            return _refused(_CANCEL_FROM_READING, "from_reading")
        if not await store.cancel(ctx.owner_id, str(args["id"]), self._clock()):
            return _refused("no such reminder or timed ask", "not_found")
        return ToolResult(ok=True, content=f"cancelled {args['id']}", effect="confirmed")
