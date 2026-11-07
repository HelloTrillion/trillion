"""One authenticated conversation over the realtime protocol (PRD §8.3).

A session owns a conversation and the turn counter the confirmation gate
depends on. Only a committed user input advances that counter: a typed
message, or a tap on a confirmation card (VOI-15a). Conversation text is
never logged here; logs carry ids, types and statuses only.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Concatenate, Literal, ParamSpec, TypeVar
from zoneinfo import ZoneInfo

from trillion import __version__
from trillion.api import listening
from trillion.api.draft import Draft, same_words
from trillion.api.jobs import STARTED, Full, Job, goes_to_background
from trillion.api.listening import FOLLOW_UP_S, QUIET_LISTEN_S, Judge, Listener
from trillion.api.runtime import OUT_OF_TURN, OWNER, Runtime
from trillion.api.speech import SendBytes, Speaker, describe
from trillion.api.trace import Trace, Via, ahead_ms
from trillion.brain import compact
from trillion.brain import title as naming
from trillion.brain.loop import (
    INTERRUPTED,
    CallTimed,
    LoopEvent,
    ReplyDelta,
    ToolEnded,
    ToolStarted,
    cut,
    kept_of,
    run_turn,
    tool_schemas,
)
from trillion.brain.loop import (
    QUIET as QUIET_TOOL,
)
from trillion.brain.types import (
    ContextBlock,
    Msg,
    ProviderError,
    TextBlock,
    ToolResultBlock,
    ToolUse,
    ToolUseBlock,
)
from trillion.connectors.stripe_revenue import NAME as STRIPE_REVENUE
from trillion.delivery import door as delivery
from trillion.delivery.door import Delivery, Facts, NotFree, free
from trillion.helpers import needs
from trillion.helpers.store import CONNECTOR_TITLES, OwnHelper
from trillion.memory import present
from trillion.persona import day
from trillion.persona.prompt import (
    JobNote,
    PendingNote,
    capped,
    compaction,
    degraded,
    greeting,
    helper_overlay,
    volatile_context,
)
from trillion.scheduler.ask import HANDOFF_RUN, ROUTINE_ID, ROUTINE_RUN
from trillion.scheduler.store import https_link
from trillion.spend import desk as spend
from trillion.spend.caller import as_caller
from trillion.spend.gate import VoiceCapped
from trillion.spend.types import Caller, Work
from trillion.spine import labels
from trillion.spine.confirm import Status
from trillion.spine.contract import JSON, Risk, ToolCall, ToolContext, ToolDefinition
from trillion.spine.loop_guard import LoopGuard
from trillion.spine.refusal import plain_no
from trillion.spine.router import Router, RouterResponse
from trillion.spine.untrusted import is_outside, wrap
from trillion.storage.sqlite.conversation_store import USER_INPUTS, ConversationKind, Kind
from trillion.tools.folders import View, host_note, open_place
from trillion.tools.helper import ASK as HELPER_ASK
from trillion.tools.helper import CATEGORY as HELPER_CATEGORY
from trillion.tools.helper import CREATE as HELPER_CREATE
from trillion.tools.helper import DECLINED_PREF as HELPER_DECLINED
from trillion.tools.helper import NARROW as HELPER_NARROW
from trillion.tools.helper import NOTED_PREF as HELPER_NOTED
from trillion.tools.helper import OFFER_PREF as HELPER_OFFER
from trillion.tools.helper import SUGGEST as HELPER_SUGGEST
from trillion.tools.helper import UPDATE as HELPER_UPDATE
from trillion.tools.helper import WIDEN as HELPER_WIDEN
from trillion.tools.helper import card_draft as helper_draft
from trillion.tools.helper import card_look as helper_look
from trillion.tools.helper import make as make_helper
from trillion.tools.helper import plain as plain_helper
from trillion.tools.quiet import asks
from trillion.tools.routine import CATEGORY as ROUTINE_CATEGORY
from trillion.tools.routine import for_session as routine_tools
from trillion.tools.routine import mine as routine_here
from trillion.tools.routine import named as routine_named
from trillion.tools.schedule import ASK as ASK_SCHEDULE
from trillion.tools.schedule import OWN_LANDS as ROUTINE_OWN_LANDS
from trillion.tools.web import FETCH
from trillion.voice.endpoint import NOTHING_AFTER_S, Verdict
from trillion.voice.overlap import confirm, decide, is_filler, words_of
from trillion.voice.timeline import LEAD_S, TAIL_S, Timeline
from trillion.voice.types import VoiceError, Word
from trillion.voice.whose import EchoShare, matched, whose
from trillion.workspace import live
from trillion.workspace.board import Board
from trillion.workspace.board import summary as board_summary
from trillion.workspace.service import for_page

logger = logging.getLogger(__name__)

PROTOCOL = 1
MAX_TEXT = 32_000
# How long a barge-in waits for her reply to stop before listening. An action she was
# carrying out runs to its end first; this bounds the wait, not the action.
BARGE_WAIT_S = 3.0
# A microphone that sends nothing at all for this long is treated as not working.
NO_AUDIO_S = 3.0
# Before the first frame: a USB microphone sends only zeros for a second or more while
# it wakes, which the page holds back, and says plainly itself after 4 s (WAKE_S).
FIRST_AUDIO_S = 6.0
# One frame of audio from the page is small; anything much larger is not audio.
MAX_AUDIO_FRAME = 64_000
# While the user talks over her, what they said is weighed at least this often.
OVERLAP_TICK_S = 0.05
# Watching while she speaks ends if nothing of hers has started playing for this long:
# a reply that never sounds, a long tool, a page that stopped reporting (VOI-9).
WATCH_HOLD_S = 15.0
SURFACE = "web"
# The day board is built at most this often per conversation, however often it is asked.
DAY_BUILD_EVERY_MS = 60_000
# A reload dropped because something was saved meanwhile is tried again this soon, and
# given up on after this long if the page never comes free (the page catches up when
# it next opens the conversation).
RELOAD_AGAIN_S = 0.2
RELOAD_GIVE_UP_S = 30.0
# The error a page is sent when a routine is building in its conversation.
ROUTINE_BUILDING = "routine_building"
# How the page shows the saved recipe: a small line, never the user's words.
DAY_BUILD_SHOWN = "Building your day"
__all__ = ["OUT_OF_TURN", "PROTOCOL", "Session"]
Send = Callable[[dict[str, Any]], Awaitable[None]]

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _calls_as(
    caller: Caller,
    work: Work | None = None,
) -> Callable[
    [Callable[Concatenate[Session, _P], Coroutine[Any, Any, _R]]],
    Callable[Concatenate[Session, _P], Coroutine[Any, Any, _R]],
]:
    """Every paid call made inside the method counts as `caller`, for this session's
    conversation (BRN-7), and so does every task it starts. On each way into the
    session, since some (a notice the door hands over, a turn it begins) start on a
    task of someone else's."""

    def wrap(
        method: Callable[Concatenate[Session, _P], Coroutine[Any, Any, _R]],
    ) -> Callable[Concatenate[Session, _P], Coroutine[Any, Any, _R]]:
        @functools.wraps(method)
        async def run(session: Session, /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
            with as_caller(caller, session.id, work=work):
                return await method(session, *args, **kwargs)

        return run

    return wrap


@dataclass(kw_only=True)
class Overlap:
    """The user started talking over her (VOI-9): what they meant is being decided."""

    listener: Listener
    started: float
    # The reply being talked over, for telling her own voice from theirs.
    speaker: Speaker | None
    watcher: asyncio.Task[None] | None = None
    # Each frame wakes the watcher, so a word stops her as soon as it is heard.
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    # Decided: it is becoming the user's turn while her reply stops.
    promoting: bool = False
    # She finished being heard meanwhile: carrying on opens the follow-up window.
    ended: bool = False
    # Opened by the page to watch while she speaks: every frame is streamed, and this
    # decides when to duck her. False for the page's own `overlap`, already ducked.
    watch: bool = False
    # Still only watching: nothing heard so far is anything but her.
    watching: bool = False
    # Words said before this, on the recognizer's clock, are already dealt with.
    past_s: float = 0.0
    # Her reply on the listener's clock, for telling her echo from them (#102). None
    # for the page's own `overlap`, which has no clock of hers.
    timeline: Timeline | None = None
    # When a sentence of hers last started playing, or the watch began.
    her_at: float = 0.0
    # How many words were heard when she was ducked: what follows confirms them.
    ducked_on: int = 0
    # Words heard over her so far, and when that last changed (`_quiet_s`).
    words_seen: int = 0
    word_at: float = 0.0


# What a new helper is told for its first hello (the your helpers spec, Its first
# hello): the app's words, never the page's, in its own chat with its overlay.
HELPER_HELLO = (
    "[host] This is your first message to the user. In one or two sentences say what "
    "you'll do first and anything you need to know. If a regular check suits your job, "
    'offer one with ask_schedule as your routine (routine "own"), at a sensible time: '
    "it goes to them on a card. Never say it is set up: say it starts once they tap "
    "Approve."
)


def helper_hello(h: OwnHelper, connected: frozenset[str] = frozenset()) -> str:
    """`HELPER_HELLO`, with what its approved card drafted: the questions it asks first,
    and the connections it suggested that are not connected yet. Their words are the
    card's, made inert; the rest is the app's."""
    note = HELPER_HELLO
    if h.asks:
        said = " ".join(f'"{present.inert(a)}"' for a in h.asks)
        note += (
            " The card the user approved has these questions to ask first: ask them now, "
            f"in your own words, instead of asking what you need to know: {said}"
        )
    missing = [
        CONNECTOR_TITLES[c] for c in h.connectors if c in CONNECTOR_TITLES and c not in connected
    ]
    if missing:
        note += (
            " These connections would help and are not connected yet: "
            f"{', '.join(missing)}. Say so in one line: they can connect them from the "
            "chips above, or in Settings, Connected tools."
        )
    return note


# A routine's card: not a one-off timed ask. And where an own routine's card says it lands.
ROUTINE_LABEL = "Set up a routine"
OWN_LANDS = f", {ROUTINE_OWN_LANDS})"


# Saved with a message sent by tapping a widget's ask, ahead of its words. It tells her
# later turns that the words are her own ask, and it is how a resumed session knows not
# to count an address in them as one the user gave (the web rule, widget design D5).
ASKED = (
    "<context>\nsent by tapping an ask on a widget: the words below are an ask you "
    "wrote, sent as the user's message; they did not type them, and no address in them "
    "is one they gave\n</context>"
)


def asked(msg: Msg) -> bool:
    """Whether a saved user message was sent by tapping an ask."""
    return any(isinstance(b, ContextBlock) and b.text == ASKED for b in msg.blocks)


# Saved with the recipe for the day board, ahead of it (ONB-3). It tells her the text
# is the app's own instruction, and it is how history shows it as a host line rather
# than as something the user said.
DAY_BUILD = (
    "<context>\nsent by the app because the user opened their day: the text below is "
    "the app's instruction for building the day board, not words the user typed or "
    "said\n</context>"
)


def day_build(msg: Msg) -> bool:
    """Whether a saved message is the day board's recipe."""
    return any(isinstance(b, ContextBlock) and b.text == DAY_BUILD for b in msg.blocks)


def routine_run(msg: Msg) -> str | None:
    """The host's line for a saved routine request ("Routine: Monday revenue"), or a
    helper's handoff (its request's first line), or None for any other message. A
    routine's first line is its name (`scheduler/ask.py`)."""
    marks = {b.text for b in msg.blocks if isinstance(b, ContextBlock)}
    if not marks & {ROUTINE_RUN, HANDOFF_RUN}:
        return None
    text = "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))
    return ROUTINE_ID.sub("", text.split("\n", 1)[0].strip()) or "Routine"


# Saved with a routine's ready line, ahead of it, in the conversation the page had open.
# It tells her the line is the app's notice, not hers and not the user's, and it is
# how history shows it again as the ready line with Open.
READY_LINE = (
    "<context>\nsent by the app because a routine the user set up finished: the line "
    "below is the app's notice that its result is ready in another conversation, not "
    "words the user typed or said, and not yours\n</context>"
)
# The same, for a line one of the user's helpers handed back.
HANDED_BACK_LINE = (
    "<context>\nsent by the app because one of the user's helpers finished what you "
    "asked it: the line below is the app's notice that its answer is ready in the "
    "helper's conversation, not words the user typed or said, and not yours\n</context>"
)
_READY_NOTES = frozenset({READY_LINE, HANDED_BACK_LINE})
# The conversation a ready line opens, on its last line.
_READY_OPENS = re.compile(r"\n\(conversation ([A-Za-z0-9_-]{1,64})\)$")


def ready_msg(title: str, conversation_id: str | None, *, handoff: bool = False) -> Msg:
    """A ready line as saved: the app's note, the line, and where it opens."""
    opens = f"\n(conversation {conversation_id})" if conversation_id else ""
    note = HANDED_BACK_LINE if handoff else READY_LINE
    return Msg(role="user", blocks=(ContextBlock(text=note), TextBlock(text=title + opens)))


def ready_line(msg: Msg) -> dict[str, str] | None:
    """A saved ready line as the page shows it, or None for any other message."""
    if not any(isinstance(b, ContextBlock) and b.text in _READY_NOTES for b in msg.blocks):
        return None
    text = "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))
    opens = _READY_OPENS.search(text)
    shown = {"role": "host", "text": _READY_OPENS.sub("", text).strip()}
    return {**shown, "conversation_id": opens.group(1)} if opens else shown


# Saved ahead of a watcher's heads-up (the speaking up spec): the app's record of a
# notice it showed, so a reload draws it again with its Join, detail and held reason.
# By the notice's id only: a find's detail and link are someone else's words, kept in
# the notice, never here where the model rereads them. Never her message (PER-11).
HEADS_UP_NOTE = (
    "<context>\nsent by the app: it showed the user a heads-up from what it watches. "
    "The text below is the app's record of it, not words the user typed or said, and "
    "not yours; your line for it, if you had one, follows\n</context>"
)
_NOTE_ID = re.compile(r"^\(notice ([A-Za-z0-9_-]{1,64})\)$", re.MULTILINE)
_NOTE_HELD = re.compile(r"^\(held: ([^\n()]{1,80})\)$", re.MULTILINE)
_NOTE_WORDED = "(her line follows)"


def heads_up_msg(d: Delivery) -> Msg:
    """A heads-up as the app keeps it: its title, its notice, why she held it, and
    whether her line follows."""
    lines = [d.title, f"(notice {d.id})"]
    if d.quiet:
        lines.append(f"(held: {d.quiet})")
    if d.text:
        lines.append(_NOTE_WORDED)
    blocks = (ContextBlock(text=HEADS_UP_NOTE), TextBlock(text="\n".join(lines)))
    return Msg(role="user", blocks=blocks)


@dataclass(frozen=True, kw_only=True)
class _HeadsUpNote:
    id: str
    title: str
    quiet: str | None
    worded: bool


def heads_up_note(msg: Msg) -> _HeadsUpNote | None:
    """A saved heads-up note, or None for any other message."""
    if not any(isinstance(b, ContextBlock) and b.text == HEADS_UP_NOTE for b in msg.blocks):
        return None
    text = "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))
    found = _NOTE_ID.search(text)
    if found is None:
        return None
    held = _NOTE_HELD.search(text)
    lines = text.split("\n")
    return _HeadsUpNote(
        id=found.group(1),
        title=lines[0],
        quiet=held.group(1) if held else None,
        worded=_NOTE_WORDED in lines,
    )


# Saved with a background read's result, ahead of it (the background reads spec). It
# tells her the note is the app's, carrying what she read, and it is how history shows
# it again as a small host line ("Stripe numbers are in").
BACKGROUND = (
    "<context>\nsent by the app because a read you started in the background ended: the "
    "text below is the app's note with its result, not words the user typed or said\n"
    "</context>"
)
_IN = (
    "Say in one short line that it is in, show it on the board if it is better seen, "
    "then say at most two sentences on what matters."
)
_NOT_IN = "Say plainly, once, that it did not come back, and offer to try again."
# A note's host line, from its first line: one note per message.
_NOTE_TITLE = re.compile(r"\[host\] ([^\n]+?)\. The background read you started \(")
# The read's own timeout ends it first; this bounds what the router does around it.
JOB_MARGIN_S = 5.0
# How often a read that ended waits again while a routine builds in its conversation.
HOLD_WAIT_S = 0.2
# After a Stop, or a note that could not be saved, the follow-up waits this long for
# the next pause: never at the rest the Stop itself made.
PAUSE_AGAIN_S = 0.5


def background_title(name: str, connector: str | None, label: str, *, ok: bool) -> str:
    """The host line for a background read: by its connector's title where it has one
    ("Stripe numbers are in"), else by what it read or its label."""
    if name == STRIPE_REVENUE:
        subject, many = f"{connector or 'Stripe'} numbers", True
    elif name == FETCH:
        subject, many = "The page", False
    elif connector:
        subject, many = f"{connector} results", True
    else:
        subject, many = label, False
    if not ok:
        return f"{subject} didn't come back"
    return f"{subject} {'are' if many else 'is'} in"


def background_note(title: str, job: Job, r: RouterResponse) -> str:
    """What she is told when a background read ends: the host's words, and its result
    as the model would have seen it, already wrapped as someone else's (SPN-5)."""
    ok = r.status == "succeeded"
    ended = "is in" if ok else "did not come back"
    head = f"[host] {title}. The background read you started ({job.tool}, job {job.id}) {ended}."
    return f"{head} Result: {r.to_model()}\n{_IN if ok else _NOT_IN}"


# The host note for the one nudge after a quiet `listening` window passes in silence
# (VOI-7). Saved as a tap's note, so its turn is the host's: she may not stay quiet on
# it, which is also what keeps a nudge from following a nudge.
NUDGE = (
    "[host] They trailed off mid-thought and have been quiet a few seconds. If you can "
    "tell what they were reaching for, offer it back in a few words as a question. If "
    "not, say one short line inviting them to go on. Your own words."
)


def quiet_done(use: ToolUseBlock, result: ToolResultBlock) -> bool:
    """A saved `stay_quiet` `done` that was accepted: the user's message before it was
    seen, and needs no reply (VOI-7)."""
    return (
        use.name == QUIET_TOOL
        and use.args.get("kind") == "done"
        and result.tool_use_id == use.id
        and not result.is_error
    )


def background_msg(note: str) -> Msg:
    """A note saved with no session to build its context: the app's mark and the note."""
    return Msg(role="user", blocks=(ContextBlock(text=BACKGROUND), TextBlock(text=note)))


def background_lines(msg: Msg) -> list[dict[str, str]] | None:
    """A saved background note as the page shows it, or None for any other message."""
    if not any(isinstance(b, ContextBlock) and b.text == BACKGROUND for b in msg.blocks):
        return None
    text = "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))
    found = _NOTE_TITLE.match(text)
    return [{"role": "host", "text": found.group(1)}] if found else []


async def background_read(
    router: Router, d: ToolDefinition, u: ToolUse, ctx: ToolContext
) -> RouterResponse:
    """The read itself, through the router like any call, with the call's own context."""
    try:
        return await asyncio.wait_for(
            # Its own guard: the turn it came from is over.
            router.dispatch(ToolCall(name=u.name, args=u.args), ctx, LoopGuard()),
            d.timeout_s + JOB_MARGIN_S,
        )
    except TimeoutError:
        return RouterResponse(
            status="failed", detail=f"timed out after {d.timeout_s:g}s", error_kind="timeout"
        )


def _chip(r: RouterResponse) -> str:
    """What a call's chip says when it ends: why it was refused, its status, or that it
    is working in the background."""
    if r.status == "started":
        return "background"
    return r.reason.value if r.reason is not None else r.status


@dataclass(frozen=True, kw_only=True)
class _Finished:
    """A background read that ended: its note, the read a widget built from it remembers,
    and the title its ready line has."""

    note: str
    read: tuple[str, JSON] | None
    title: str
    job_id: str


async def background_ended(rt: Runtime, job: Job, r: RouterResponse) -> None:
    """A background read ended: to the page on its conversation, or kept there for
    when it is next opened, with a ready notice through the door."""
    d = rt.registry.definition(job.tool)
    tool = rt.registry.get(job.tool)
    title = background_title(
        job.tool,
        live.connector(job.tool, tool),
        labels.tool_label(job.tool, labels.connected(d, tool)),
        ok=r.status == "succeeded",
    )
    finished = _Finished(
        note=background_note(title, job, r),
        read=(job.tool, job.args) if r.status == "succeeded" else None,
        title=title,
        job_id=job.id,
    )
    cid = job.conversation_id
    # A routine building there is the one writer until it is done.
    while cid in rt.routine_holds and cid not in rt.by_conversation:
        await asyncio.sleep(HOLD_WAIT_S)
    session = rt.by_conversation.get(cid)
    if session is not None:
        await session.job_ended(finished)
        return
    await keep_for_later(rt, cid, [finished])


async def keep_for_later(
    rt: Runtime, cid: str, finished: Sequence[_Finished], *, notify: bool = True
) -> None:
    """Notes for a conversation no page is on: saved there, and a ready notice for each
    through the door. Called with no await since the look that found no page there, it
    holds the conversation as a routine does, so no turn writes between its messages.
    A page that opened meanwhile read the history before the notes: it catches up, and
    gets no notice for what it now shows."""
    rt.routine_holds.add(cid)
    opened: Any = None
    try:
        for f in finished:
            # Looked at before each: a conversation deleted meanwhile gets nothing more.
            if not await rt.conversations.exists(cid, OWNER):
                return
            await rt.conversations.append(cid, OWNER, "tap", background_msg(f.note), rt.clock())
            if f.read is not None:
                rt.jobs.keep_read(cid, *f.read)
        opened = rt.by_conversation.get(cid)
        if notify and opened is None and rt.schedules is not None and rt.door is not None:
            for f in finished:
                n = await rt.schedules.ready_notice(
                    OWNER, f"job-{f.job_id}", f.title, rt.clock(), cid
                )
                rt.jobs.keep_notice(cid, n.id)
                await rt.door.added(n)
    finally:
        rt.routine_holds.discard(cid)
    if opened is not None:
        await opened.catch_up()


async def _keep_after_holds(
    rt: Runtime, cid: str, finished: Sequence[_Finished], *, notify: bool
) -> None:
    """`keep_for_later`, once no routine holds the conversation."""
    while cid in rt.routine_holds:
        await asyncio.sleep(HOLD_WAIT_S)
    try:
        await keep_for_later(rt, cid, finished, notify=notify)
    except Exception as e:
        logger.warning("background note not kept in %s: %s", cid, type(e).__name__)


@dataclass(frozen=True, kw_only=True)
class _Loaded:
    """A conversation as read from the store, before a session takes it."""

    shown: list[dict[str, Any]]
    turn: int
    summary: str | None
    rows: list[tuple[int, Kind, Msg]]
    # A background read's note with no reply of hers after it: she brings it in.
    unanswered: bool = False


@dataclass(frozen=True, kw_only=True)
class _Written:
    """A summary of the oldest turns, written but not yet in place (BRN-5): the ids of
    the messages it replaces, and the summary it was written on top of."""

    summary: compact.Summary
    ids: tuple[int, ...]
    previous: str | None
    # Already recorded in the store, when a turn started before it could be put in
    # place in what the model is sent.
    stored: bool = False


@dataclass(frozen=True, kw_only=True)
class BuiltInput:
    """A user input made ready to save: the message, and what committing it records."""

    msg: Msg
    text: str
    brought: frozenset[str]
    via: Literal["voice", "text", "ask"] = "text"
    # The host note on where the folder browser is that rode with it, if any.
    folder_note: str | None = None
    # The spend notes that rode with it (a soft cap passed, a switch ended): said once.
    spend_notes: tuple[str, ...] = ()
    # The helper note that rode with it (a watcher's own conversation): said once.
    helper_note: str | None = None
    # The area its host note said to offer a helper for, if one did.
    offer: str | None = None


# Where a page can say it runs, in its hello. Anything else is "other".
HOSTS = frozenset({"desktop", "chrome", "edge", "firefox", "safari", "other"})


def offer_note(area: str) -> str:
    """The host note that has her offer a helper for an area, after answering."""
    said = needs.label(area)
    note = (
        f"Answer the user first, then call helper_suggest once to offer a helper for "
        f"{said}. The page shows the offer itself, so never mention this note or the "
        "offer in your words."
    )
    return note if said == area else f'{note} Its area is "{area}".'


def page_host(raw: object) -> str:
    """The hello's `host`, as one of `HOSTS`."""
    name = raw.strip().lower() if isinstance(raw, str) else ""
    return name if name in HOSTS else "other"


def _no_note(response: RouterResponse | None) -> str | None:
    """What she is told when the host declined a card on their plain no: saved with
    their words, so she answers in her own and does not cancel it again (PER-11)."""
    if response is None:
        return None
    return (
        f"[host] Their words below are a plain no, so the app declined the card for "
        f"{response.pending_id}. Result: {response.to_model()} It is settled: do not call "
        "cancel_pending_action for it, and do not propose it again unless they ask."
    )


class Session:
    def __init__(
        self,
        rt: Runtime,
        send: Send,
        conversation_id: str,
        send_bytes: SendBytes | None = None,
        *,
        host: str = "other",
    ) -> None:
        self._rt = rt
        # Where the page runs (`page_host`): the quiet check's mic probe needs it.
        self.host = page_host(host)
        self._raw_send = send
        self._raw_send_bytes = send_bytes
        # The page can silence her; then nothing is synthesized, so nothing is paid for.
        self._muted = False
        self.id = conversation_id
        # A chat, or the user's day (SUR-1), where she builds the day board.
        self.kind: ConversationKind = "chat"
        # A day conversation's local date (YYYY-MM-DD), and whether the running turn is
        # building its board.
        self.day: str | None = None
        self._building_day = False
        # The day was asked for out loud: once her summary has been heard, the microphone
        # reopens as after a spoken turn. Only the microphone: the build stays the app's
        # input and can approve nothing.
        self._listen_after = False
        # How her last turn chose to say nothing, if it did, until its turn is over and
        # the session acts on it (VOI-7), and the turn number its silence was heard as:
        # a page's "played" for that silence never reopens the microphone.
        self._quiet: str | None = None
        self._quiet_turn: int | None = None
        # The watch that goes on as the quiet window once her silence is heard.
        self._quiet_watch: Overlap | None = None
        # The microphone left open after a quiet `listening`: if it passes in silence,
        # she nudges once. Let go with the listener, by anything that stops it.
        self._window: Listener | None = None
        # The page said she was heard to the end while her turn was still finishing:
        # the follow-up is decided once the turn is over.
        self._ended_early = False
        # The number the running turn's speech is heard as: only its own "played" is
        # remembered while it finishes, never an earlier turn's arriving late.
        self._playing: int | None = None
        # The messages the model is sent, after the latest summary, each with its kind
        # and its id in the store. The whole conversation stays in the store.
        self._history: list[Msg] = []
        self._kinds: list[Kind] = []
        self._ids: list[int] = []
        # Messages this session has saved, counted: a reload that read the store while
        # one was saved would lose it, and is dropped (`reloaded`).
        self._saves = 0
        self._summary: str | None = None
        # A summary of the oldest turns being written in the background, and one written
        # while a turn ran: it is put in place at the next pause between turns (BRN-5).
        self._compacting: asyncio.Task[None] | None = None
        self._written: _Written | None = None
        # How long the history was when a summary last failed: not tried again in the
        # background until it has grown well past that.
        self._compact_failed_at: int | None = None
        # The turns in which she read something from outside: a page, a file, or a
        # timed ask's answer, which may quote an email (MEM-5).
        self._outside: set[int] = set()
        self._turn = 0
        self._task: asyncio.Task[None] | None = None
        # Notes about taps that arrived mid-turn, delivered to the model afterwards.
        self._queued: list[str] = []
        # The cards her last reply raised: only one of those can a plain no end (SPN-2f).
        self._asked_last: set[str] = set()
        self._closed = False
        self._greeted = False
        self._orb = ""
        # Whether the orb's "speaking" is a notice's: the page stays free to send then.
        self._orb_notice = False
        # The orb's state between turns: idle, or error after a failure.
        self._rest = "idle"
        # Memories already brought along in this conversation: each rides once, with
        # the message that first called for it, and is never repeated.
        self._brought: set[str] = set()
        self._listener: Listener | None = None
        # The user is talking over her, and what they meant is being decided (VOI-9).
        self._overlap: Overlap | None = None
        # Her first call, started while the user is probably finishing (VOI-12); then,
        # once it is the turn's own, the one the turn replays. Whether one was started
        # in this stretch of listening, for the trace.
        self._draft: Draft | None = None
        self._using: Draft | None = None
        self._drafted = False
        self._watchdog: asyncio.Task[None] | None = None
        self._last_audio = 0.0
        # How the current turn was said. A spoken yes is recorded as spoken (SPN-14).
        # A tap on a widget's ask is "ask": typed, except that it can never approve.
        self._via: Literal["voice", "text", "ask"] = "text"
        # How long turns take (ONB-11): the next turn's start, the running turn's
        # trace, and the last one saved, which the page's "audible" may still reach.
        self._arrived: Trace | None = None
        self._trace: Trace | None = None
        self._last_trace: Trace | None = None
        self._timed = 0
        self._mic_setup: dict[str, Any] = {}
        self._warming: asyncio.Future[Any] | None = None
        # What the door judges "free" by (VOI-17): the last turn sent to be heard and
        # when its audio finished sending, and the last one the page said it played.
        self._spoken: tuple[int, int] | None = None
        self._played: int | None = None
        # She is saying a sentence of her own outside a turn: a leg is down (VOI-14).
        self._saying = False
        # Speaking notices, when that is the session's task: user input stops it.
        self._notice_task: asyncio.Task[None] | None = None
        # User input being taken in: nothing is delivered over it (VOI-17).
        self._taking = 0
        # Until the page has its history and cards, a notice would arrive before them.
        self._started = False
        # The last turn whose number speech went out under.
        self._voiced: int | None = None
        # The running turn's voice: the page reports to it each sentence it starts
        # playing, so an interrupted reply keeps what was heard (D8).
        self._speaker: Speaker | None = None
        # Her latest spoken reply on the recognizer's clock (#102): each sentence, how
        # loud it was and when it played. Kept until the next reply begins, as her echo
        # comes back after she ends. The turn number its voice plays under (None while
        # a watch waits for the reply's voice), and the listener whose clock it is on.
        self._timeline: Timeline | None = None
        self._timeline_turn: int | None = None
        self._clock: Listener | None = None
        # How much of her comes back through the microphone, learned from her words,
        # and which words it was learned from already.
        self._echo = EchoShare()
        self._learned: set[tuple[str, float]] = set()
        self._aligned: set[tuple[str, float]] = set()
        # The sentence of the timeline's reply last reported playing, and when (the
        # loop's clock): a watch opened mid-reply places it from that.
        self._reported: tuple[int, float] | None = None
        # The user's spoken words are being collected and saved: a cut-in waits, or
        # cancelling her turn would lose them.
        self._collecting = False
        # Her last spoken reply, saved whole, while it may still be playing: its voice
        # and its id in the store. A barge before the model has been sent it trims it
        # to what was heard (D8). Gone once anything is saved or sent after it.
        self._tail: tuple[Speaker, int] | None = None
        # The user just pressed Stop: notices that arrive straight after it are shown,
        # not spoken. Cleared at the first rest after it (VOI-9).
        self._stopped = False
        # When the user last did something here. The live page is the latest (SUR-9).
        self.active_ms = rt.clock()
        # Who named this conversation, if anyone, and her naming task (SUR-1). She
        # names it once, after her first reply, and never over the user's own name.
        self._title_source: str | None = None
        self._naming: asyncio.Task[None] | None = None
        # The routine tools a page's turn here is offered, and the routine named in her
        # context, read again before each turn.
        self._routine_tools: frozenset[str] = frozenset()
        # A dropped reload, tried once more when the page is free (`catch_up`).
        self._retry: asyncio.Task[None] | None = None
        self._routine = ""
        # The helper whose own chat this is, if it is one, and in any other the helpers
        # not paused, as (id, name, job): read again with the routines.
        self._helper: OwnHelper | None = None
        self._helpers: list[tuple[str, str, str]] = []
        # The last look failed: no helper tool is offered until one works.
        self._helpers_unread = False
        # (turn, area): the user's turn whose host note said to offer a helper for an
        # area. helper_suggest is offered then only, and only once.
        self._offering: tuple[int, str] | None = None
        # (local date, area): what the last look for an offer found, looked at once a day.
        self._offer_looked: tuple[str, str | None] | None = None
        # Background reads that ended here, waiting for a pause to be brought in, and
        # whether a saved one still waits for her (the background reads spec).
        self._finished: list[_Finished] = []
        self._unanswered = False
        # The follow-up answers a spoken conversation: the microphone comes back after it,
        # and a barge over it keeps what was heard.
        self._aloud = False
        # Looks again once a page that never says it played her reply stops holding back.
        self._grace: asyncio.TimerHandle | None = None
        # Looks again at the next pause after a Stop, or after a save that failed.
        self._again: asyncio.TimerHandle | None = None
        # Closing because the conversation opened elsewhere: that page brings notes in.
        self._replacing = False
        # Where this page's folder browser is (None: the places list), and the note
        # her next input carries after the user moved it themselves.
        self.folder_place: Path | None = None
        self._folder_note: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    @classmethod
    async def start(
        cls,
        rt: Runtime,
        send: Send,
        resume: str | None,
        send_bytes: SendBytes | None = None,
        *,
        host: str = "other",
    ) -> Session:
        resumed = bool(resume) and await rt.conversations.exists(str(resume), OWNER)
        if resumed:
            # One conversation, one live session. Two would each keep their own copy of
            # the history and interleave what they save. The older one lets its turn
            # finish recording, and only then is the history read.
            other = rt.by_conversation.get(str(resume))
            if other is not None:
                await other.replaced()
        session = cls(rt, send, str(resume) if resumed else uuid.uuid4().hex, send_bytes, host=host)
        shown: list[dict[str, Any]] = []
        if resumed:
            loaded = await session._read()
            session._take(loaded)
            shown = loaded.shown
            found = await rt.conversations.kind_of(session.id, OWNER)
            session.kind, session.day = found if found is not None else ("chat", None)
            if rt.web is not None:
                # The addresses the user gave earlier in this conversation stay openable.
                for kind, msg in await rt.conversations.history(session.id, OWNER):
                    if kind == "user" and not asked(msg):
                        for b in msg.blocks:
                            if isinstance(b, TextBlock):
                                rt.web.ledger.said(session.id, b.text)
        else:
            await rt.conversations.create(session.id, OWNER, rt.clock())
        title, session._title_source = await rt.conversations.title_of(session.id, OWNER)
        await session._send(
            "session",
            id=session.id,
            title=title,
            protocol=PROTOCOL,
            agent_name=rt.identity.agent_name,
            onboarding=rt.onboarding(),
            version=__version__,
            model=session._model(),
            speech=session._speech(),
            listening=session._listening(),
            granted_roots=[str(r) for r in rt.granted_roots],
        )
        rt.sessions.add(session)
        rt.by_conversation[session.id] = session
        # Nothing is built while no page is open: this one's opening sets it going again.
        rt.helpers_changed()
        try:
            await session._send("history", messages=shown)
            # The conversation's board as it was left, open or closed (D3).
            board = for_page(await session._board(), rt.zone(), rt.refreshing(session.id))
            await session._send("board", board=board)
            running = len(rt.jobs.running(session.id))
            if running:
                await session._send("jobs", count=running)
            for a in await session._open_actions():
                await session._send_card(a.id)
            # A spend card still to be answered, with its choices as they are now, and a
            # switch in force, for the banner. A switch made for a month now over ends
            # first.
            await session._spend_tick()
            for card in rt.spend.cards_for(session.id):
                card = await session._fresh_card(card)
                await session._send("spend.card", **card.to_page())
            banner = rt.spend.banner()
            if banner:
                await session._send("spend.switch", switches=banner)
            session._started = True
            await session._state("idle")
        except BaseException:
            # Never left half started: the door would take it for the live page and
            # hold every notice for it, with no system notification instead.
            await session.close()
            raise
        return session

    async def _read(self) -> _Loaded:
        """Read the conversation from the store: the page and the turn count get every
        message; the model gets the latest summary and what came after it (BRN-5)."""
        rt = self._rt
        shown: list[dict[str, Any]] = []
        turn = 0
        unanswered = False
        # A heads-up whose line is the next message.
        awaiting: dict[str, Any] | None = None
        # The user's last message as shown, and her call to stay quiet after it, if she
        # made one: answered and accepted, it marks that message seen (VOI-7).
        said: dict[str, Any] | None = None
        hushed: ToolUseBlock | None = None
        for kind, msg in await rt.conversations.history(self.id, OWNER):
            turn += kind in USER_INPUTS
            if kind == "tool_results":
                for b in msg.blocks:
                    if isinstance(b, ToolResultBlock) and hushed is not None and said:
                        if quiet_done(hushed, b):
                            said["seen"] = True
                hushed = None
                continue
            uses = [b for b in msg.blocks if isinstance(b, ToolUseBlock)]
            hushed = uses[0] if kind == "assistant" and len(uses) == 1 else None
            text = "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))
            routine = routine_run(msg) if kind == "tap" else None
            ready = ready_line(msg) if kind == "tap" else None
            brought = background_lines(msg) if kind == "tap" else None
            note = heads_up_note(msg) if kind == "tap" else None
            if kind == "assistant":
                unanswered = False
            if note is not None:
                # Drawn again as it arrived; her line, the next message, joins it.
                shown.append(await self._heads_up(note))
                awaiting = shown[-1] if note.worded else None
                continue
            if kind == "assistant" and awaiting is not None and text:
                awaiting["text"], awaiting = text, None
                continue
            awaiting = None
            if ready is not None:
                shown.append(ready)
            elif brought is not None:
                # The host's line, never the note or the result it carries.
                shown.extend(brought)
                unanswered = True
            elif kind == "tap" and day_build(msg):
                # The host's line, never shown as the user's words or the recipe's.
                shown.append({"role": "host", "text": DAY_BUILD_SHOWN})
            elif routine is not None:
                shown.append({"role": "host", "text": routine})
            elif kind in ("user", "assistant") and text:
                shown.append({"role": msg.role, "text": text})
            if kind == "user":
                said = shown[-1] if text and shown and shown[-1].get("role") == "user" else None
        summary, rows = await rt.conversations.context(self.id, OWNER)
        return _Loaded(shown=shown, turn=turn, summary=summary, rows=rows, unanswered=unanswered)

    async def _heads_up(self, note: _HeadsUpNote) -> dict[str, Any]:
        """A heads-up for the page's history, with its link and detail from its notice."""
        kept = None
        if self._rt.schedules is not None:
            try:
                kept = await self._rt.schedules.notice(note.id)
            except Exception as e:
                logger.warning("heads-up not read in %s: %s", self.id, type(e).__name__)
        if kept is not None and kept.owner_id != OWNER:
            kept = None
        return {
            "role": "notice",
            "kind": "heads_up",
            "id": note.id,
            "title": note.title,
            "text": None,
            "link": https_link(kept.link) if kept is not None else None,
            "detail": kept.detail if kept is not None else None,
            "quiet": note.quiet,
        }

    def _take(self, loaded: _Loaded) -> None:
        """Make what was read this session's own. No await: nothing comes between."""
        self._turn, self._summary = loaded.turn, loaded.summary
        self._unanswered = loaded.unanswered
        self._ids[:] = [message_id for message_id, _, _ in loaded.rows]
        self._kinds[:] = [kind for _, kind, _ in loaded.rows]
        self._history[:] = [msg for _, _, msg in loaded.rows]

    async def reloaded(self) -> bool:
        """A routine wrote here with nobody's turn running: read the conversation again
        and show the page what is there now, history and board. It delivers nothing:
        notices reach the user only through the door (SUR-9).

        False, and nothing changed, while a turn runs or input is taken, before the page
        has its history, or when anything was saved here while it read: the page then
        catches up when it next opens the conversation."""
        if self._closed or not self._started or self.writing():
            return False
        saved = self._saves
        loaded = await self._read()
        if self._closed or self.writing() or self._saves != saved:
            return False
        self._take(loaded)
        # Her last reply here is history now: nothing of it can be trimmed.
        self._tail = None
        rt = self._rt
        await self._send("history", messages=loaded.shown)
        await self._send(
            "board", board=for_page(await self._board(), rt.zone(), rt.refreshing(self.id))
        )
        # A read that ended while a routine built here is brought in now, if free.
        self._follow_up_if_free()
        return True

    async def catch_up(self) -> None:
        """A routine wrote here: show it now, or, if that read was dropped, once more
        when the page is next free, so an open page is not left behind the run."""
        if await self.reloaded() or self._closed or self._retry is not None:
            return
        self._retry = asyncio.create_task(self._reload_later())

    async def _reload_later(self) -> None:
        try:
            await asyncio.sleep(RELOAD_AGAIN_S)
            waited = RELOAD_AGAIN_S
            while self.writing() and not self._closed and waited < RELOAD_GIVE_UP_S:
                await asyncio.sleep(RELOAD_AGAIN_S)
                waited += RELOAD_AGAIN_S
            await self.reloaded()
        except Exception as e:
            # The page catches up when it next opens the conversation.
            logger.warning("reload not tried again in session %s: %s", self.id, type(e).__name__)
        finally:
            self._retry = None

    def _offers(self, d: ToolDefinition) -> bool:
        """Whether a page's turn here is offered this tool: never a routine's own,
        unless this conversation is where it belongs (`tools/routine.py`). In a
        helper's own chat, only the tools that change it; anywhere else, making and
        asking one (AGT-2)."""
        if d.category == HELPER_CATEGORY:
            return d.name in self._helper_tools()
        return d.category != ROUTINE_CATEGORY or d.name in self._routine_tools

    def _helper_tools(self) -> frozenset[str]:
        if self._rt.helpers is None or self._helpers_unread:
            return frozenset()
        if self._helper is not None:
            return frozenset({HELPER_UPDATE, HELPER_NARROW, HELPER_WIDEN})
        if self._offering is not None and self._offering[0] == self._turn:
            # Its host note said to offer one, this turn.
            return frozenset({HELPER_CREATE, HELPER_ASK, HELPER_SUGGEST})
        return frozenset({HELPER_CREATE, HELPER_ASK})

    def offering(self, area: str) -> bool:
        """Whether this turn was told to offer a helper for `area` and has not yet."""
        return (
            self._helper is None
            and not self._helpers_unread
            and self._offering == (self._turn, area)
        )

    def claim_offer(self, area: str) -> bool:
        """`offering`, and from now on not: one helper_suggest takes the turn's offer,
        so two calls in one reply make one offer."""
        if not self.offering(area):
            return False
        self._offering = None
        return True

    def _mcp_area(self, tool: str) -> str | None:
        servers = self._rt.mcp
        server = servers.server_of(tool) if servers is not None else None
        return needs.server_area(server.name, server.catalog) if server is not None else None

    async def _offer_due(self) -> str | None:
        """The area to offer a helper for on this turn, if any. Never in the way of
        the turn: anything unexpected means no offer, logged by its kind only."""
        rt = self._rt
        if rt.helpers is None or self._helper is not None or self._helpers_unread:
            return None
        if self.kind not in needs.COUNTED_KINDS:
            return None
        try:
            now = rt.clock()
            today = day.today(now, rt.zone()).isoformat()
            if self._offer_looked is not None and self._offer_looked[0] == today:
                # Looked at once a day here: a draft and its turn get the same answer.
                return self._offer_looked[1]
            area = await self._offer_now(now)
            self._offer_looked = (today, area)
            return area
        except Exception as e:
            logger.warning(
                "helper offer not looked at in session %s: %s", self.id, type(e).__name__
            )
            return None

    async def _offer_now(self, now: int) -> str | None:
        rt = self._rt
        if rt.helpers is None:
            return None
        raw = await rt.preferences.get(OWNER, HELPER_OFFER)
        at = raw.get("at_ms") if isinstance(raw, dict) else None
        noted = await rt.preferences.get(OWNER, HELPER_NOTED)
        times = [t for t in (at, noted) if isinstance(t, int)]
        last = max(times) if times else None
        if last is not None and now - last < needs.ONCE_MS:
            # Offered, or told to offer, in the last day: the audit need not be read.
            return None
        calls = await rt.spine_store.recent_tools(OWNER, needs.since(now))
        ids = {sid for tool, sid, _ in calls if needs.area_of(tool, self._mcp_area) is not None}
        kinds: dict[str, str] = {}
        for sid in ids:
            found = await rt.conversations.kind_of(sid, OWNER)
            if found is not None:
                kinds[sid] = found[0]
        areas = needs.repeated(calls, kinds, rt.zone() or "", now, self._mcp_area)
        if not areas:
            return None
        said_no = await rt.preferences.get(OWNER, HELPER_DECLINED)
        declined = [a for a in said_no if isinstance(a, str)] if isinstance(said_no, list) else []
        return needs.to_offer(areas, await rt.helpers.all(OWNER), declined, last, now)

    async def _noted(self, area: str) -> None:
        """The host note rode with a saved message: not again for a day, here or in any
        other conversation, whether or not she offers."""
        rt = self._rt
        self._offering = (self._turn, area)
        if self._offer_looked is not None:
            self._offer_looked = (self._offer_looked[0], None)
        try:
            await rt.preferences.put(OWNER, HELPER_NOTED, rt.clock(), rt.clock())
        except Exception as e:
            logger.warning("helper offer not marked in session %s: %s", self.id, type(e).__name__)

    async def _offered(self) -> None:
        """helper_suggest kept its offer: the page shows it, with Create and No thanks."""
        self._offering = None
        try:
            raw = await self._rt.preferences.get(OWNER, HELPER_OFFER)
        except Exception as e:
            logger.warning("helper offer not shown in session %s: %s", self.id, type(e).__name__)
            return
        if isinstance(raw, dict) and raw.get("name") and raw.get("job"):
            await self._send("helper.offer", area=raw["area"], name=raw["name"], job=raw["job"])

    async def _on_helper_offer(self, area: object, choice: object) -> None:
        """The page's Create or No thanks, taken only for the offer still open. Create
        is the user's own tap, so it makes the helper as a card's approval would."""
        if not isinstance(area, str) or choice not in ("create", "decline"):
            await self._error("bad_message")
            return
        try:
            failed = await self._answer_offer(area, choice)
        except Exception as e:
            # A store that fails never closes the page's socket: it is told, and may try again.
            logger.warning("helper offer not answered in session %s: %s", self.id, type(e).__name__)
            failed = "error"
        if failed is not None:
            # With its area, so the page settles that offer's line and no other.
            await self._send("helper.offer.failed", area=area, reason=failed)

    async def _answer_offer(self, area: str, choice: str) -> str | None:
        """Take the answer: why it failed, if it did in a way the page should show."""
        rt = self._rt
        store = rt.helpers
        if store is None:
            return None
        async with rt.helper_offers:
            raw = await rt.preferences.get(OWNER, HELPER_OFFER)
            at = raw.get("at_ms") if isinstance(raw, dict) else None
            if not (
                isinstance(raw, dict)
                and raw.get("area") == area
                and isinstance(raw.get("name"), str)
                and isinstance(raw.get("job"), str)
                and isinstance(at, int)
            ):
                # Answered already, never made, or for another area: the page is told,
                # so its line never waits for an answer that will not come.
                logger.info("helper offer answer not taken in session %s", self.id)
                return "stale"
            now = rt.clock()
            if now - at > needs.ONCE_MS:
                return "stale"
            # Answered either way: kept only for the once-a-day rule.
            await rt.preferences.put(OWNER, HELPER_OFFER, {"area": area, "at_ms": at}, now)
            if choice == "decline":
                said_no = await rt.preferences.get(OWNER, HELPER_DECLINED)
                declined = list(said_no) if isinstance(said_no, list) else []
                if area not in declined:
                    await rt.preferences.put(OWNER, HELPER_DECLINED, [*declined, area], now)
                return None
            name, job = " ".join(raw["name"].split()), " ".join(raw["job"].split())
            if plain_helper(name, job) is not None:
                return "invalid"
            made = await make_helper(store, rt.conversations, OWNER, name, job, now)
        if isinstance(made, str):
            return made
        logger.info("helper %s made from an offer in %s", made.id, self.id)
        # With its area: the page settles that offer's line on it, and no other.
        await self._send(
            "helper.created", helper_id=made.id, conversation_id=made.conversation_id, area=area
        )
        return None

    async def _read_place(self) -> None:
        """What this conversation is now: which routine tools it is offered (a routine
        set up, or today's day turned over, since the last turn changes it), and whose
        helper's chat it is, or which helpers she can hand work to."""
        await self._read_helpers()
        rt = self._rt
        try:
            routines = await rt.schedules.active(OWNER) if rt.schedules is not None else []
        except Exception as e:
            # Offered nothing: a routine is changed only where it surely belongs.
            logger.warning("routines not read in session %s: %s", self.id, type(e).__name__)
            routines = []
        today = self.day == day.today(rt.clock(), rt.zone()).isoformat()
        self._routine_tools = routine_tools(self.kind, self.id, routines, today=today)
        found = routine_here(self.kind, self.id, routines, today=today)
        self._routine = routine_named(found) if found is not None else ""

    async def _read_helpers(self) -> None:
        store = self._rt.helpers
        if store is None:
            self._helper, self._helpers = None, []
            return
        try:
            self._helper = await store.by_conversation(OWNER, self.id)
            mine = await store.all(OWNER) if self._helper is None else []
        except Exception as e:
            # Offered none: a chat is never taken for hers, or a helper's, by mistake.
            # Her calls keep the helper last read, so a helper's chat stays its own.
            logger.warning("helpers not read in session %s: %s", self.id, type(e).__name__)
            self._helpers_unread, self._helpers = True, []
            return
        self._helpers_unread = False
        self._helpers = [(h.id, h.name, h.job) for h in mine if h.paused_ms is None]
        if self._helper is None and self.kind == "helper":
            # Its helper was deleted: the chat stays, as a plain one (the store's kind too).
            self.kind = "chat"

    async def _helper_made(self, r: RouterResponse) -> None:
        """A confirmed create card: the page opens the new helper's chat."""
        if r.tool != HELPER_CREATE or r.status != "succeeded":
            return
        try:
            made = json.loads(r.content)
            helper_id, conversation_id = made["helper_id"], made["conversation_id"]
        except (ValueError, TypeError, KeyError):
            return
        await self._send("helper.created", helper_id=helper_id, conversation_id=conversation_id)

    def _model(self) -> dict[str, str] | None:
        p = self._rt.provider
        return {"provider": p.name, "model": p.model} if p is not None else None

    def _speech(self) -> dict[str, Any] | None:
        rt = self._rt
        s = rt.speech_used
        return describe(rt.tts, s.kind if s else None, s.voice if s else None)

    def _listening(self) -> dict[str, Any] | None:
        rt = self._rt
        s = rt.listening_used
        return listening.describe(rt.stt, s.kind if s else None, s.choice if s else None)

    async def settings_changed(self) -> None:
        """Settings changed. The next turn uses them; one in flight finishes as it was."""
        if self._overlap is not None and self._overlap.watch:
            # The hearing it watches with may be gone: the page opens it again if it can.
            await self._end_overlap(tell=True)
        await self._send("model", model=self._model())
        await self._send("speech", speech=self._speech())
        await self._send("listening", listening=self._listening())

    async def identity_changed(self) -> None:
        """The user renamed her or themselves. The page follows; the next turn uses it."""
        await self._send("identity", agent_name=self._rt.identity.agent_name)

    async def renamed(self, title: str) -> None:
        """The user named this conversation: hers will never replace it. A naming call
        in flight is left to finish (its cost is recorded); the store refuses its title."""
        self._title_source = "user"
        await self._broadcast_title(title)

    async def deleted(self) -> None:
        """The conversation was deleted elsewhere. Say so, then stop for good; the
        page starts a new one."""
        # Nothing is kept for it: its reads were cancelled with it.
        self._finished = []
        await self._send("error", code="deleted")
        await self.close()

    async def replaced(self) -> None:
        """This conversation was opened somewhere else. Say so, then stop for good."""
        self._replacing = True
        await self._send("error", code="replaced")
        await self.close()

    def busy(self) -> bool:
        """Her turn, or her saying something, is running here: a refresh waits."""
        return self._task is not None and not self._task.done()

    def writing(self) -> bool:
        """Busy, or taking in something the user did that may start a turn: a routine
        waits rather than write between this page's messages."""
        return self.busy() or self._taking > 0

    async def _routine_building(self) -> bool:
        """A routine is building in this conversation: it is the one writer here until
        done, so what would start a turn is refused, and the page told why."""
        if self.id not in self._rt.routine_holds:
            return False
        await self._error(ROUTINE_BUILDING)
        return True

    async def close(self) -> None:
        self._rt.sessions.discard(self)
        if self._rt.by_conversation.get(self.id) is self:
            del self._rt.by_conversation[self.id]
            # No page is on this conversation any more: nothing is kept for it.
            if self._rt.refresher is not None:
                self._rt.refresher.forget(self.id)
        await self._end_overlap(tell=False)
        await self._stop_listening()
        self._closed = True
        for timer in (self._grace, self._again):
            if timer is not None:
                timer.cancel()
        self._grace = self._again = None
        retry, self._retry = self._retry, None
        if retry is not None and retry is not asyncio.current_task():
            retry.cancel()
        self._tail = None
        # A summary being written is let go. A provider reports usage only at the end of
        # a call, so one stopped here counts as nothing. The conversation is summarized
        # again when it is next open and long.
        compacting, self._compacting = self._compacting, None
        self._written = None
        if compacting is not None:
            compacting.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await compacting
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        finished, self._finished = self._finished, []
        if finished:
            # Never lost: saved here, and brought in when the conversation is next open,
            # with a ready notice unless the page that replaces this one brings it in.
            rt = self._rt
            notify = not self._replacing
            if self.id in rt.routine_holds:
                # A routine is the one writer there: the notes wait for it, not the close.
                rt.jobs.track(self.id, _keep_after_holds(rt, self.id, finished, notify=notify))
            else:
                try:
                    await keep_for_later(rt, self.id, finished, notify=notify)
                except Exception as e:
                    # The close goes on: what it lets go matters more than the note.
                    logger.warning(
                        "background note not kept in session %s: %s", self.id, type(e).__name__
                    )
        # The turn is over by now, so nothing is replaying either of these. Belt and
        # braces: every path that ends a turn already lets its draft go.
        await self._drop_draft()
        using, self._using = self._using, None
        if using is not None:
            await self._let_go(using)
        naming_task = self._naming
        if naming_task is not None and not naming_task.done():
            naming_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await naming_task

    # ── Incoming messages ────────────────────────────────────────────────

    @_calls_as("conversation")
    async def handle(self, msg: Mapping[str, Any]) -> None:
        if self._closed:
            return
        kind = msg.get("type")
        if kind == "turn.text":
            # A tap on a widget's ask says so; anything else in `from` is plain typing.
            await self._on_text(msg.get("text"), ask=msg.get("from") == "ask")
        elif kind == "interrupt":
            # Before anything that awaits: a notice that arrives meanwhile is not stopped,
            # and is not spoken either: they asked for quiet.
            task = self._task
            self._stopped = True
            self._listen_after = False
            self._stopped_her()
            # Stopped: the page drops what it was playing and never reports it played.
            self._spoken = None
            # Whatever they were saying over her goes too; the page stops everything.
            await self._end_overlap(tell=False)
            if self._listener is not None:
                # Stopping while she listens throws the words away: nothing is sent.
                await self._stop_listening()
                await self._send("mic.stop", reason="cancelled")
                await self._state(self._rest)
            # Never the one a turn replays: that stops with its turn, below.
            await self._drop_draft()
            if task is not None:
                # Its end comes to rest, which offers what is held and ends the quiet.
                task.cancel()
            else:
                # Her reply may still be playing: it keeps only what was heard.
                await self.trim_tail()
                # Nothing to stop, but what was held for the speech just dropped may come.
                await self._offer()
                self._stopped = False
        elif kind == "listen.start":
            held = msg.get("held") is True
            ov = self._overlap
            if ov is not None and ov.watching and self._task is None:
                # Speak, while her finished reply plays: the watch becomes the turn,
                # from what is said next.
                self._let_go_heard(ov)
                await self._leave_watch(ov)
                await self._overlap_interrupts(ov)
            elif self._window is not None and self._listener is self._window:
                # They take the floor in the window left open for them: it is theirs,
                # and no nudge follows it.
                self._window = None
                await self._send("mic.start", follow_up=False)
            else:
                await self._on_listen(NOTHING_AFTER_S, held=held)
            if held and self._listener is not None:
                self._listener.key_held = True
        elif kind == "barge":
            ov = self._overlap
            if ov is not None:
                # Already listening over her: that becomes the turn.
                await self._leave_watch(ov)
                await self._overlap_interrupts(ov)
            else:
                await self._on_barge()
        elif kind == "overlap":
            ov = self._overlap
            if ov is not None and ov.watching:
                # The page ducked her itself: decide now.
                await self._start_deciding(ov)
            else:
                await self._on_overlap()
        elif kind == "overlap.watch":
            await self._on_watch()
        elif kind == "overlap.unwatch":
            # The page stopped watching (a switch turned off, a microphone gone).
            if self._overlap is not None and self._overlap.watching:
                await self._end_overlap(tell=True)
        elif kind == "listen.stop":
            # The user says they are done: what was heard so far is the message.
            if self._listener is not None:
                await self._conclude("end")
        elif kind in ("confirm", "cancel"):
            await self._on_tap(msg.get("pending_id"), kind)
        elif kind == "spend.choice":
            await self._on_spend_choice(msg)
        elif kind == "greet":
            await self._on_greet()
        elif kind == "day.build":
            await self._on_day_build(spoken=msg.get("spoken") is True)
        elif kind == "helper.hello":
            await self._on_helper_hello()
        elif kind == "speech.mute":
            self._muted = msg.get("muted") is True
        elif kind == "mic.opened":
            # What the page's audio setup reports: numbers and a state, nothing heard.
            if msg.get("silent") is True:
                logger.warning("the page's microphone gave exact silence: %s", dict(msg))
            self._mic_setup = {
                k: msg.get(k)
                for k in ("rate", "device_rate", "state", "channels", "device", "track", "silent")
                if isinstance(msg.get(k), int | float | str | bool)
            }
        elif kind == "playback.sentence":
            self._sentence_played(msg.get("turn_id"), msg.get("seq"))
            self._her_heard()
        elif kind == "playback.started":
            await self._audible(msg.get("turn_id"), msg.get("in_ms"))
            self._her_heard()
        elif kind == "playback.ended":
            turn_id = msg.get("turn_id")
            if isinstance(turn_id, int) and not isinstance(turn_id, bool):
                self._played = turn_id
            if self._tail is not None and self._tail[0].turn == turn_id:
                # Heard to the end: there is nothing left to cut.
                self._tail = None
            ov = self._overlap
            if ov is not None and ov.watching:
                # Heard to the end while only watching: the watch is the follow-up.
                ov.ended = True
                await self._watch_settles()
            elif ov is not None:
                # They are talking over her end: what they meant decides what follows.
                ov.ended = True
            elif turn_id is not None and turn_id == self._quiet_turn:
                # A silence she chose has nothing to be heard: what follows it is the
                # session's, never a follow-up window (VOI-7).
                pass
            # She has finished being heard, which is what reopens the microphone (VOI-8,
            # VOI-15b): only after a spoken turn, and never over a turn still running.
            elif self._answered_aloud() and self._listener is None:
                if self._task is None:
                    await self._on_listen(FOLLOW_UP_S, follow_up=True)
                elif turn_id is not None and turn_id == self._playing:
                    # Her turn is still finishing: it opens once the turn is over. An
                    # earlier turn's end, heard while a later one runs, opens nothing.
                    self._ended_early = True
            await self._offer()
        elif kind in ("board.open", "board.close", "board.remove"):
            await self._on_board(kind, msg.get("id"))
        elif kind == "board.seen":
            # Whether this page shows the board: live widgets refresh only then.
            on = msg.get("on")
            if not isinstance(on, bool):
                await self._error("bad_message")
            elif self._rt.refresher is not None:
                # `opened`: the board itself was opened, not the tab shown again.
                self._rt.refresher.seen(self.id, on, opened=on and msg.get("opened") is True)
        elif kind == "board.refresh":
            widget_id = msg.get("id")
            if not (isinstance(widget_id, str) and widget_id):
                await self._error("bad_message")
            elif self._rt.refresher is not None:
                self._rt.refresher.now(self.id, widget_id)
        elif kind == "helper.offer":
            await self._on_helper_offer(msg.get("area"), msg.get("choice"))
        elif kind == "confirm.shown":
            pid = msg.get("pending_id")
            if isinstance(pid, str) and pid:
                await self._rt.router.mark_delivered(pid, OWNER, SURFACE)
        else:
            await self._error("unknown_type")

    async def _board(self) -> Board | None:
        """The saved board, or None. A board that cannot be read never stops a turn."""
        boards = self._rt.boards
        if boards is None:
            return None
        try:
            return await boards.current(self.id)
        except Exception as e:
            logger.warning("board not read: %s", type(e).__name__)
            return None

    async def _on_board(self, kind: str, widget_id: object) -> None:
        """The user opened or closed the board, or took a widget off. It changes the
        saved board, so her next turn sees what they see (D7). When nothing changed,
        this page is sent the board as it really is, so a page that drew the change
        ahead of time does not keep it."""
        boards = self._rt.boards
        if boards is None:
            return
        if kind == "board.remove" and not (isinstance(widget_id, str) and widget_id):
            await self._error("bad_message")
            return
        # A draft carries the board as it was in its context: it can no longer be used.
        await self._drop_draft()
        try:
            if kind == "board.open":
                changed = await boards.user_open(self.id)
            elif kind == "board.close":
                changed = await boards.user_close(self.id)
            else:
                changed = await boards.user_remove(self.id, str(widget_id))
        except Exception as e:
            logger.warning("board edit failed: %s", type(e).__name__)
            changed = False
        if not changed:
            rt = self._rt
            board = for_page(await self._board(), rt.zone(), rt.refreshing(self.id))
            await self._send("board", board=board)

    async def _on_text(self, text: object, *, ask: bool = False) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            await self._error("bad_message")
        elif ask and text.lstrip().startswith("["):
            # A "[host]" line is the app speaking; an ask never is (the kit refuses one).
            await self._error("bad_message")
        elif self._rt.provider is None:
            await self._error("no_model")
        else:
            async with self._taking_input():
                await self._take_text(text, ask=ask)

    async def _take_text(self, text: str, *, ask: bool = False) -> None:
        if self._task is not None:
            await self._error("busy")
        elif await self._routine_building():
            return
        else:
            self._spoken = None
            await self._stop_listening()
            # An ask is typed words that can never approve (widget design D5).
            self._via = "ask" if ask else "text"
            self._turn += 1
            if ask:
                # Her words, not the user's, and maybe drawn from a page she read: what she
                # does on them counts as read (MEM-5), so a memory is kept as a claim and a
                # reminder or timed ask is refused, as after any outside read.
                self._outside.add(self._turn)
            self.active_ms = self._rt.clock()
            self._arrived = self._starting("text")
            declined = None if ask else await self._said_no(text, "text", self._turn)
            if declined is not None:
                await self._show_declined(declined)
            await self._save_input("user", text, marker=_no_note(declined))
            self._begin()

    async def _on_greet(self) -> None:
        """The introduction is over: she says hello in her own words, once (ONB-3).

        The page only asks. What she is told is authored here and carries nothing
        the page sent, and it is refused anywhere but the start of a conversation
        with someone who has introduced themselves."""
        rt = self._rt
        if rt.provider is None or rt.profile is None:
            return
        async with self._taking_input():
            if self._task is not None or self._history or self._greeted:
                return
            if await self._routine_building():
                return
            self._greeted = True
            await self._stop_listening()
            self._via = "text"
            self._arrived = self._starting("greet")
            self._queued.append(greeting(rt.identity))
            self._begin()

    async def _on_helper_hello(self) -> None:
        """A helper's chat opened: the first time ever, it says hello in its own words
        and may offer a routine on a card. The page only asks; the note is the app's.
        Marked before the turn runs, so a hello that fails is never tried again and
        its fixed first line stands."""
        rt = self._rt
        store = rt.helpers
        if store is None or rt.provider is None or self.kind != "helper":
            return
        async with self._taking_input():
            if await self._routine_building():
                return
            await self._read_place()
            h = self._helper
            # Paused: not spent, so it says hello once resumed. A turn running: not
            # spent either, so it is never used up without one.
            if h is None or h.paused_ms is not None or self._task is not None:
                return
            # Anything beyond its fixed first line: it is already talking, so the hello
            # is spent with no turn.
            fresh = self._kinds in ([], ["assistant"])
            try:
                first = await store.said_hello(OWNER, h.id, rt.clock())
            except Exception as e:
                logger.warning(
                    "helper hello not marked in session %s: %s", self.id, type(e).__name__
                )
                return
            if not first or not fresh:
                return
            note = helper_hello(h, rt.connected_catalog())
            if self._task is not None:
                # Never taken over a turn: it is that turn's loop that says it next.
                self._queued.append(note)
                return
            await self._stop_listening()
            self._via = "text"
            self._arrived = self._starting("greet")
            self._queued.append(note)
            self._begin()

    async def _on_day_build(self, *, spoken: bool = False) -> None:
        """Build the day board (SUR-1): she reads what is connected and shows it.

        Like the greeting, the page only asks: the recipe is composed here from what
        is set up, never from anything the page sent (ONB-3). It is taken only in
        today's day conversation, never over a running turn, and at most once a minute. It is
        saved as the host's input, so it advances no turn and can approve nothing."""
        rt = self._rt
        if self.kind != "day":
            return
        if rt.provider is None:
            await self._error("no_model")
            return
        async with self._taking_input():
            now = rt.clock()
            zone = rt.zone()
            date = day.today(now, zone)
            last = rt.day_built.get(self.id)
            if (
                self._task is not None
                # A past day stays as it was left: its board is that day's.
                or self.day != date.isoformat()
                or (last is not None and now - last < DAY_BUILD_EVERY_MS)
            ):
                return
            if await self._routine_building():
                return
            await self._stop_listening()
            # Never an approval: the recipe is the app's words, not the user's.
            self._via = "ask"
            self.active_ms = now
            self._arrived = self._starting("tap")
            text = day.recipe(
                rt.day_setup(), date, zone or "", rt.identity, layout=await rt.home_layout()
            )
            built = await self._build_input("tap", text, marker=DAY_BUILD)
            await self._commit_input("tap", built)
            # Only once it is saved: a build that failed to save can be asked for again.
            rt.day_built[self.id] = now
            await self._send("host", text=DAY_BUILD_SHOWN)
            self._building_day = True
            self._listen_after = spoken
            self._begin()

    # ── Listening ────────────────────────────────────────────────────────

    async def _on_barge(self) -> None:
        """The user talked over her (VOI-9): her reply stops, what she had said is kept
        and marked interrupted, and she listens. The page heard them start and holds
        their first words until listening opens, so nothing they said is lost.

        Stopping her is not cancelling work (VOI-15c): an action already approved
        finishes and is recorded before she listens."""
        self._spoken = None
        self._stopped_her()
        async with self._taking_input():
            task = self._task
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError, Exception):
                    await asyncio.wait_for(asyncio.shield(task), BARGE_WAIT_S)
            else:
                # Her turn is over but its sound may not be: keep only what was heard.
                await self.trim_tail()
            await self._on_listen(NOTHING_AFTER_S)

    # ── Talking over her (VOI-9) ─────────────────────────────────────────

    async def _on_overlap(self) -> None:
        """The page ducked her voice because the user started talking over it (VOI-9).

        Her reply keeps going, quieter, while about a second of what they say is heard;
        then they either let her carry on or become the turn. That needs a recognizer
        that gives words while they talk and a reply to talk over: a turn running, or
        her finished reply still playing. Otherwise it is a barge, as before (D7)."""
        rt = self._rt
        if self._overlap is not None:
            return
        task = self._task
        # Notices are not a reply: talking over one stops it, as before.
        running = task is not None and not task.done() and task is not self._notice_task
        if (
            rt.stt is None
            or rt.listening_used is None
            or not rt.stt.turns
            or rt.provider is None
            or self._listener is not None
            or not (running or self._tail is not None)
            # Hearing at its cap is not tried while she talks (BRN-8).
            or self._hearing_held()
        ):
            await self._barge_instead()
            return
        tail = self._tail[0] if self._tail is not None else None
        # Nothing heard is shown until it turns out to be the user's turn, and no
        # draft starts from it: her reply must stay as it is until then.
        listener = Listener(
            rt.stt, rt.listening_used.choice, on_partial=self._unshown, speakers=rt.speakers
        )
        ov = Overlap(listener=listener, started=self._now(), speaker=self._speaker or tail)
        self._overlap = ov
        try:
            await listener.start()
        except VoiceError as e:
            # They cannot be heard over her: she stops, as a barge would stop her, and
            # the listening that follows says what is wrong.
            logger.warning("listening over her failed in session %s: %s", self.id, e.kind)
            if self._overlap is ov:
                self._overlap = None
                await listener.abandon()
                await self._barge_instead()
            return
        if self._overlap is ov:
            ov.watcher = asyncio.create_task(self._watch_overlap(ov))
        elif not ov.promoting:
            # Stopped or closed while it connected: nobody will close it otherwise.
            await listener.abandon()

    async def _barge_instead(self) -> None:
        """An overlap that cannot be decided is a barge (D7). The page only ducked her,
        so her sound is stopped here unless stopping her turn does it."""
        task = self._task
        if task is None or task.done():
            await self._stop_her_sound(self._tail[0] if self._tail is not None else None)
        # Frames the page sends meanwhile wait in the socket until this returns, by
        # when the listener exists.
        await self._on_barge()

    async def _stop_her_sound(self, speaker: Speaker | None) -> None:
        """Tell the page to stop whatever of her is still playing."""
        if speaker is not None:
            turn: int | None = speaker.turn
        elif self._spoken is not None:
            turn = self._spoken[0]
        else:
            turn = self._played
        await self._send("audio.stop", turn=turn)

    async def _unshown(self, text: str) -> None:
        return None

    async def _overlap_audio(self, ov: Overlap, pcm: bytes) -> None:
        """A frame heard over her. Every frame goes to the listener deciding, and to it
        still while it becomes the turn, so none of their words is lost."""
        self._last_audio = self._now()
        try:
            verdict = await ov.listener.audio(pcm)
        except VoiceError as e:
            if not ov.promoting:
                # Hearing failed: she carries on, as if they had not spoken.
                logger.warning("listening over her failed in session %s: %s", self.id, e.kind)
                if ov.watch:
                    # Nothing more can be heard with it: the watch is over.
                    await self._end_overlap(tell=True)
                    await self._offer()
                else:
                    await self._overlap_carries_on(ov)
            # Becoming the turn: the listener raises it again on the next frame.
            return
        ov.wake.set()
        if verdict is not None and verdict != "end" and not ov.promoting:
            # Nothing said, a dead microphone, or far too long: she carries on.
            await self._overlap_carries_on(ov)

    async def _watch_overlap(self, ov: Overlap) -> None:
        """Weigh what has been heard over her, on every frame and every tick."""
        while self._overlap is ov and not ov.promoting:
            if ov.watching:
                if not await self._watch_once(ov):
                    return
            else:
                listener = ov.listener
                speaker = self._speaker or ov.speaker
                done = listener.verdict == "end"
                # The audio's own clock as well: the page sends the words it held while
                # deciding to duck all at once, and they were said before now. That
                # pre-roll counts too, so the limits land about 0.3 s early.
                elapsed = max(self._now() - ov.started, listener.heard_s)
                if ov.watch:
                    # Ducked on words of theirs: only theirs are heard from here, so
                    # none of them is hers to leave out.
                    tokens = listener.text().split()
                    decision = confirm(
                        " ".join(tokens[: ov.ducked_on]),
                        " ".join(tokens[ov.ducked_on :]),
                        # Ended by the recognizer, even on a word that leads on.
                        done=done or listener.turn_done,
                        elapsed_s=elapsed,
                        quiet_s=self._quiet_s(ov),
                    )
                else:
                    decision = decide(
                        listener.text(),
                        done=done,
                        elapsed_s=elapsed,
                        playing=speaker.playing() if speaker is not None else (),
                    )
                if decision == "carry_on":
                    await self._overlap_carries_on(ov)
                    # Watching again, unless that ended it.
                    continue
                if decision == "interrupt":
                    await self._overlap_interrupts(ov)
                    return
            ov.wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(ov.wake.wait(), OVERLAP_TICK_S)

    async def _watch_once(self, ov: Overlap) -> bool:
        """While she speaks: are any words heard theirs? Each is judged by when it was
        said (#102). Once she has ended, the watch becomes the window. False once the
        watch is over."""
        now = self._now()
        if now - self._last_audio > NO_AUDIO_S or now - ov.her_at > WATCH_HOLD_S:
            # The page stopped sending, or nothing of hers has played for long.
            logger.info("talked over in session %s: watch_over", self.id)
            await self._end_overlap(tell=True)
            await self._offer()
            return False
        if self._collecting:
            # Their last words are still being saved: judged once they are.
            return True
        if self._after_her() and self._her_over(ov):
            # Her echo is over on the recognizer's clock: the window opens, keeping
            # only their words.
            await self._follow_up_from(ov)
            return False
        theirs = self._fresh(ov)
        if any(not is_filler(w) for word in theirs for w in words_of(word.text)):
            if self._after_her() and ov.ended:
                # She was heard to the end: their words are the next turn.
                await self._follow_up_from(ov)
                return False
            await self._start_deciding(ov)
        return True

    def _answered_aloud(self) -> bool:
        """Her last turn answered something said out loud, so the microphone reopens
        after it: a spoken turn, or a day build asked for by voice."""
        return self._via == "voice" or self._listen_after

    @property
    def spoken_turn(self) -> bool:
        """The running turn answers something said out loud."""
        return self._via == "voice"

    def _after_her(self) -> bool:
        """Her spoken turn is over: once she has been heard, the watch is the next window."""
        return self._task is None and self._answered_aloud() and self._listener is None

    def _her_over(self, ov: Overlap) -> bool:
        """Her echo is over by the listener's clock: her last sentence, played, has
        ended. With nothing of hers placed, the page's word that she was heard to the
        end is all there is."""
        timeline = ov.timeline
        if timeline is None or not timeline.sentences:
            return ov.ended
        now = ov.listener.sent_s
        end = timeline.ended_final(now_s=now)
        return end is not None and now >= end

    def _quiet_s(self, ov: Overlap) -> float:
        """How long since the last word of anyone's was heard over her: words still
        coming, theirs or taken for hers, mean they are still talking."""
        count = len(ov.listener.heard_words())
        now = self._now()
        if count != ov.words_seen:
            ov.words_seen, ov.word_at = count, now
        return now - ov.word_at

    def _fresh(self, ov: Overlap) -> list[Word]:
        """Their words among those not dealt with yet, in order."""
        words = [w for w in ov.listener.heard_words() if w.start_s >= ov.past_s]
        theirs = self._theirs(ov.listener, ov.timeline, words)
        return [w for w, t in zip(words, theirs, strict=True) if t]

    def _theirs(
        self, listener: Listener, timeline: Timeline | None, words: Sequence[Word]
    ) -> list[bool]:
        """Whether each word is theirs rather than her echo, by when it was said, what
        she was saying then and how loud the microphone was (#102). Her words that
        match what she was saying teach how much of her comes back."""
        if timeline is None:
            return [True] * len(words)
        # Her playback, with its margins, is not the room.
        busy = [(a - LEAD_S, b + TAIL_S) for a, b in timeline.spans()]
        room = listener.room_level(busy=busy)
        # How far from where she was placed her words are heard: measured, for the logs.
        fresh = [w for w in words if (w.text, round(w.start_s, 1)) not in self._aligned]
        for w in fresh:
            self._aligned.add((w.text, round(w.start_s, 1)))
            timeline.align(w)
        out: list[bool] = []
        for w in words:
            self._learn(listener, timeline, w)
            who = whose(
                w,
                timeline,
                listener.word_level,
                share=self._echo.value,
                room=room,
                learned=self._echo.learned,
            )
            out.append(who == "theirs")
        return out

    def _learn(self, listener: Listener, timeline: Timeline, word: Word) -> None:
        """Learn her echo's share from a word of hers, once per word."""
        key = (word.text, round(word.start_s, 1))
        if key in self._learned or not matched(word, timeline):
            return
        self._learned.add(key)
        self._echo.learn(
            mic_level=listener.word_level(word.start_s, word.end_s),
            her_level=timeline.her_level(word.start_s, word.end_s),
        )

    def _judge_on(
        self, listener: Listener, timeline: Timeline | None, past_s: float = 0.0
    ) -> Judge:
        """For a listener that heard her: keep only their words, from `past_s` on."""

        def keep(words: Sequence[Word]) -> list[bool]:
            theirs = self._theirs(listener, timeline, words)
            return [t and w.start_s >= past_s for w, t in zip(words, theirs, strict=True)]

        return keep

    def _let_go_heard(self, ov: Overlap) -> None:
        """Every word heard so far is dealt with: only what is said after counts."""
        ov.past_s = max([ov.past_s, *(w.end_s for w in ov.listener.heard_words())])

    def _stopped_her(self) -> None:
        """She is being stopped: sentences of hers not reported played were never heard,
        but the one under way now was."""
        if self._timeline is not None:
            self._timeline.stop(at_s=self._clock_now())

    def _clock_now(self) -> float | None:
        """The recognizer's clock now, if a listener that watched her is still open."""
        clock = self._clock
        return clock.sent_s if clock is not None and clock.open else None

    def _new_timeline(self) -> Timeline:
        last = self._timeline
        if last is not None and (offset := last.offset_s()) is not None:
            # Numbers only: how far her reports were off the recognizer's clock.
            logger.debug(
                "her playback in session %s was heard %+.2fs from its reports (%d words)",
                self.id,
                offset,
                len(last.offsets),
            )
        timeline = Timeline()
        self._timeline, self._timeline_turn = timeline, None
        self._learned.clear()
        self._aligned.clear()
        self._reported = None
        return timeline

    def _reply_timeline(self) -> Timeline:
        """The timeline for a reply about to be voiced: the one a watch opened for it,
        or a new one, placed on an open watch's clock."""
        ov = self._overlap
        timeline = self._timeline
        if (
            timeline is not None
            and self._timeline_turn is None
            and ov is not None
            and ov.timeline is timeline
        ):
            return timeline
        timeline = self._new_timeline()
        if ov is not None and ov.watch:
            timeline.begin(at_s=ov.listener.sent_s)
            ov.timeline = timeline
        else:
            self._clock = None
        return timeline

    async def _start_deciding(self, ov: Overlap) -> None:
        """Words that are not hers: duck her, and decide from now, as for an overlap."""
        if not ov.watching or self._overlap is not ov:
            return
        await self._leave_watch(ov)
        ov.ducked_on = len(ov.listener.text().split())
        ov.started = self._now()
        # Words still coming are counted from the duck on (`_quiet_s`).
        ov.words_seen, ov.word_at = len(ov.listener.heard_words()), ov.started
        logger.info("talked over in session %s: deciding", self.id)
        await self._send("overlap.duck")
        ov.wake.set()

    async def _leave_watch(
        self, ov: Overlap, *, patience_s: float = NOTHING_AFTER_S, words_only: bool = False
    ) -> None:
        """Stop only watching: what follows is heard as the user. Only their words count:
        her echo, before, between or after them, is left out."""
        if not ov.watching:
            return
        ov.watching = False
        ov.listener.hear(
            patience_s=patience_s,
            judge=self._judge_on(ov.listener, ov.timeline, ov.past_s),
            words_only=words_only,
        )

    def _her_heard(self) -> None:
        """A sentence of hers started playing: a watch goes on."""
        if self._overlap is not None:
            self._overlap.her_at = self._now()

    async def _on_watch(self) -> None:
        """The page streams the microphone while she speaks, so the user can cut in
        (VOI-9). Her voice coming back is told from theirs by when each word was said
        and how loud it was (#102); only words that are theirs duck her and are decided.
        That needs a recognizer that judges turns and times its words, and a reply to
        talk over: one coming, or one still being heard."""
        rt = self._rt
        if self._overlap is not None:
            return
        task = self._task
        running = task is not None and not task.done() and task is not self._notice_task
        unheard = self._spoken is not None and self._spoken[0] != self._played
        if (
            rt.stt is None
            or rt.listening_used is None
            or not rt.stt.turns
            or not rt.stt.word_times
            or rt.provider is None
            or self._listener is not None
            # With her voice off there is nothing to talk over, and nothing to tell apart.
            or self._muted
            or rt.tts is None
            # Her echo is told apart by her own audio: a voice the page makes has none.
            or rt.tts.where != "core"
            or rt.speech_used is None
            or not (running or self._tail is not None or unheard)
            # Hearing at its cap is not tried while she talks (BRN-8).
            or self._hearing_held()
        ):
            await self._send("overlap.over", end=True)
            return
        tail = self._tail[0] if self._tail is not None else None
        wake = asyncio.Event()

        async def heard(_: str) -> None:
            wake.set()  # judged at once, not at the next tick

        # Nothing heard is shown, and no draft starts from it.
        listener = Listener(
            rt.stt, rt.listening_used.choice, on_partial=heard, watch=True, speakers=rt.speakers
        )
        now = self._now()
        # Her reply on this listener's clock: the one playing, or, before her voice
        # starts, the one coming.
        timeline = self._timeline
        if timeline is None or (running and self._speaker is None):
            timeline = self._new_timeline()
        # Opened mid-reply: the sentence last reported playing started that long ago.
        reported = self._reported
        playing = (reported[0], now - reported[1]) if reported is not None else None
        timeline.begin(at_s=listener.sent_s, playing=playing)
        self._clock = listener
        ov = Overlap(
            listener=listener,
            started=now,
            speaker=self._speaker or tail,
            wake=wake,
            watch=True,
            watching=True,
            timeline=timeline,
            her_at=now,
        )
        self._overlap = ov
        self._last_audio = now
        logger.info("talked over in session %s: watching", self.id)
        try:
            await listener.start()
        except VoiceError as e:
            # She carries on unwatched; Stop still works.
            logger.warning("watching over her failed in session %s: %s", self.id, e.kind)
            if self._overlap is ov:
                self._overlap = None
                await listener.abandon()
                await self._send("overlap.over", end=True)
            return
        if self._overlap is ov:
            ov.watcher = asyncio.create_task(self._watch_overlap(ov))
        elif not ov.promoting:
            # Stopped or closed while it connected: nobody will close it otherwise.
            await listener.abandon()

    async def _watch_settles(self) -> None:
        """Her turn is over, or she has been heard to the end, while only watching. Heard
        to the end after a spoken turn, the watch becomes the follow-up window (VOI-8);
        with nothing of hers left to hear, it ends; otherwise it waits for her end."""
        ov = self._overlap
        if ov is None or not ov.watching or self._task is not None:
            return
        unheard = self._spoken is not None and self._spoken[0] != self._played
        if ov.ended and self._answered_aloud() and self._listener is None:
            # Her last words may still come back: the watch tells them from the user's
            # until her echo is over, then becomes the window (see `_watch_once`).
            ov.wake.set()
        elif ov.ended or not (unheard or self._tail is not None):
            logger.info("talked over in session %s: watch_over", self.id)
            await self._end_overlap(tell=True)

    async def _follow_up_from(self, ov: Overlap) -> None:
        """She finished being heard: the listener that watched her goes on as the window
        that opens after she speaks, without a new connection. Only their words go on
        as the turn: her echo, however late it is reported, is left out."""
        async with self._taking_input():
            if self._overlap is not ov or ov.promoting:
                return
            ov.promoting = True
            # After a quiet `listening`, this is the window left open for them (VOI-7).
            quiet = self._quiet == "listening"
            self._quiet, self._quiet_watch = None, None
            logger.info("talked over in session %s: follow_up", self.id)
            await self._leave_watch(
                ov, patience_s=QUIET_LISTEN_S if quiet else FOLLOW_UP_S, words_only=quiet
            )
            self._spoken = None
            await self._become_turn(ov, follow_up=True, quiet=quiet)

    async def _overlap_carries_on(self, ov: Overlap) -> None:
        """An acknowledgement, or nothing at all: she carries on. Nothing is saved, and
        the turn counter does not move. A watch goes back to watching."""
        if self._overlap is not ov or ov.promoting:
            return
        logger.info("talked over in session %s: carry_on", self.id)
        if ov.watch:
            await self._send("overlap.over")
            self._let_go_heard(ov)
            ov.listener.watch()
            ov.watching = True
            ov.her_at = max(ov.her_at, self._now())
            if ov.ended:
                await self._watch_settles()
            await self._offer()
            return
        await self._end_overlap(tell=True)
        if ov.ended and self._answered_aloud() and self._task is None and self._listener is None:
            # She finished being heard while they spoke: the window that would have opened.
            await self._on_listen(FOLLOW_UP_S, follow_up=True)
        # Notices held while they were heard may arrive now.
        await self._offer()

    async def _overlap_interrupts(self, ov: Overlap) -> None:
        """A real interruption: she stops as a barge stops her, and the listener that
        was deciding goes on as the user's turn, every word kept (VOI-9).

        Stopping her is not cancelling work (VOI-15c): an action already approved
        finishes and is recorded first."""
        if self._overlap is not ov or ov.promoting:
            return
        ov.promoting = True
        logger.info("talked over in session %s: interrupt", self.id)
        self._spoken = None
        self._stopped_her()
        async with self._taking_input():
            task = self._task
            if task is not None:
                task.cancel()
                # Stop, or the session closing, can cancel this too: what follows checks.
                # This can swallow this watcher's own cancellation; that is safe, as
                # the `self._overlap is not ov` check below then returns.
                with contextlib.suppress(asyncio.CancelledError, TimeoutError, Exception):
                    await asyncio.wait_for(asyncio.shield(task), BARGE_WAIT_S)
            else:
                # Her turn is over but its sound may not be: keep only what was heard.
                speaker = self._tail[0] if self._tail is not None else ov.speaker
                if await self.trim_tail() is None:
                    # Nothing to trim, so no `interrupted` stops her: say it plainly.
                    await self._stop_her_sound(speaker)
            if self._overlap is not ov:
                # Stopped or closed meanwhile; that let go of the listener.
                return
            if self._task is not None or self._listener is not None or self._closed:
                # Her work is still finishing: as a barge would, say she is busy.
                self._overlap = None
                ov.watcher = None
                await ov.listener.abandon()
                await self._over(ov)
                await self._error("busy")
                return
            await self._become_turn(ov, follow_up=False)

    async def _become_turn(self, ov: Overlap, *, follow_up: bool, quiet: bool = False) -> None:
        """The listener that heard them over her goes on as the user's turn. With
        `quiet`, it is the window left open after a quiet `listening` (VOI-7)."""
        self._overlap = None
        ov.watcher = None
        listener = ov.listener
        listener.promote(on_partial=self._partial, on_turn=self._turn_signal)
        self._listener = listener
        self._window = listener if quiet else None
        self._drafted = False
        self._last_audio = self._now()
        self._warm()
        # The page's microphone is already open; this tells it the words are a turn.
        await self._send("mic.start", follow_up=follow_up, **({"quiet": True} if quiet else {}))
        # Each send can let a Stop or a close in: only a listener still open goes on.
        if self._listener is not listener:
            return
        await self._state("listening")
        if self._listener is not listener:
            return
        self._watchdog = asyncio.create_task(self._watch(listener))
        if said := listener.text():
            await self._partial(said)
        if listener.verdict is not None and self._listener is listener:
            # They finished while she was stopping.
            await self._conclude(listener.verdict)

    async def _end_overlap(self, *, tell: bool) -> None:
        """Stop deciding (or watching) and let go of what was heard over her. With
        `tell`, the page brings her voice back up, and a watch's microphone closes."""
        ov, self._overlap = self._overlap, None
        if ov is None:
            return
        if ov is self._quiet_watch:
            # The watch that was to be the quiet window ended without opening it.
            self._quiet, self._quiet_watch = None, None
        watcher, ov.watcher = ov.watcher, None
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
        await ov.listener.abandon()
        if tell:
            await self._over(ov)

    async def _over(self, ov: Overlap) -> None:
        """Her voice back up. For a watch, `end`: the page stops sending, as it is over."""
        await self._send("overlap.over", **({"end": True} if ov.watch else {}))

    async def _on_listen(
        self,
        patience_s: float,
        *,
        follow_up: bool = False,
        held: bool = False,
        quiet: bool = False,
    ) -> None:
        async with self._taking_input():
            await self._listen(patience_s, follow_up=follow_up, held=held, quiet=quiet)

    async def _listen(
        self, patience_s: float, *, follow_up: bool, held: bool = False, quiet: bool = False
    ) -> None:
        rt = self._rt
        if self._listener is not None or self._overlap is not None:
            return
        if rt.stt is None or rt.listening_used is None:
            if not follow_up:
                await self._error("no_listening")
        elif rt.provider is None:
            await self._error("no_model")
        elif self._task is not None:
            await self._error("busy")
        elif await self._routine_building():
            # Before the microphone opens: no words heard, no draft paid for, all refused.
            return
        elif follow_up and self._hearing_held():
            # At its cap, the microphone does not reopen by itself (BRN-8): the user can
            # still press it, and hears why.
            return
        else:
            self._spoken = None
            listener = Listener(
                rt.stt,
                rt.listening_used.choice,
                on_partial=self._partial,
                patience_s=patience_s,
                on_turn=self._turn_signal,
                # The quiet window: only words hold it open (VOI-7).
                words_only=quiet,
                speakers=rt.speakers,
            )
            # Set before it starts: what was held while it connected is judged the same way.
            listener.key_held = held
            if follow_up and self._timeline is not None and self._timeline.sentences:
                # No watch placed her on this clock: her last sentence is taken to end
                # as it opens, so her echo still in the air is hers.
                listener.judge_by(self._judge_on(listener, self._timeline.ending_at(0.0)))
            self._listener = listener
            # A window left open after a quiet `listening` (VOI-7): silence nudges once.
            self._window = listener if quiet else None
            self._drafted = False
            self._last_audio = asyncio.get_running_loop().time()
            self._warm()
            await self._state("arming")
            # The page opens the microphone now; what it sends while the recognizer is
            # still connecting is held, so the first word is not lost.
            await self._send("mic.start", follow_up=follow_up, **({"quiet": True} if quiet else {}))
            try:
                await listener.start()
            except VoiceCapped as e:
                await self._listening_capped(e)
                return
            except VoiceError as e:
                await self._listening_failed(e.kind)
                return
            if self._listener is listener:
                self._watchdog = asyncio.create_task(self._watch(listener))
                await self._state("listening")
            elif self._closed:
                # Closed while it connected: nobody will close it otherwise.
                await listener.abandon()

    @_calls_as("conversation")
    async def audio(self, pcm: bytes) -> None:
        """A binary frame from the page: microphone audio, 16 kHz mono PCM16 (VOI-1)."""
        ov = self._overlap
        if ov is not None:
            if not self._closed and len(pcm) <= MAX_AUDIO_FRAME:
                await self._overlap_audio(ov, pcm)
            return
        listener = self._listener
        if self._closed or listener is None or len(pcm) > MAX_AUDIO_FRAME:
            return
        self._last_audio = asyncio.get_running_loop().time()
        try:
            verdict = await listener.audio(pcm)
        except VoiceError as e:
            await self._listening_failed(e.kind)
            return
        if verdict is not None and self._listener is listener:
            await self._conclude(verdict)

    async def _watch(self, listener: Listener) -> None:
        """A microphone that goes silent on the wire never reaches a verdict by itself."""
        loop = asyncio.get_running_loop()
        while self._listener is listener:
            await asyncio.sleep(0.5)
            limit = NO_AUDIO_S if listener.frames else FIRST_AUDIO_S
            if self._listener is listener and loop.time() - self._last_audio > limit:
                self._watchdog = None
                await self._conclude("inaudible")
                return

    async def _partial(self, text: str) -> None:
        await self._send("transcript.partial", text=text)

    async def _stop_listening(self) -> Listener | None:
        await self._end_overlap(tell=True)
        listener, self._listener = self._listener, None
        self._window = None
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()
        if listener is not None:
            await listener.abandon()
        # After the listener: it can no longer start another.
        await self._drop_draft()
        return listener

    async def _listening_failed(self, kind: str) -> None:
        logger.warning("listening failed in session %s: %s", self.id, kind)
        await self._stop_listening()
        await self._send("mic.stop", reason="failed")
        # Typing still works: one leg down never takes the conversation with it (VOI-14).
        await self._hearing_down(kind)
        await self._state(self._rest)

    async def _listening_capped(self, e: VoiceCapped) -> None:
        """Hearing is at its hard cap: the microphone stops, she says why, aloud if her
        voice works, and the card offers what can be done (BRN-8). Typing still works."""
        logger.warning("listening at its cap in session %s", self.id)
        await self._stop_listening()
        await self._send("mic.stop", reason="failed")
        line = self._capped_line("hearing", e.provider_id)
        await self._spend_capped("hearing", e.provider_id, line)
        await self._say_line(line)
        await self._state(self._rest)

    @_calls_as("conversation")
    async def _hearing_down(self, kind: str) -> None:
        """She cannot hear: say so in one sentence, aloud if her voice works (VOI-14)."""
        said = degraded("hearing", kind)
        await self._error("listening", kind=kind, sentence=said)
        await self._say_line(said)

    @_calls_as("conversation")
    async def _say_line(self, said: str) -> None:
        """One sentence of the app's, outside a turn, aloud if her voice works."""
        rt = self._rt
        if rt.tts is None or rt.speech_used is None or self._muted or self._voice_held():
            return
        # Not a spoken turn any more: the end of this sentence must not reopen the
        # microphone that just failed, or one fault would repeat itself forever.
        self._via = "text"
        heard_as = rt.out_of_turn()
        speaker = Speaker(
            rt.tts,
            rt.speech_used.voice,
            heard_as,
            self._send,
            self._send_bytes,
            on_capped=self._voice_capped,
        )
        self._saying = True
        try:
            await speaker.feed(said)
            await speaker.finish()
            self._spoken = (heard_as, rt.clock())
        finally:
            self._saying = False

    async def _conclude(self, verdict: Verdict) -> None:
        """Listening is over. Either there is something to answer, or the reason is said."""
        # Until the answer is the session's task, nothing is delivered in between.
        async with self._taking_input():
            await self._concluded(verdict)

    async def _concluded(self, verdict: Verdict) -> None:
        listener, self._listener = self._listener, None
        # Whether this was the window left open after a quiet `listening`: let go now,
        # whatever ended it, so it can nudge once at most.
        window = listener is not None and listener is self._window
        self._window = None
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()
        if listener is None:
            return
        await self._send("mic.stop", reason=verdict)
        if verdict == "inaudible":
            # Enough to tell a paused page from a muted microphone, and nothing heard.
            logger.warning(
                "listening ended (%s): %d frames, %.1fs of audio, loudest %.4f, page audio %s",
                verdict,
                listener.frames,
                listener.heard_s,
                listener.peak,
                self._mic_setup or "not reported",
            )
        if verdict == "inaudible":
            # Said out loud rather than failing silently (VOI-4).
            await listener.abandon()
            await self._drop_draft()
            await self._hearing_down("inaudible")
            await self._state(self._rest)
        elif verdict == "nothing":
            await listener.abandon()
            await self._drop_draft()
            if window and not self._closed and self._task is None and self._rt.provider:
                # They trailed off and stayed quiet: one short line from her, in her
                # own words, invites them on (VOI-7). Spoken, as their turn was.
                self._queued.append(NUDGE)
                self._begin()
                return
            await self._state(self._rest)
        else:
            await self._state("processing")
            self._collecting = True
            # The turn starts when they stopped talking, not when that was noticed.
            quiet = listener.quiet_s
            trace = self._starting("voice", ago=quiet)
            trace.endpoint_s = quiet
            self._task = asyncio.create_task(self._heard(listener, trace))

    async def _heard(self, listener: Listener, trace: Trace) -> None:
        """Collect the words, then answer them. Runs as the session's one task.

        A draft that heard the same words, against the same history, becomes the turn's
        first call (VOI-12). Any other is let go before the turn starts."""
        answer = False
        only_her = False
        draft: Draft | None = None
        try:
            said = await listener.end()
            text = said.text[:MAX_TEXT].strip()
            # Taken only now: one can still start while the last words arrive.
            draft, self._draft = self._draft, None
            # Heard over her, the listener keeps only their words: none left is her
            # echo alone, and nothing of theirs was missed.
            only_her = not text and bool(listener.raw_text())
            if only_her:
                logger.info("spoken turn in session %s dropped: her echo", self.id)
            if text and await self._routine_building():
                # Heard, but a routine is building here: nothing is saved or answered.
                pass
            elif text:
                trace.mark("heard", self._now())
                self._arrived = trace
                # Said over her too: a "no" while her question still plays is the usual
                # answer. "Stop" is never a refusal, so quieting her cancels nothing.
                declined = await self._said_no(text, "voice", self._turn + 1)
                # A draft was built while the card still waited: it never saw the no.
                usable = declined is None and draft is not None and self._usable(draft, text)
                # A used draft saves its own words: the screen shows those, so a reload
                # shows the same.
                shown = draft.words if usable and draft is not None else text
                await self._send("transcript.final", text=shown)
                if declined is not None:
                    # Their words show first, then the card goes.
                    await self._show_declined(declined)
                self._via = "voice"
                self._turn += 1
                self.active_ms = self._rt.clock()
                if usable and draft is not None:
                    # The message it was given is the one saved, so the turn's history
                    # is exactly what the draft sent.
                    await self._commit_input("user", draft.built)
                    self._using, draft = draft, None
                    trace.draft = "used"
                else:
                    if draft is not None:
                        # Let go before the turn's own call, so they never run together.
                        await self._let_go(draft)
                        draft = None
                    if self._drafted:
                        trace.draft = "dropped"
                    await self._save_input("user", text, marker=_no_note(declined))
                answer = True
                self._collecting = False
            elif not only_her:
                # Sound, but no words in it (or only her own, come back). Said, so the
                # silence is not a mystery. Only her voice heard: nothing was missed.
                await self._send("notice", code="listening", reason="unheard")
        except VoiceError as e:
            logger.warning("listening failed in session %s: %s", self.id, e.kind)
            await self._hearing_down(e.kind)
        except asyncio.CancelledError:
            await listener.abandon()
            await self._send("interrupted")
        except Exception as e:
            logger.error("listening failed in session %s: %s", self.id, type(e).__name__)
            await self._error("internal")
        finally:
            self._collecting = False
            # Not used: nobody will read it. A turn cancelled here leaves none behind.
            if draft is not None:
                await self._let_go(draft)
            if not answer:
                await self._drop_draft()
        if answer:
            await self._run()
        else:
            self._task = None
            # No reply is coming: a watch for one ends.
            await self._watch_settles()
            await self._state(self._rest)

    async def _on_tap(self, pending_id: object, decision: Literal["confirm", "cancel"]) -> None:
        if not isinstance(pending_id, str) or not pending_id:
            await self._error("bad_message")
            return
        self.active_ms = self._rt.clock()
        async with self._taking_input():
            await self._tapped(pending_id, decision)

    async def _tapped(self, pending_id: str, decision: Literal["confirm", "cancel"]) -> None:
        if await self._routine_building():
            # The card stays: tapped again once the routine is done, it is answered.
            return
        # Whose chat this is, read now: a tap can come before any turn here.
        await self._read_place()
        # A tap is the user's own act on one card, so it is allowed at once: it is judged
        # as the turn after the newest thing said. It does not advance the turn the model
        # acts in. If it did, tapping one card mid-turn would let the model confirm
        # another in the very turn it proposed it (SPN-2e).
        response = await self._rt.router.resolve(
            pending_id,
            decision,
            await self._ctx(f"tap-{uuid.uuid4().hex}", via="tap", turn=self._turn + 1),
        )
        if self._read_outside(None, response):
            # What she says and does on the result is something she read (MEM-5), as
            # when the same read runs without a card. Her answer runs in this turn.
            self._outside.add(self._turn)
        await self._send(
            "confirm.resolved",
            pending_id=pending_id,
            status=response.status,
            reason=response.reason.value if response.reason is not None else None,
            detail=response.detail or None,
        )
        await self._helper_made(response)
        action = "Approve" if decision == "confirm" else "Decline"
        note = (
            f"[host] The user tapped {action} on the card for {pending_id}. Say what came "
            "of it in one short sentence, and don't repeat what you said before. Result: "
        )
        self._queued.append(note + response.to_model())
        if self._task is None:
            self._begin()

    async def _said_no(
        self, text: str, via: Literal["voice", "text"], turn: int
    ) -> RouterResponse | None:
        """A plain no, as their whole turn, ends the card her last reply raised
        (SPN-2f): the host cancels it, not her. The cancelled card's response, or None.
        A failure here is never the turn's: it is logged, and their words go to her."""
        if not plain_no(text):
            # Most turns: nothing to look up.
            return None
        try:
            ctx = await self._ctx(f"no-{uuid.uuid4().hex}", via=via, turn=turn)
            response = await self._rt.router.cancel_on_no(
                text, ctx, asked=frozenset(self._asked_last)
            )
        except Exception as e:
            logger.warning("plain no not applied in session %s: %s", self.id, type(e).__name__)
            return None
        if response is None or response.pending_id is None:
            return None
        return response

    async def _show_declined(self, response: RouterResponse) -> None:
        """The card goes from the page, as after a tap on Decline."""
        await self._send(
            "confirm.resolved",
            pending_id=response.pending_id,
            status=response.status,
            reason=None,
            detail=None,
        )

    def _read_outside(self, name: str | None, r: RouterResponse) -> bool:
        """A call that succeeded with someone else's words. A confirmed card counts as
        the tool it ran, by tap or by her `confirm_pending_action`, never as itself."""
        ran = r.tool or name
        if r.status != "succeeded" or ran is None:
            return False
        return is_outside(ran, self._rt.registry.definition(ran))

    # ── A turn ───────────────────────────────────────────────────────────

    def _begin(self, *, follow_up: bool = False) -> None:
        self._task = asyncio.create_task(self._run(follow_up=follow_up))

    @_calls_as("conversation")
    async def _run(self, *, follow_up: bool = False) -> None:
        """Turns, one after another, until nothing is waiting.

        `_task` stays set for the whole of it, so a second turn can never start beside
        this one: a message that arrives meanwhile is refused as busy, and a tap's note
        is picked up by the loop. `follow_up`: it brings in background reads that ended."""
        stopped = False
        mine = False
        try:
            if follow_up:
                try:
                    stopped = not await self._bring_in()
                except asyncio.CancelledError:
                    # Stopped before it was saved: it stays, for the next pause.
                    stopped = True
                except Exception as e:
                    # What was not saved stays, and is tried at the next pause; this one
                    # still comes to rest, with nothing of the follow-up left set.
                    logger.warning(
                        "background note not saved in session %s: %s", self.id, type(e).__name__
                    )
                    self._listen_after = False
                    stopped = True
                    self._look_again_soon()
            while not stopped and not self._closed:
                if self._queued:
                    if self._using is not None:
                        # A tap's note joins the history: the draft never saw it.
                        await self._unuse()
                    # What the taps did, in one message, for her to answer.
                    note, self._queued = "\n".join(self._queued), []
                    await self._save_input("tap", note)
                if self._rt.provider is None:
                    break
                if self._using is None:
                    # A draft was only started on a history short enough to send.
                    await self._compact_if_too_long()
                await self._one_turn()
                if not self._queued:
                    break
        finally:
            # Only its own: never a task that started after this one was let go.
            if self._task is asyncio.current_task():
                self._task = None
                mine = True
                if self._building_day:
                    # Before anything awaits, so no input of the user's has begun: their
                    # next answer to a card is theirs again, never taken for an ask.
                    self._building_day = False
                    self._via = "text"
                if follow_up:
                    # Likewise: the user's next words are theirs again, never an ask.
                    self._via = "voice" if self._aloud else "text"
                    self._aloud = False
            if self._using is not None:
                # Never reached its turn.
                await self._unuse()
        # A watch over her reply ends now unless something of hers is still to be heard.
        await self._watch_settles()
        if not (mine and await self._after_turn()):
            await self._state(self._rest)
        if mine and not self._closed:
            # Her reply is out: only now is a long history summarized (BRN-5).
            await self._compact_after_turn()

    async def _after_turn(self) -> bool:
        """What the microphone does now her turn is over, decided only now, so a page's
        "played" that came while the turn was finishing is never lost. True when it
        opened, so the orb is left to say so."""
        quiet, self._quiet = self._quiet, None
        early, self._ended_early = self._ended_early, False
        if (
            self._closed
            or self._stopped
            or self._task is not None
            or self._listener is not None
            or not self._answered_aloud()
        ):
            return False
        ov = self._overlap
        if ov is not None:
            if quiet == "listening" and ov.watching and not ov.promoting:
                # The page is watching over her: that watch goes on as the window once
                # her silence is heard (`_follow_up_from`), so it is still the quiet one.
                self._quiet, self._quiet_watch = quiet, ov
            return False
        if quiet == "listening":
            # They trailed off: the floor stays theirs a while (VOI-7).
            await self._on_listen(QUIET_LISTEN_S, follow_up=True, quiet=True)
        elif quiet is None and early:
            await self._on_listen(FOLLOW_UP_S, follow_up=True)
        else:
            return False
        return self._listener is not None

    async def _one_turn(self) -> None:
        rt = self._rt
        # A switch whose time has passed ends before the turn takes its model (BRN-8).
        await self._spend_tick()
        # The turn she acts in is fixed when it starts. Nothing that happens while she
        # works, a tap least of all, can move her into a later one.
        turn = self._turn
        provider = rt.provider
        if provider is None:  # pragma: no cover - callers check before starting a turn
            return
        # From here on, the cards she asks about are this reply's.
        self._asked_last = set()
        speaking = False
        speaker: Speaker | None = None
        timeline: Timeline | None = None
        heard_as = turn
        # A "played" from here on is this turn's, or a later one's.
        self._ended_early = False
        self._quiet = self._quiet_turn = self._quiet_watch = None
        self._playing = None
        if (
            rt.tts is not None
            and rt.speech_used is not None
            and not self._muted
            and not self._voice_held()
        ):
            # A turn's number is heard once: a tap's answer in the same turn gets its own,
            # or the page's "played" for the first would pass for it (VOI-17).
            heard_as = rt.out_of_turn() if self._voiced == turn else turn
            self._voiced = turn
            # Her audio, as it is sent, places her on the recognizer's clock (#102).
            timeline = tl = self._reply_timeline()
            self._timeline_turn = heard_as

            def on_audio(seq: int, written: str, spoken: str, levels: list[float]) -> None:
                at = self._clock_now()
                tl.sentence(seq, written=written, spoken=spoken, levels=levels, at_s=at)

            speaker = Speaker(
                rt.tts,
                rt.speech_used.voice,
                heard_as,
                self._send,
                self._send_bytes,
                on_audio,
                on_capped=self._voice_capped,
            )
        self._speaker = speaker
        # With no voice there is nothing to be heard, so no "played" is this turn's.
        self._playing = heard_as if speaker is not None else None
        self._tail = None
        # Only a spoken reply to a spoken turn is cut to what was heard: a typed one was
        # read, and so was one with her voice off (D8).
        listened = speaker is not None and (self._via == "voice" or self._aloud)
        quick = self._quick()
        # What the turn saved of the round it was cut off in, when that was cut to what
        # was heard. Set only by the loop, so the page is told exactly what was kept.
        kept: str | None = None

        def heard(_written: str) -> str | None:
            nonlocal kept
            if speaker is None or speaker.failed or self._muted:
                # Her voice went or was turned off midway: the rest was read, not lost.
                return None
            said = speaker.heard_text()
            kept = cut(said)
            return said

        # The id of the last message the loop saved, when it is her reply.
        reply_id: int | None = None

        async def save(msg: Msg) -> None:
            nonlocal reply_id
            await self._save_from_loop(msg)
            reply_id = self._ids[-1] if msg.role == "assistant" and self._ids else None

        # Whether the loop has finished writing: after that, a barge trims what it saved.
        written = False
        # A turn nobody started (a tap's answer) starts now.
        trace, self._arrived = self._arrived or self._starting("tap"), None
        trace.turn = turn
        trace.heard_as = heard_as
        self._trace = trace

        async def emit(ev: LoopEvent) -> None:
            nonlocal speaking
            if isinstance(ev, CallTimed):
                trace.call(ev)
            elif isinstance(ev, ReplyDelta):
                trace.mark("first_text", self._now())
                if not speaking:
                    speaking = True
                    await self._state("speaking")
                await self._send("reply.delta", text=ev.text)
                if speaker is not None:
                    await speaker.feed(ev.text)
            elif isinstance(ev, ToolStarted):
                trace.tools += 1
                if speaker is not None:
                    await speaker.pause()
                speaking = False
                await self._state("processing")
                await self._send("tool.start", call_id=ev.call_id, name=ev.name, risk=ev.risk)
            elif isinstance(ev, ToolEnded):
                r = ev.response
                if self._read_outside(ev.name, r):
                    # From here to the end of the turn, what she writes to memory is
                    # something she read, not something the user said.
                    self._outside.add(turn)
                if r.status == "succeeded":
                    self._remember_read(turn, ev.name, ev.args)
                    # Confirmed by her `confirm_pending_action` on the user's yes.
                    await self._helper_made(r)
                    if ev.name == HELPER_SUGGEST:
                        await self._offered()
                await self._send(
                    "tool.end",
                    call_id=ev.call_id,
                    ok=r.status in ("succeeded", "confirmation_required", "started"),
                    summary=_chip(r),
                )
                if r.status == "confirmation_required" and r.pending_id:
                    self._asked_last.add(r.pending_id)
                # A call proposed again while its card waits gets no second card.
                if r.status == "confirmation_required" and r.pending_id and not r.already_asked:
                    await self._send_card(r.pending_id)

        final = "idle"
        # The draft this turn replays as its first call, if it has one (VOI-12).
        using, self._using = self._using, None
        try:
            await self._state("processing")
            # Rules and the skills that are on, read once for this turn, off the event loop.
            system = await asyncio.to_thread(rt.system)
            await self._read_place()
            if using is not None and list(using.system) != system:
                # A rule or skill changed while the draft ran: it sent an old prompt.
                trace.draft = "dropped"
                await self._let_go(using)
                using = None
            # A turn that answers the app's own note (a card's tap, a background read, the
            # day's build) and not the user's words: she may not stay quiet on it (VOI-7).
            host_turn = self._kinds[-1:] == ["tap"]
            # Their words end in a question: she answers it, never stays quiet (VOI-7).
            asked = self._asked()
            done = await run_turn(
                provider=provider,
                router=rt.router,
                registry=rt.registry.offered(self._offers),
                system=system,
                history=compact.with_summary(self._summary, self._history),
                ctx=lambda call_id: self._ctx(call_id, turn=turn, host_turn=host_turn, asked=asked),
                # The day board is built from its reads in one turn: none goes aside.
                background=None if self._building_day else self._background,
                emit=emit,
                save=save,
                first=using.replay() if using is not None else None,
                heard=heard if listened else None,
                quick=quick,
            )
            written = True
            # The model failed: her one sentence for it, spoken if her voice still works
            # and shown either way (VOI-14). Authored, since there is nobody to compose it.
            # At its hard cap the line says which cap (BRN-8); what was written before the
            # refusal is already saved, and is spoken before it.
            at_cap = done.stop == "provider_error" and done.error_kind == "capped"
            capped_pid = getattr(provider, "provider_id", None) if at_cap else None
            down = (
                self._capped_line("model", capped_pid)
                if capped_pid is not None
                else degraded("model", done.error_kind or "other")
                if done.stop == "provider_error"
                else None
            )
            if (
                listened
                and speaker is not None
                and done.stop == "end"
                and reply_id is not None
                and self._ids[-1:] == [reply_id]
            ):
                # Written and saved, but still to be heard: kept in case she is talked
                # over while it plays.
                self._tail = (speaker, reply_id)
            # Each model call of the turn was recorded as it ended, by the metered
            # provider (BRN-7).
            if speaker is not None:
                if down is not None:
                    await speaker.pause()
                    await speaker.feed(down)
                await speaker.finish()
                self._spoken = (heard_as, rt.clock())
            if timeline is not None:
                timeline.finish()
            if down is not None and capped_pid is not None:
                final = "error"
                await self._spend_capped("model", capped_pid, down)
            elif down is not None:
                final = "error"
                await self._error("provider", kind=done.error_kind, sentence=down)
            elif done.stop == "quiet":
                # A chosen silence is no failure: it has no notice (VOI-7).
                await self._quiet_ends(done.quiet, heard_as)
            elif done.stop != "end":
                await self._send("notice", code="stopped", reason=done.stop)
            # Kept before the page hears the turn is over, so the next one never waits on it.
            await self._keep_trace(trace, done.stop, voiced=speaker is not None)
            u = done.usage
            await self._send(
                "turn.complete",
                stop=done.stop,
                **({"quiet": done.quiet} if done.quiet is not None else {}),
                cost_usd=done.cost_usd,
                usage={
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_read_tokens": u.cache_read_tokens,
                    "cache_write_tokens": u.cache_write_tokens,
                },
            )
            # A provider this turn took to its warning is said once, through the door.
            await self._spend_warn()
        except asyncio.CancelledError:
            if timeline is not None:
                timeline.stop(at_s=self._clock_now())
            if speaker is not None:
                await speaker.stop()
                # Stopped: nothing of it is still to be heard.
                self._spoken = None
            if written:
                # Cut off while her last words were still being voiced.
                kept = await self.trim_tail(tell=False)
            await self._keep_trace(trace, "interrupted", voiced=speaker is not None)
            # With `kept`, the page shows what was heard in place of what was written.
            await self._send("interrupted", **({"kept": kept} if kept is not None else {}))
        except Exception as e:
            # The class name only: an exception message can carry conversation text.
            logger.error("turn failed in session %s: %s", self.id, type(e).__name__)
            if timeline is not None:
                # Nothing more of hers is coming.
                timeline.stop(at_s=self._clock_now())
            final = "error"
            await self._keep_trace(trace, "error", voiced=speaker is not None)
            await self._error("internal")
        finally:
            # Its reads are this turn's: a widget saved in a later turn never remembers them.
            rt.reads.end(self.id, turn)
            if self._speaker is speaker:
                self._speaker = None
            if using is not None:
                # Already over unless the turn was cut short. Its usage is the turn's.
                await using.cancel()
        if not self._closed:
            # However the turn ended: what counts is that a reply of hers was saved.
            self._maybe_name()
        self._rest = final

    async def _quiet_ends(self, kind: str | None, heard_as: int) -> None:
        """She chose to say nothing (VOI-7). A sign-off is marked seen, and a spoken one
        closes the microphone; a trailed-off thought keeps it open once the turn is over."""
        self._quiet, self._quiet_turn = kind, heard_as
        if kind == "listening":
            return
        # Seen, so a sign-off never looks unanswered. Saved as the call and its answer:
        # a reload marks it again from them.
        await self._send("seen")
        if self._answered_aloud():
            # A sign-off ends active capture (VOI-15d): nothing reopens after it.
            self._listen_after = False
            await self._send("mic.stop", reason="done")

    def _remember_read(self, turn: int, name: str, args: Mapping[str, Any]) -> None:
        """A read that succeeded this turn, kept while the turn runs so a widget she
        builds from it can be kept fresh: only one that may run with nobody deciding,
        from a connector a widget can name (the live widgets spec)."""
        rt = self._rt
        if not live.eligible(rt.registry.definition(name)):
            return
        title = live.connector(name, rt.registry.get(name))
        if title is not None:
            rt.reads.record(self.id, turn, name, args, title)

    # ── Background reads (the background reads spec) ────────────────────

    async def _background(
        self, u: ToolUse, d: ToolDefinition, ctx: ToolContext
    ) -> RouterResponse | None:
        """Start a slow read in the background and answer for it at once, or None to
        run it in the turn: not one that goes there (D1), or three already running."""
        rt = self._rt
        if not goes_to_background(d):
            return None
        same = rt.jobs.same(self.id, u.name, u.args)
        if same is not None:
            return RouterResponse(status="refused", detail=f"already running as job {same.id}")
        try:
            job = await rt.jobs.start(
                self.id,
                u.name,
                u.args,
                lambda: background_read(rt.router, d, u, ctx),
                lambda job, r: background_ended(rt, job, r),
            )
        except Full:
            return None
        return RouterResponse(status="started", job=job.id, detail=STARTED)

    async def job_ended(self, finished: _Finished) -> None:
        """A background read of this conversation ended: brought in at the next pause."""
        if self._closed:
            # Deleted or closed meanwhile: whoever closed it keeps what it had.
            return
        self._finished.append(finished)
        self._follow_up_if_free()

    def _follow_up_if_free(self) -> None:
        """Begin the turn that brings in what ended, only when the door would deliver a
        notice (D2): never over the user, never over her own reply. No await, so nothing
        can start between the look and the turn."""
        rt = self._rt
        if self._closed or not (self._finished or self._unanswered) or rt.provider is None:
            return
        if self.id in rt.routine_holds:
            return
        if self._stopped or self._again is not None:
            # What arrives at the rest a Stop made is never spoken: it comes at the next.
            self._look_again_soon()
            return
        if not free(self.facts(), rt.clock()):
            self._look_again_after_grace()
            return
        # Her words on what she read can approve nothing (widget design D5). Answering a
        # spoken conversation, the microphone comes back after her, as after a spoken turn.
        self._aloud = self._via == "voice"
        self._listen_after = self._aloud
        self._via = "ask"
        self._begin(follow_up=True)

    def _look_again_after_grace(self) -> None:
        """A page that never says it played her reply stops holding notices back after
        the door's grace; nothing else may happen then, so the follow-up looks again."""
        spoken = self._spoken
        if spoken is None or self._played == spoken[0] or self._grace is not None:
            return
        left_ms = spoken[1] + delivery.PLAYBACK_GRACE_MS - self._rt.clock()
        if left_ms < 0:
            return

        def again() -> None:
            self._grace = None
            self._follow_up_if_free()

        # Just past the grace: `free` wants more than it, not as much.
        self._grace = asyncio.get_running_loop().call_later(left_ms / 1000 + 0.01, again)

    def _look_again_soon(self) -> None:
        if self._again is not None or self._closed:
            return

        def again() -> None:
            self._again = None
            self._follow_up_if_free()

        self._again = asyncio.get_running_loop().call_later(PAUSE_AGAIN_S, again)

    async def _bring_in(self) -> bool:
        """Save what ended as the host's notes, one message each, and set the turn up to
        answer them. Until they are saved they stay where they were: a Stop before then
        loses nothing, and once saving has begun it is finished. False when there is
        nothing to answer: the conversation is gone."""
        done = list(self._finished)
        if done:
            self._arrived = self._starting("tap")
            built = await self._build_input("tap", done[0].note, marker=BACKGROUND)
            saving = asyncio.ensure_future(self._save_notes(built, done))
            try:
                if not await asyncio.shield(saving):
                    return False
            except asyncio.CancelledError:
                while not saving.done():
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.wait({saving})
                # Saved, and still to be answered at the next pause.
                self._unanswered = (
                    not saving.cancelled() and saving.exception() is None and saving.result()
                )
                raise
        rt = self._rt
        self._unanswered = False
        reads, notices = rt.jobs.take(self.id)
        reads += [d.read for d in done if d.read is not None]
        # What she says and does on it is something she read (MEM-5).
        self._outside.add(self._turn)
        # A widget she builds from it now remembers the read, as in the turn that made it.
        for name, args in reads:
            self._remember_read(self._turn, name, args)
        for notice_id in notices:
            # This turn is its ready line: the notice never arrives as well.
            if rt.schedules is not None:
                with contextlib.suppress(Exception):
                    await rt.schedules.mark_delivered(notice_id, self.id, rt.clock())
        return True

    async def _save_notes(self, built: BuiltInput, done: Sequence[_Finished]) -> bool:
        """Save each note, taking it off the waiting list as it lands, so a save that
        fails part way never saves one twice. False, with nothing more saved or kept,
        once the conversation is gone."""
        for i, d in enumerate(done):
            if not await self._rt.conversations.exists(self.id, OWNER):
                self._finished.clear()
                self._unanswered = False
                return False
            if i == 0:
                await self._commit_input("tap", built)
            else:
                await self._save("tap", background_msg(d.note))
            del self._finished[:1]
        return True

    # ── Naming (SUR-1) ───────────────────────────────────────────────────

    def _maybe_name(self) -> None:
        """After her first reply: name the conversation in the background, so the
        next turn never waits on it."""
        if self._naming is not None or self._title_source in ("model", "user"):
            return
        exchange = self._first_exchange()
        if exchange is None or self._rt.provider is None:
            return
        self._naming = asyncio.create_task(self._name(*exchange))

    def _first_exchange(self) -> tuple[Msg, Msg] | None:
        """The user's first message and her answer to it: her last words before the
        next input, so the answer and not a "Let me check." before a tool call. A
        reply cut off before any word is no answer; the next one with words is."""
        # The live history: after compaction on a resumed long conversation, this is
        # the oldest exchange still in it, not the very first one.
        said: Msg | None = None
        answer: str | None = None
        for m, k in zip(self._history, self._kinds, strict=True):
            if k in ("user", "tap"):
                if answer is not None:
                    break
                if k == "user" and said is None:
                    said = m
            elif k == "assistant" and said is not None:
                text = "".join(b.text for b in m.blocks if isinstance(b, TextBlock))
                # The mark an interrupted reply is saved with is not her words.
                text = text.replace(INTERRUPTED.strip(), "").strip()
                if text:
                    answer = text
        if said is None or answer is None:
            return None
        return said, Msg(role="assistant", blocks=(TextBlock(text=answer),))

    # Her naming the conversation is the title writer's, which nobody waits on.
    @_calls_as("background", work="titles")
    async def _name(self, said: Msg, reply: Msg) -> None:
        rt = self._rt
        # Background work: the usual model, never a switch (`background_provider`).
        provider = rt.background_provider
        if provider is None:
            return
        if await rt.held("titles"):
            # At the hard spend cap: the first-words title stays, and nothing is said.
            return
        # Its call is recorded by the metered provider, even if the session closes.
        named, _usage = await naming.name(provider, [said, reply])
        try:
            if named is None:
                return
            if await rt.conversations.set_title(self.id, OWNER, named, "model"):
                self._title_source = "model"
                await self._broadcast_title(named)
        except Exception as e:
            # Best effort: the first-words title stays. The class name only.
            logger.warning("naming in session %s failed: %s", self.id, type(e).__name__)

    async def _broadcast_title(self, text: str) -> None:
        """Every page open on this conversation shows its new title."""
        for s in list(self._rt.sessions):
            if s.id == self.id:
                await s._send("conversation.title", id=self.id, title=text)

    # ── Early thinking (VOI-12) ──────────────────────────────────────────

    async def _turn_signal(self, kind: str, text: str) -> None:
        """The recognizer thinks they have probably finished, or that they carried on.
        A draft starts on the first and is let go on the second."""
        if kind == "resumed":
            await self._drop_draft()
        elif kind == "maybe":
            await self._drop_draft()
            await self._start_draft(text)

    @_calls_as("conversation")
    async def _start_draft(self, words: str) -> None:
        """Her first call for these words, sent exactly as the confirmed turn would
        send it, and held. Nothing is saved, shown, said or run.

        Runs on the recognizer's reader task, so it must stay quick: a database read,
        a memory recall and the system prompt, read off the event loop."""
        rt = self._rt
        provider = rt.provider
        words = words[:MAX_TEXT].strip()
        if (
            self._closed
            or not rt.think_early
            or provider is None
            or not words
            or self._task is not None
            or self._queued
            # Deciding whether she was interrupted: her reply must stay trimmable.
            or self._overlap is not None
        ):
            return
        # A listener is only open on a spoken turn: the message is marked as spoken.
        # Built before the message is counted: it will be the next one.
        built = await self._build_input("user", words, via="voice", turn=self._turn + 1)
        # The same as `_one_turn` sends: its rules and skills, its history, its tools.
        system = await asyncio.to_thread(rt.system)
        # Read after the awaits, so a trim meanwhile is in what is sent.
        history = [*self._history, built.msg]
        # A history the turn would summarize first is not the history it would send.
        if compact.estimate(history) > compact.ceiling(rt.compact_at):
            return
        # The model is about to be sent her last reply: it can no longer be trimmed.
        self._tail = None
        self._draft = Draft.start(
            provider=provider,
            system=system,
            messages=compact.with_summary(self._summary, history),
            tools=tool_schemas(rt.registry.offered(self._offers)),
            words=words,
            built=built,
            history_len=len(self._history),
            summary=self._summary,
            # As the turn that replays it will send it.
            quick=self._quick(),
        )
        self._drafted = True

    def _usable(self, draft: Draft, text: str) -> bool:
        """The draft heard these words and sent what this turn would send."""
        return (
            not draft.failed
            and draft.provider is self._rt.provider
            and same_words(draft.words, text)
            and draft.history_len == len(self._history)
            and draft.summary is self._summary
            and not self._queued
        )

    async def _drop_draft(self) -> None:
        """Let go of a draft not yet any turn's. Safe when there is none."""
        draft, self._draft = self._draft, None
        if draft is not None:
            await self._let_go(draft)

    async def _unuse(self) -> None:
        """A draft taken for a turn that will not replay it after all."""
        using, self._using = self._using, None
        if using is None:
            return
        if self._arrived is not None:
            self._arrived.draft = "dropped"
        await self._let_go(using)

    async def _let_go(self, draft: Draft) -> None:
        """Stop a draft nobody will replay. What it cost was recorded by the metered
        provider when its call ended (BRN-7): a provider reports usage at the end of a
        call, so one stopped before then counts as nothing."""
        await draft.cancel()

    # ── Timing (ONB-11) ──────────────────────────────────────────────────

    def _warm(self) -> None:
        """While they talk, open the connections her answer will need: the model's and
        the voice's. Nothing is sent; a provider with nothing to open is skipped."""
        rt = self._rt
        calls = [
            warm()
            for p in (rt.provider, None if self._muted else rt.tts)
            if p is not None and callable(warm := getattr(p, "warm", None))
        ]
        if calls and (self._warming is None or self._warming.done()):
            self._warming = asyncio.ensure_future(asyncio.gather(*calls))
        else:
            for call in calls:
                call.close()

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _starting(self, via: Via, *, ago: float = 0.0) -> Trace:
        return Trace(via=via, t0=self._now() - ago)

    async def _keep_trace(self, trace: Trace, stop: str, *, voiced: bool) -> None:
        """Save how the turn went, once. A timing that cannot be saved costs nothing else."""
        if self._trace is not trace:
            return
        trace.mark("saved", self._now())
        self._trace = None
        self._last_trace = trace
        rt = self._rt
        provider = rt.provider
        # A playback report can come while the row is being saved: it marks the trace,
        # but there is no row to update yet. It is written once the row exists.
        had_played = "played" in trace.marks
        row = trace.row(
            conversation_id=self.id,
            first_in_session=self._timed == 0,
            provider=provider.name if provider is not None else "",
            model=provider.model if provider is not None else "",
            voice=rt.speech_used.kind if voiced and rt.speech_used is not None else None,
            ears=rt.listening_used.kind
            if trace.via == "voice" and rt.listening_used is not None
            else None,
            stop=stop,
        )
        self._timed += 1
        try:
            trace.row_id = await rt.conversations.record_timing(OWNER, row, rt.clock())
            if not had_played and "played" in trace.marks:
                played = trace.ms(trace.marks["played"])
                await rt.conversations.timing_played(OWNER, trace.row_id, played)
        except Exception as e:
            logger.warning("timing not saved in session %s: %s", self.id, type(e).__name__)

    async def trim_tail(self, *, tell: bool = True) -> str | None:
        """Talked over after her reply was written: keep only what was heard (VOI-9, D8).

        History is never changed once the model has been sent it. Her last spoken reply
        is the one exception: while it is still the conversation's last message, with
        no draft that has read it, it is cut to the sentences heard, as the loop cuts a
        reply interrupted mid-stream. Every sentence having started is not all of it
        heard: the last is still playing, and is kept and marked like the others. Only
        `playback.ended` says she was heard to the end, and it lets the reply go.
        Nothing changes if her voice failed or was muted (then it was read). Returns
        what was kept, and with `tell` sends it to the page; None when nothing changed."""
        tail, self._tail = self._tail, None
        if tail is None:
            return None
        speaker, message_id = tail
        if (
            self._ids[-1:] != [message_id]
            or self._draft is not None
            or self._using is not None
            or speaker.failed
            or self._muted
        ):
            return None
        blocks = self._history[-1].blocks
        if len(blocks) != 1 or not isinstance(blocks[0], TextBlock):
            return None  # only a plain reply: a tool call is never cut
        written, heard = blocks[0].text, speaker.heard_text()
        if not written.startswith(heard):  # pragma: no cover - the speaker only saw this
            return None
        msg = Msg(role="assistant", blocks=(TextBlock(text=kept_of(written, heard)),))
        # A change to what is saved here, as `_save` is: a reload reading meanwhile is dropped.
        self._saves += 1
        try:
            changed = await self._rt.conversations.amend_tail(self.id, OWNER, message_id, msg)
        except Exception as e:
            logger.warning("reply not trimmed in session %s: %s", self.id, type(e).__name__)
            return None
        if not changed:
            return None
        if message_id in self._ids:
            self._history[self._ids.index(message_id)] = msg
        kept = cut(heard)
        if tell:
            await self._send("interrupted", kept=kept)
        return kept

    def _sentence_played(self, turn_id: object, seq: object) -> None:
        """The page started playing a sentence of a reply (VOI-15b, D8). Only the running
        turn's count: a report for one already over, or for a notice, is late news."""
        if isinstance(turn_id, bool) or not isinstance(seq, int) or isinstance(seq, bool):
            return
        tail = self._tail[0] if self._tail is not None else None
        for speaker in {self._speaker, tail}:
            if speaker is not None and speaker.turn == turn_id:
                speaker.played(seq)
        # Where it starts on the recognizer's clock: the audio the watching listener has
        # sent by now, as the page sends its reports and its microphone in order. With
        # no listener watching there is no clock to place it on.
        timeline, now = self._timeline, self._clock_now()
        if timeline is not None and self._timeline_turn == turn_id:
            self._reported = (seq, self._now())
            if now is not None:
                timeline.start(seq, at_s=now)

    async def _audible(self, turn_id: object, in_ms: object) -> None:
        """The page scheduled the first sound of a turn's reply, to be heard this far ahead."""
        if isinstance(turn_id, bool) or not isinstance(turn_id, int):
            return
        at = self._now() + ahead_ms(in_ms) / 1000
        running, last = self._trace, self._last_trace
        if running is not None and running.heard_as == turn_id:
            running.mark("played", at)
        elif last is not None and last.heard_as == turn_id and last.mark("played", at):
            # Heard after the turn was saved: a long first sentence, or a slow page.
            if last.row_id is not None:
                with contextlib.suppress(Exception):
                    await self._rt.conversations.timing_played(OWNER, last.row_id, last.ms(at))

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _ctx(
        self,
        call_id: str,
        via: Literal["voice", "text", "tap", "channel"] | None = None,
        *,
        turn: int | None = None,
        host_turn: bool = False,
        asked: bool = False,
    ) -> ToolContext:
        at = self._turn if turn is None else turn
        # Looked at for each call and each tap, off the event loop. A tap's context is
        # the one the router checks the approved card against, so a share removed or
        # set to read only since the card was raised refuses it (SPN-2c).
        roots, read_only = await self._rt.reach()
        return ToolContext(
            owner_id=OWNER,
            session_id=self.id,
            turn=at,
            call_id=call_id,
            via=via or self._via,
            granted_roots=roots,
            read_only_roots=read_only,
            read_outside=at in self._outside,
            host_turn=host_turn,
            asked=asked,
            # In a helper's own chat, every call and tap is the helper's (AGT-2).
            helper_id=self._helper.id if self._helper is not None else None,
        )

    def _asked(self) -> bool:
        """The user's words this turn, spoken or typed, end in a question mark. A used
        draft saved the words it answered, so they are these too."""
        if self._kinds[-1:] != ["user"]:
            return False
        texts = [b.text for b in self._history[-1].blocks if isinstance(b, TextBlock)]
        return bool(texts) and asks(texts[-1])

    async def _save(self, kind: Kind, msg: Msg) -> None:
        # Something after her last reply: it is no longer the one to trim.
        self._tail = None
        self._saves += 1
        self._history.append(msg)
        self._kinds.append(kind)
        message_id = await self._rt.conversations.append(
            self.id, OWNER, kind, msg, self._rt.clock()
        )
        self._ids.append(message_id)

    # ── Compaction (BRN-5) ───────────────────────────────────────────────

    def _quick(self) -> bool:
        """Her replies are spoken in this session: every turn, typed ones too, asks the
        model for its first word sooner. Fixed for the session unless her voice is
        turned on or off, so the cached conversation is not reset turn by turn."""
        rt = self._rt
        return rt.tts is not None and rt.speech_used is not None and not self._muted

    def _between_turns(self) -> bool:
        """Nothing is reading the history: no turn, no input being taken, no draft."""
        return (
            not self._closed and not self.writing() and self._draft is None and self._using is None
        )

    async def _compact_after_turn(self) -> None:
        """Her turn is over: put a summary written meanwhile in place, and start the next
        one if the history is still past the summary point. Never fails the turn."""
        written, self._written = self._written, None
        if written is not None:
            try:
                await self._put_in_place(written, idle=True)
            except Exception as e:
                logger.warning("compaction in session %s failed: %s", self.id, type(e).__name__)
        self._compact_later()

    def _compact_later(self) -> None:
        """Summarize the oldest turns in the background, once the history is past the
        summary point. One summary at a time, and none while one waits to be put in
        place, nor soon after one failed (`_backing_off`)."""
        rt = self._rt
        running = self._compacting
        if (
            self._closed
            or rt.provider is None
            or self._written is not None
            or (running is not None and not running.done())
            or compact.estimate(self._history) <= rt.compact_at
            or self._backing_off()
        ):
            return
        self._compacting = asyncio.create_task(self._compact_in_background())

    def _backing_off(self) -> bool:
        """A summary failed, and the history has grown by less than half the summary
        point since: a model that cannot write one is not asked every turn."""
        failed = self._compact_failed_at
        grown = compact.estimate(self._history) - (failed or 0)
        return failed is not None and grown < self._rt.compact_at // 2

    async def _compact_in_background(self) -> None:
        """Write the summary while the user carries on. A turn that starts meanwhile is
        sent the whole history; the summary is put in place at the next pause."""
        try:
            written = await self._summarize(compact.TIMEOUT_S)
            if written is not None:
                await self._put_in_place(written, idle=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The class name only. The history stays whole and is tried again later.
            logger.warning("compaction in session %s failed: %s", self.id, type(e).__name__)
        finally:
            if self._compacting is asyncio.current_task():
                self._compacting = None

    async def _compact_if_too_long(self) -> None:
        """Before a turn: only a history past the ceiling, too long to send as it is,
        waits for a summary, and not for long (`compact.WAIT_S`): one being written is
        given that long, or else one is written in that long. Failing that, the turn
        goes ahead on the full history: a long prompt is better than a lost one."""
        rt = self._rt
        if compact.estimate(self._history) <= compact.ceiling(rt.compact_at):
            return
        running = self._compacting
        if running is not None and not running.done():
            # Already being written: wait a while for that one rather than write a
            # second. A cancelled turn, or one that waits no longer, leaves it running.
            await asyncio.wait({running}, timeout=compact.WAIT_S)
            written, self._written = self._written, None
            if written is not None:
                await self._put_in_place(written)
            return
        written, self._written = self._written, None
        if written is None and not self._backing_off():
            written = await self._summarize(compact.WAIT_S)
        if written is not None:
            await self._put_in_place(written)

    @_calls_as("conversation")
    async def _summarize(self, timeout: float) -> _Written | None:
        """Notes on the oldest turns of the history as it is now, or None if it needs
        none or they could not be written. Changes nothing but what was paid for."""
        rt = self._rt
        provider = rt.provider
        if provider is None or compact.estimate(self._history) <= rt.compact_at:
            return None
        cut = compact.cut_point(self._kinds, self._history, rt.compact_keep)
        if cut is None:
            return None
        ids, previous, older = tuple(self._ids[:cut]), self._summary, self._history[:cut]
        try:
            summary = await asyncio.wait_for(
                compact.summarize(provider, compaction(rt.identity), previous, older), timeout
            )
        except (ProviderError, compact.CompactionFailed, TimeoutError) as e:
            logger.warning("compaction in session %s failed: %s", self.id, type(e).__name__)
            self._compact_failed_at = compact.estimate(self._history)
            return None
        self._compact_failed_at = None
        # Paid for whether or not it is put in place: the metered provider recorded it.
        return _Written(summary=summary, ids=ids, previous=previous)

    def _still_fits(self, written: _Written) -> bool:
        """The history still begins with what the summary replaces, after the summary it
        was written on: no reload or other summary has changed it since."""
        n = len(written.ids)
        return (
            not self._closed
            and tuple(self._ids[:n]) == written.ids
            and len(self._ids) > n
            and self._summary == written.previous
        )

    async def _put_in_place(self, written: _Written, *, idle: bool = False) -> bool:
        """Replace the oldest turns with their summary, in the store and in what the
        model is sent. Only while nothing reads the history: with `idle`, a summary that
        finds a turn running (or one starting while the store is written) is kept in
        `_written` for that turn's end. False if it was not put in place."""
        if idle and not self._between_turns():
            self._written = written
            return False
        if not self._still_fits(written):
            return False
        rt = self._rt
        if not written.stored:
            # A change to what is saved, as `_save` is: a reload reading meanwhile is
            # dropped.
            self._saves += 1
            await rt.conversations.compact(
                self.id, OWNER, written.ids[-1], written.summary.text, rt.clock()
            )
            written = replace(written, stored=True)
        if not self._still_fits(written):
            # A reload read the store meanwhile, and has it or will.
            return False
        if idle and not self._between_turns():
            # A turn started while the store was written: it is sent what it was given.
            self._written = written
            return False
        n = len(written.ids)
        self._summary = written.summary.text
        del self._history[:n], self._kinds[:n], self._ids[:n]
        await self._send("notice", code="compacted")
        return True

    async def _build_input(
        self,
        kind: Kind,
        text: str,
        *,
        via: Literal["voice", "text", "ask"] | None = None,
        marker: str | None = None,
        turn: int | None = None,
    ) -> BuiltInput:
        """The message a user input is saved as, built but not saved. A draft (VOI-12)
        uses this, so a used draft saves exactly what it was given. `via` is how it was
        said, when that is not yet the session's. `marker` is a host note saved ahead
        of the text, saying what the text is. `turn` is the user message this input
        belongs to, when it is not yet counted (an early draft)."""
        via = via or self._via
        open_actions = await self._open_actions()
        pending = [PendingNote(pending_id=a.id, summary=a.summary) for a in open_actions]
        spoken = kind == "user" and via == "voice"
        # The user's own clock, so "at four" means four where they are (AGT-9).
        zone = self._rt.zone()
        recalled, brought = self._remembered(text) if kind == "user" else ("", frozenset())
        await self._read_place()
        now = self._rt.clock()
        jobs = [
            JobNote(tool=j.tool, job_id=j.id, seconds=max(0, (now - j.started_ms) // 1000))
            for j in self._rt.jobs.running(self.id)
        ]
        context = volatile_context(
            datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone(),
            pending,
            recalled,
            # Said aloud: she is told it is a transcript, and that she will be heard (#40).
            spoken=self._rt.identity if spoken else None,
            zone=zone or "",
            board=board_summary(await self._board()),
            routine=self._routine,
            jobs=jobs,
            turn=self._turn if turn is None else turn,
            # "Your last line" is asked about the user's own words, never a host note.
            who=self._rt.identity if kind == "user" else None,
            helper=helper_overlay(self._helper, self._rt.identity.agent_name)
            if self._helper is not None
            else "",
            helpers=self._helpers,
        )
        notes = (ContextBlock(text=ASKED),) if kind == "user" and via == "ask" else ()
        if marker is not None:
            notes = (*notes, ContextBlock(text=marker))
        folder_note = self._folder_note
        if folder_note is not None:
            notes = (*notes, ContextBlock(text=folder_note))
        # A soft cap passed, or a switch ended: told once, in her own words (PER-11).
        await self._spend_tick()
        spend_notes = self._rt.spend.notes(self._in_use())
        notes = (*notes, *(ContextBlock(text=note) for _, note in spend_notes))
        # What this conversation is about, when it is a helper's own: here only, once.
        helper_note = await self._rt.helper_note(self.id)
        if helper_note is not None:
            notes = (*notes, ContextBlock(text=helper_note))
        # A need that keeps coming back, on the user's own turn: offer a helper, once.
        # Never while they ask for one, or a helper's card of theirs waits here: their
        # request wins over a suggestion.
        drafting = any(a.tool == HELPER_CREATE for a in open_actions)
        offer = (
            await self._offer_due()
            if kind == "user" and via != "ask" and not drafting and not needs.asks_for_helper(text)
            else None
        )
        if offer is not None:
            notes = (*notes, ContextBlock(text=offer_note(offer)))
        blocks = (ContextBlock(text=context), *notes, TextBlock(text=text))
        return BuiltInput(
            msg=Msg(role="user", blocks=blocks),
            text=text,
            brought=brought,
            via=via,
            folder_note=folder_note,
            spend_notes=tuple(key for key, _ in spend_notes),
            helper_note=helper_note,
            offer=offer,
        )

    async def _commit_input(self, kind: Kind, built: BuiltInput) -> None:
        """Save a built input. History is only ever appended to."""
        self._brought.update(built.brought)
        if built.folder_note is not None and built.folder_note == self._folder_note:
            # Told once. A click since this was built leaves its own note for the next.
            self._folder_note = None
        # Whatever she says next answers a saved background note too: it needs no turn
        # of its own.
        self._unanswered = False
        if kind == "user" and built.via != "ask" and self._rt.web is not None:
            # Only what the user said themselves: never a tap's note, never a page, and
            # never an ask's words, which she wrote and a page could have chosen.
            self._rt.web.ledger.said(self.id, built.text)
        await self._save(kind, built.msg)
        if built.spend_notes:
            try:
                await self._rt.spend.said(built.spend_notes)
            except Exception as e:
                logger.warning("spend note not marked in session %s: %s", self.id, type(e).__name__)
        if kind == "user":
            # Only this turn may offer, and only for what its note said.
            self._offering = None
            if self._helper is not None:
                # Written in a helper's chat: a handoff that needed the user is answered.
                self._rt.helpers_changed()
            if built.offer is not None:
                await self._noted(built.offer)
        if built.helper_note is not None:
            try:
                await self._rt.helper_note_said(self.id)
            except Exception as e:
                logger.warning(
                    "helper note not marked in session %s: %s", self.id, type(e).__name__
                )
        if kind == "user" and self._title_source is None:
            await self._show_first_words()

    async def _show_first_words(self) -> None:
        """The store titled the conversation with the user's first words: the page
        shows them now, not only once she has named it (D4). Her name replaces them."""
        title, source = await self._rt.conversations.title_of(self.id, OWNER)
        if title is not None and source == "words":
            self._title_source = "words"
            await self._broadcast_title(title)

    async def _save_input(self, kind: Kind, text: str, *, marker: str | None = None) -> None:
        """A user input, with the context it was said in. Both are saved together and
        replayed unchanged on every later turn: history is only ever appended to."""
        await self._commit_input(kind, await self._build_input(kind, text, marker=marker))

    def _remembered(self, said: str) -> tuple[str, frozenset[str]]:
        """What she has saved that bears on this message, as untrusted text (MEM-5),
        and the ids it used. Nothing is marked as brought until the message is saved.

        It is saved with the message like the rest of its context, so the history
        replays byte for byte. A correction or a forgetting therefore shows from the
        next message on, not in messages already sent (MEM-6b)."""
        rt = self._rt
        fresh = [m for m in rt.memory.recall(said) if m.id not in self._brought]
        text, used = present.alongside(fresh, rt.clock())
        return (wrap("memory_recall", text) if used else "", frozenset(m.id for m in used))

    async def _save_from_loop(self, msg: Msg) -> None:
        await self._save("assistant" if msg.role == "assistant" else "tool_results", msg)

    async def _open_actions(self) -> list[Any]:
        now = self._rt.clock()
        return [
            a
            for a in await self._rt.spine_store.list_by_status(Status.PROPOSED)
            if a.owner_id == OWNER and a.session_id == self.id and a.expires_at > now
        ]

    async def _send_card(self, pending_id: str) -> None:
        a = await self._rt.spine_store.get_pending(pending_id)
        if a is None or a.owner_id != OWNER:
            return
        reg = self._rt.registry
        label = labels.tool_label(a.tool, labels.connected(reg.definition(a.tool), reg.get(a.tool)))
        summary = a.summary
        # A new helper's colour passes over those its others have, as its tap does.
        taken = (
            {h.color for h in await self._rt.helpers.all(OWNER)}
            if a.tool == HELPER_CREATE and self._rt.helpers is not None
            else set()
        )
        if a.tool == ASK_SCHEDULE and a.args.get("routine") in ("day", "own"):
            # A routine, not a one-off: said so, and where it lands.
            label = ROUTINE_LABEL
            if a.args.get("routine") == "own":
                summary = await self._routine_lands(a.session_id, summary)
        await self._send(
            "confirm.request",
            pending_id=a.id,
            tool=a.tool,
            # A friendly name and the reason it needs the user, both from the host.
            tool_label=label,
            reason=labels.reason(a.risk, a.recoverable),
            summary=summary,
            expires_at=a.expires_at,
            # Spend and anything unrecoverable can only be approved here, by a tap (SPN-14).
            tap_only=a.risk is Risk.SPEND or not a.recoverable,
            # A new helper's picture and draft, from the approved arguments its tap makes
            # it from. The page pins it above the message box, one per conversation.
            **(
                {"look": helper_look(a.args, taken), "draft": self._card_draft(a.args)}
                if a.tool == HELPER_CREATE
                else {}
            ),
        )

    def _card_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        """A create card's draft, its suggested connections said with their titles and
        whether each is connected now."""
        draft = helper_draft(args)
        have = self._rt.connected_catalog()
        draft["connectors"] = [
            {"id": c, "title": CONNECTOR_TITLES[c], "connected": c in have}
            for c in draft["connectors"]
        ]
        return draft

    async def _routine_lands(self, session_id: str, summary: str) -> str:
        """A routine set in a helper's chat runs there: its card says so by its name."""
        store = self._rt.helpers
        if store is None or not summary.endswith(OWN_LANDS):
            return summary
        try:
            h = await store.by_conversation(OWNER, session_id)
        except Exception as e:
            logger.warning("card %s: its helper not read: %s", session_id, type(e).__name__)
            return summary
        if h is None:
            return summary
        return summary[: -len(OWN_LANDS)] + f", in {present.inert(h.name)}'s chat)"

    async def _state(self, orb: str, *, notice: bool = False) -> None:
        if orb != self._orb or notice != self._orb_notice:
            self._orb, self._orb_notice = orb, notice
            # A notice's speech says so: the page may send over it, and it gives way.
            await self._send("state", orb=orb, **({"notice": True} if notice else {}))
        if orb in ("idle", "error"):
            # At rest: anything held for this page may arrive now. What arrives at the
            # first rest after a Stop is shown, not spoken; after that, as usual.
            await self._offer()
            self._stopped = False

    # ── Notices (SUR-9, VOI-17) ──────────────────────────────────────────

    def facts(self) -> Facts:
        """What the door judges "free" by. Read from what is running, never a flag."""
        task = self._task
        spoken = self._spoken
        return Facts(
            turn_running=(task is not None and not task.done())
            or self._saying
            or self._taking > 0
            or not self._started,
            listening=self._listener is not None or self._overlap is not None,
            spoken_turn=spoken[0] if spoken is not None else None,
            spoken_ms=spoken[1] if spoken is not None else None,
            heard_turn=self._played,
            active_ms=self.active_ms,
            host=self.host,
        )

    @contextlib.asynccontextmanager
    async def _taking_input(self) -> AsyncIterator[None]:
        """Around taking in what the user did: nothing is delivered until it is the
        session's task or has come to nothing, and notice speech gives way to it."""
        # Counted before the first await, so a tick in any gap sees the page busy.
        self._taking += 1
        # Something new is taken in: a spoken build's promise to listen is spent.
        self._listen_after = False
        try:
            await self._hush()
            yield
        finally:
            self._taking -= 1
        if self._taking == 0 and self._task is None:
            # It came to nothing: what was held for it may arrive now.
            await self._offer()

    async def _hush(self) -> None:
        """Stop speaking notices, and wait until that has stopped. They stay saved."""
        task = self._notice_task
        if task is not None and task is self._task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _offer(self) -> None:
        # A background read that ended is brought in at the same pauses as a notice.
        self._follow_up_if_free()
        door = self._rt.door
        if door is None or self._closed:
            return
        try:
            await door.offer(self)
        except Exception as e:
            # The notices stay held; the next tick's flush tries again.
            logger.warning("notices not offered in session %s: %s", self.id, type(e).__name__)

    async def deliver(self, notices: Sequence[Delivery]) -> None:
        """The door hands over notices while this page is free. All or none: a page
        that is no longer free raises `NotFree` before anything happens, and the door
        holds them again. The ones with her words are saved as her messages in one
        statement, so they survive a reload; then each is sent, and spoken if her
        voice is on, best effort, if the door says to speak it. A missed or failed one
        is shown by its title, never spoken (PER-11); a quiet one, or one past its
        moment, is shown and not spoken. Speech is queued, never waited for."""
        rt = self._rt
        if self._closed or not free(self.facts(), rt.clock()):
            raise NotFree
        said = [n.text for n in notices if n.text and n.spoken]
        # Hers are saved as hers. A ready line is the app's, never hers: kept as its note,
        # so she never takes it for something she said, and a reload shows it with Open.
        # A heads-up keeps the app's note first, so a reload shows it with its Join,
        # detail and reason, then her line if she has one; with none, the note alone.
        rows: list[tuple[Kind, Msg]] = []
        for n in notices:
            if n.kind == "heads_up":
                rows.append(("tap", heads_up_msg(n)))
            if not n.text:
                continue
            if n.kind == "ready":
                rows.append(("tap", ready_msg(n.title, n.conversation_id, handoff=n.handoff)))
            else:
                rows.append(("assistant", Msg(role="assistant", blocks=(TextBlock(text=n.text),))))
        if rows:
            await self._save_all(rows)
        if any((n.kind in ("ask", "heads_up") and n.text) or n.handoff for n in notices):
            # A timed ask's answer can quote what it read, as can a helper's handed-back
            # line, and a heads-up is written from an invite or an email: whatever she
            # does next on the strength of it is something she read, not something the
            # user said (MEM-5).
            self._outside.update({self._turn, self._turn + 1})
        # Saved: from here nothing raises, or the door would hold them and they would be
        # saved twice. `_send` already logs a failed send and goes on.
        for n in notices:
            await self._send(
                "notice",
                code="schedule",
                id=n.id,
                kind=n.kind,
                title=n.title,
                text=n.text,
                due_ms=n.due_ms,
                due=n.due,
                late=n.late,
                conversation_id=n.conversation_id,
                link=n.link,
                spoken=n.spoken,
                quiet=n.quiet,
                # The page's only: never spoken, never saved as her message.
                detail=n.detail,
                # Which helper it came from, named as the panel names it.
                helper_id=n.helper_id,
                helper_name=n.helper_name,
                helper_icon=n.helper_icon,
                helper_color=n.helper_color,
            )
        if said and rt.tts is not None and rt.speech_used is not None and not self._muted:
            if self._task is None and not self._closed and not self._stopped:
                self._task = self._notice_task = asyncio.create_task(self._say_notices(said))

    async def _save_all(self, rows: Sequence[tuple[Kind, Msg]]) -> None:
        """Like `_save`, for several at once: in the history before the write, so a turn
        that starts meanwhile has them, and taken out again if the write fails. Only
        these: a message saved meanwhile stays, wherever they now are."""
        self._saves += 1
        self._tail = None
        kinds = [k for k, _ in rows]
        msgs = [m for _, m in rows]
        self._history.extend(msgs)
        self._kinds.extend(kinds)
        try:
            ids = await self._rt.conversations.append_many(
                self.id, OWNER, kinds, msgs, self._rt.clock()
            )
        except BaseException:
            mine = {id(m) for m in msgs}
            kept = [i for i, m in enumerate(self._history) if id(m) not in mine]
            self._history[:] = [self._history[i] for i in kept]
            self._kinds[:] = [self._kinds[i] for i in kept]
            raise
        self._ids.extend(ids)

    @_calls_as("conversation")
    async def _say_notices(self, said: list[str]) -> None:
        """Her words for the notices, through the normal speech path. Runs as the
        session's task, so nothing else starts over it, and "stop" stops it."""
        rt = self._rt
        if rt.tts is None or rt.speech_used is None:  # pragma: no cover - checked by deliver
            self._task = None
            return
        turn = rt.out_of_turn()
        # Not a spoken turn: when she has said it, the microphone stays closed.
        self._via = "text"
        self._listen_after = False
        speaker = Speaker(
            rt.tts,
            rt.speech_used.voice,
            turn,
            self._send,
            self._send_bytes,
            on_capped=self._voice_capped,
        )
        try:
            await self._state("speaking", notice=True)
            for text in said:
                await speaker.feed(text)
                await speaker.pause()
            await speaker.finish()
            self._spoken = (turn, rt.clock())
        except asyncio.CancelledError:
            await speaker.stop()
            self._spoken = None
        except Exception as e:
            # Shown already and saved: only the speaking failed.
            logger.warning("notice speech failed in session %s: %s", self.id, type(e).__name__)
        finally:
            if self._task is asyncio.current_task():
                self._task = None
            if self._notice_task is asyncio.current_task():
                self._notice_task = None
        await self._state(self._rest)

    async def _error(self, code: str, **extra: Any) -> None:
        await self._send("error", code=code, **extra)

    # ── At the spend caps (BRN-7, BRN-8) ─────────────────────────────────

    async def _spend_tick(self) -> None:
        try:
            await self._rt.spend_tick()
        except Exception as e:
            # The switch stays as it was, and is looked at again next turn.
            logger.warning("spend switch not checked in session %s: %s", self.id, type(e).__name__)

    def _held(self, provider: object) -> bool:
        """A leg at its hard cap that this conversation was already told of: it is
        not tried again until the card is answered or the cap lifts."""
        pid = getattr(provider, "provider_id", None)
        rt = self._rt
        return (
            pid is not None
            and rt.gate.state(pid) == "capped"
            and rt.spend.standing(self.id, pid) is not None
        )

    def _voice_held(self) -> bool:
        return self._held(self._rt.tts)

    def _hearing_held(self) -> bool:
        return self._held(self._rt.stt)

    def _capped_line(self, leg: spend.Leg, provider_id: str) -> str:
        caps = self._rt.gate.caps(provider_id)
        cap = spend.cap_words(float(caps.hard or 0.0), caps.unit)
        return capped(leg, spend.friendly(provider_id), cap)

    async def _spend_options(self, leg: spend.Leg, provider_id: str) -> list[dict[str, Any]]:
        """What the card at this cap offers: a raise, what can be switched to until the
        1st, and putting it off."""
        rt = self._rt
        desk = rt.spend
        out = [desk.raise_option(provider_id)]
        if leg == "model":
            for pid, settings in await rt.model_choices(provider_id):
                out.append(
                    desk.switch_option(
                        f"{pid}/{settings.model}", settings.model, settings.to_json(), pid
                    )
                )
        elif leg == "voice":
            out.append(desk.switch_option("system", "the computer's voice"))
        else:
            choice = await rt.whisper_ready()
            if choice is not None:
                out.append(desk.switch_option("whisper", "local Whisper", {"choice": choice}))
        out.append(desk.stop_option(leg))
        return out

    async def _spend_capped(self, leg: spend.Leg, provider_id: str, line: str) -> None:
        """The app's one line in the banner, then the card (BRN-8). Her voice capped,
        the line is not hers to say: the page may say it in the computer's voice."""
        rt = self._rt
        caps = rt.gate.caps(provider_id)
        await self._error(
            "capped",
            leg=leg,
            provider_id=provider_id,
            cap=caps.hard,
            unit=caps.unit,
            sentence=line,
            # Muted, nothing is said aloud, in any voice.
            **({"say": "system"} if leg == "voice" and not self._muted else {}),
        )
        try:
            card = await rt.spend.card(
                self.id, leg, provider_id, line, await self._spend_options(leg, provider_id)
            )
        except Exception as e:
            # The line was shown; the card comes with the next refusal.
            logger.warning("spend card not made in session %s: %s", self.id, type(e).__name__)
            return
        await self._send("spend.card", **card.to_page())

    async def _fresh_card(self, card: spend.Card) -> spend.Card:
        """A shown card with its choices as they are now: a model capped since it was
        made is no longer offered, and the raise steps from the cap as it is now."""
        rt = self._rt
        options = await self._spend_options(card.leg, card.provider_id)
        return await rt.spend.card(self.id, card.leg, card.provider_id, card.line, options)

    async def _on_spend_choice(self, msg: Mapping[str, Any]) -> None:
        """A tap on a spend card (BRN-8). Like a tap on an approval card (SPN-14), it is
        taken only as this page's own act: the session it came on and "tap" are the
        server's to say, never the message's."""
        card_id, choice, amount = msg.get("id"), msg.get("choice"), msg.get("amount")
        if (
            not isinstance(card_id, str)
            or not card_id
            or not isinstance(choice, str)
            or not choice
            or (
                amount is not None
                and (isinstance(amount, bool) or not isinstance(amount, int | float))
            )
        ):
            await self._error("bad_message")
            return
        self.active_ms = self._rt.clock()
        async with self._taking_input():
            await self._spend_chosen(card_id, choice, amount)

    async def _spend_chosen(self, card_id: str, choice: str, amount: float | None) -> None:
        rt = self._rt
        found = next((c for c in rt.spend.cards_for(self.id) if c.id == card_id), None)
        options = None
        if found is not None:
            # Judged against what the card would offer now, not when it was shown.
            options = await self._spend_options(found.leg, found.provider_id)
        try:
            await rt.spend.choose(card_id, choice, amount, self.id, "tap", options)
        except spend.Refused as e:
            await self._send("spend.refused", id=card_id, code=e.code)
        except Exception as e:
            # The page waits on an answer: it hears this one failed, and may try again.
            logger.warning("spend choice failed in session %s: %s", self.id, type(e).__name__)
            await self._send("spend.refused", id=card_id, code="failed")

    async def _voice_capped(self, e: VoiceCapped) -> None:
        await self._spend_capped("voice", e.provider_id, self._capped_line("voice", e.provider_id))

    def _in_use(self) -> list[str | None]:
        rt = self._rt
        return [getattr(p, "provider_id", None) for p in (rt.provider, rt.tts, rt.stt)]

    async def _spend_warn(self) -> None:
        try:
            await self._rt.spend.warn(self._in_use())
        except Exception as e:
            logger.warning("spend warning not checked in session %s: %s", self.id, type(e).__name__)

    async def _send_bytes(self, data: bytes) -> None:
        if self._closed or self._raw_send_bytes is None:
            return
        if self._trace is not None:
            self._trace.mark("first_audio", self._now())
        if self._overlap is not None:
            # Her audio is on its way: a watch goes on.
            self._overlap.her_at = self._now()
        try:
            await self._raw_send_bytes(data)
        except Exception as e:
            logger.debug("send of audio failed: %s", type(e).__name__)

    async def send(self, kind: str, /, **body: Any) -> None:
        """A message to this page from outside its turns. Never raises."""
        await self._send(kind, **body)

    # ── The folder browser ───────────────────────────────────────────────

    async def show_folders(self, view: View) -> None:
        """The browser moved: keep where it is and draw the panel (`folders` frame)."""
        self.folder_place = view.place
        await self._send("folders", **view.frame)

    async def browsed(self, target: str) -> View:
        """The user moved the browser in the panel (`POST /api/folders/browse`): their
        own act, so no turn and no D7 check, inside the same fence. `target` is a path,
        `places`, a name the panel shows, or `up`. Her next input says where it is now,
        so "share this one" means what the user sees. Never raises."""
        view = await open_place(self.folder_place, target, self._rt.shares, self._rt.folder_look)
        if view.problem is None:
            await self.show_folders(view)
            self._folder_note = host_note(view)
        return view

    async def _send(self, kind: str, /, **body: Any) -> None:
        """Never raises: a closed socket must not break a turn that is still recording."""
        if self._closed:
            return
        if kind == "reply.sentence" and self._trace is not None:
            self._trace.mark("first_sentence", self._now())
        try:
            await self._raw_send({"type": kind, **body})
        except Exception as e:
            logger.debug("send of %s failed: %s", kind, type(e).__name__)
