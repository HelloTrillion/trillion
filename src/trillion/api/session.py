"""One authenticated conversation over the realtime protocol (PRD §8.3).

A session owns a conversation and the turn counter the confirmation gate
depends on. Only a committed user input advances that counter: a typed
message, or a tap on a confirmation card (VOI-15a). Conversation text is
never logged here; logs carry ids, types and statuses only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from trillion import __version__
from trillion.api import listening
from trillion.api.draft import Draft, same_words
from trillion.api.listening import FOLLOW_UP_S, Judge, Listener
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
from trillion.brain.types import (
    ContextBlock,
    ModelProvider,
    Msg,
    ProviderError,
    TextBlock,
    Usage,
)
from trillion.delivery.door import Delivery, Facts, NotFree, free
from trillion.memory import present
from trillion.persona import day
from trillion.persona.prompt import (
    PendingNote,
    compaction,
    degraded,
    greeting,
    volatile_context,
)
from trillion.spine import labels
from trillion.spine.confirm import Status
from trillion.spine.contract import Risk, ToolContext
from trillion.spine.untrusted import is_outside, wrap
from trillion.storage.sqlite.conversation_store import USER_INPUTS, ConversationKind, Kind
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
# How the page shows the saved recipe: a small line, never the user's words.
DAY_BUILD_SHOWN = "Building your day"
__all__ = ["OUT_OF_TURN", "PROTOCOL", "Session"]
Send = Callable[[dict[str, Any]], Awaitable[None]]


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


@dataclass(frozen=True, kw_only=True)
class BuiltInput:
    """A user input made ready to save: the message, and what committing it records."""

    msg: Msg
    text: str
    brought: frozenset[str]
    via: Literal["voice", "text", "ask"] = "text"


class Session:
    def __init__(
        self, rt: Runtime, send: Send, conversation_id: str, send_bytes: SendBytes | None = None
    ) -> None:
        self._rt = rt
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
        # The messages the model is sent, after the latest summary, each with its kind
        # and its id in the store. The whole conversation stays in the store.
        self._history: list[Msg] = []
        self._kinds: list[Kind] = []
        self._ids: list[int] = []
        self._summary: str | None = None
        # The turns in which she read something from outside: a page, a file, or a
        # timed ask's answer, which may quote an email (MEM-5).
        self._outside: set[int] = set()
        self._turn = 0
        self._task: asyncio.Task[None] | None = None
        # Notes about taps that arrived mid-turn, delivered to the model afterwards.
        self._queued: list[str] = []
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

    # ── Lifecycle ────────────────────────────────────────────────────────

    @classmethod
    async def start(
        cls, rt: Runtime, send: Send, resume: str | None, send_bytes: SendBytes | None = None
    ) -> Session:
        resumed = bool(resume) and await rt.conversations.exists(str(resume), OWNER)
        if resumed:
            # One conversation, one live session. Two would each keep their own copy of
            # the history and interleave what they save. The older one lets its turn
            # finish recording, and only then is the history read.
            other = rt.by_conversation.get(str(resume))
            if other is not None:
                await other.replaced()
        session = cls(rt, send, str(resume) if resumed else uuid.uuid4().hex, send_bytes)
        shown: list[dict[str, str]] = []
        if resumed:
            # The page and the turn count get every message; the model gets the latest
            # summary and what came after it (BRN-5).
            for kind, msg in await rt.conversations.history(session.id, OWNER):
                session._turn += kind in USER_INPUTS
                text = "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))
                if kind == "tap" and day_build(msg):
                    # The host's line, never shown as the user's words or the recipe's.
                    shown.append({"role": "host", "text": DAY_BUILD_SHOWN})
                elif kind in ("user", "assistant") and text:
                    shown.append({"role": msg.role, "text": text})
            summary, rows = await rt.conversations.context(session.id, OWNER)
            session._summary = summary
            found = await rt.conversations.kind_of(session.id, OWNER)
            session.kind, session.day = found if found is not None else ("chat", None)
            if rt.web is not None:
                # The addresses the user gave earlier in this conversation stay openable.
                for kind, msg in await rt.conversations.history(session.id, OWNER):
                    if kind == "user" and not asked(msg):
                        for b in msg.blocks:
                            if isinstance(b, TextBlock):
                                rt.web.ledger.said(session.id, b.text)
            for message_id, kind, msg in rows:
                session._ids.append(message_id)
                session._kinds.append(kind)
                session._history.append(msg)
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
        try:
            await session._send("history", messages=shown)
            # The conversation's board as it was left, open or closed (D3).
            board = for_page(await session._board(), rt.zone(), rt.refreshing(session.id))
            await session._send("board", board=board)
            for a in await session._open_actions():
                await session._send_card(a.id)
            session._started = True
            await session._state("idle")
        except BaseException:
            # Never left half started: the door would take it for the live page and
            # hold every notice for it, with no system notification instead.
            await session.close()
            raise
        return session

    def _model(self) -> dict[str, str] | None:
        p = self._rt.provider
        return {"provider": p.name, "model": p.model} if p is not None else None

    def _speech(self) -> dict[str, Any] | None:
        rt = self._rt
        s = rt.speech
        return describe(rt.tts, s.kind if s else None, s.voice if s else None)

    def _listening(self) -> dict[str, Any] | None:
        rt = self._rt
        s = rt.listening
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
        await self._send("error", code="deleted")
        await self.close()

    async def replaced(self) -> None:
        """This conversation was opened somewhere else. Say so, then stop for good."""
        await self._send("error", code="replaced")
        await self.close()

    def busy(self) -> bool:
        """Her turn, or her saying something, is running here: a refresh waits."""
        return self._task is not None and not self._task.done()

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
        self._tail = None
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
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
        elif kind == "greet":
            await self._on_greet()
        elif kind == "day.build":
            await self._on_day_build(spoken=msg.get("spoken") is True)
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
            # She has finished being heard, which is what reopens the microphone (VOI-8,
            # VOI-15b): only after a spoken turn, and never over a turn still running.
            elif self._answered_aloud() and self._task is None and self._listener is None:
                await self._on_listen(FOLLOW_UP_S, follow_up=True)
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
            await self._save_input("user", text)
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
            self._greeted = True
            await self._stop_listening()
            self._via = "text"
            self._arrived = self._starting("greet")
            self._queued.append(greeting(rt.identity))
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
            await self._stop_listening()
            # Never an approval: the recipe is the app's words, not the user's.
            self._via = "ask"
            self.active_ms = now
            self._arrived = self._starting("tap")
            text = day.recipe(rt.day_setup(), date, zone or "", rt.identity)
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
            or rt.listening is None
            or not rt.stt.turns
            or rt.provider is None
            or self._listener is not None
            or not (running or self._tail is not None)
        ):
            await self._barge_instead()
            return
        tail = self._tail[0] if self._tail is not None else None
        # Nothing heard is shown until it turns out to be the user's turn, and no
        # draft starts from it: her reply must stay as it is until then.
        listener = Listener(rt.stt, rt.listening.choice, on_partial=self._unshown)
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
        logger.info("talked over in session %s: deciding", self.id)
        await self._send("overlap.duck")
        ov.wake.set()

    async def _leave_watch(self, ov: Overlap, *, patience_s: float = NOTHING_AFTER_S) -> None:
        """Stop only watching: what follows is heard as the user. Only their words count:
        her echo, before, between or after them, is left out."""
        if not ov.watching:
            return
        ov.watching = False
        ov.listener.hear(
            patience_s=patience_s, judge=self._judge_on(ov.listener, ov.timeline, ov.past_s)
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
            or rt.listening is None
            or not rt.stt.turns
            or not rt.stt.word_times
            or rt.provider is None
            or self._listener is not None
            # With her voice off there is nothing to talk over, and nothing to tell apart.
            or self._muted
            or rt.tts is None
            # Her echo is told apart by her own audio: a voice the page makes has none.
            or rt.tts.where != "core"
            or rt.speech is None
            or not (running or self._tail is not None or unheard)
        ):
            await self._send("overlap.over", end=True)
            return
        tail = self._tail[0] if self._tail is not None else None
        wake = asyncio.Event()

        async def heard(_: str) -> None:
            wake.set()  # judged at once, not at the next tick

        # Nothing heard is shown, and no draft starts from it.
        listener = Listener(rt.stt, rt.listening.choice, on_partial=heard, watch=True)
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
            logger.info("talked over in session %s: follow_up", self.id)
            await self._leave_watch(ov, patience_s=FOLLOW_UP_S)
            self._spoken = None
            await self._become_turn(ov, follow_up=True)

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

    async def _become_turn(self, ov: Overlap, *, follow_up: bool) -> None:
        """The listener that heard them over her goes on as the user's turn."""
        self._overlap = None
        ov.watcher = None
        listener = ov.listener
        listener.promote(on_partial=self._partial, on_turn=self._turn_signal)
        self._listener = listener
        self._drafted = False
        self._last_audio = self._now()
        self._warm()
        # The page's microphone is already open; this tells it the words are a turn.
        await self._send("mic.start", follow_up=follow_up)
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
        self, patience_s: float, *, follow_up: bool = False, held: bool = False
    ) -> None:
        async with self._taking_input():
            await self._listen(patience_s, follow_up=follow_up, held=held)

    async def _listen(self, patience_s: float, *, follow_up: bool, held: bool = False) -> None:
        rt = self._rt
        if self._listener is not None or self._overlap is not None:
            return
        if rt.stt is None or rt.listening is None:
            if not follow_up:
                await self._error("no_listening")
        elif rt.provider is None:
            await self._error("no_model")
        elif self._task is not None:
            await self._error("busy")
        else:
            self._spoken = None
            listener = Listener(
                rt.stt,
                rt.listening.choice,
                on_partial=self._partial,
                patience_s=patience_s,
                on_turn=self._turn_signal,
            )
            # Set before it starts: what was held while it connected is judged the same way.
            listener.key_held = held
            if follow_up and self._timeline is not None and self._timeline.sentences:
                # No watch placed her on this clock: her last sentence is taken to end
                # as it opens, so her echo still in the air is hers.
                listener.judge_by(self._judge_on(listener, self._timeline.ending_at(0.0)))
            self._listener = listener
            self._drafted = False
            self._last_audio = asyncio.get_running_loop().time()
            self._warm()
            await self._state("arming")
            # The page opens the microphone now; what it sends while the recognizer is
            # still connecting is held, so the first word is not lost.
            await self._send("mic.start", follow_up=follow_up)
            try:
                await listener.start()
            except VoiceError as e:
                await self._listening_failed(e.kind)
                return
            if self._listener is listener:
                self._watchdog = asyncio.create_task(self._watch(listener))
                await self._state("listening")
            elif self._closed:
                # Closed while it connected: nobody will close it otherwise.
                await listener.abandon()

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
            if self._listener is listener and loop.time() - self._last_audio > NO_AUDIO_S:
                self._watchdog = None
                await self._conclude("inaudible")
                return

    async def _partial(self, text: str) -> None:
        await self._send("transcript.partial", text=text)

    async def _stop_listening(self) -> Listener | None:
        await self._end_overlap(tell=True)
        listener, self._listener = self._listener, None
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

    async def _hearing_down(self, kind: str) -> None:
        """She cannot hear: say so in one sentence, aloud if her voice works (VOI-14)."""
        said = degraded("hearing", kind)
        await self._error("listening", kind=kind, sentence=said)
        rt = self._rt
        if rt.tts is None or rt.speech is None or self._muted:
            return
        # Not a spoken turn any more: the end of this sentence must not reopen the
        # microphone that just failed, or one fault would repeat itself forever.
        self._via = "text"
        heard_as = rt.out_of_turn()
        speaker = Speaker(rt.tts, rt.speech.voice, heard_as, self._send, self._send_bytes)
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
            if text:
                trace.mark("heard", self._now())
                self._arrived = trace
                usable = draft is not None and self._usable(draft, text)
                # A used draft saves its own words: the screen shows those, so a reload
                # shows the same.
                shown = draft.words if usable and draft is not None else text
                await self._send("transcript.final", text=shown)
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
                    await self._save_input("user", text)
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
        # A tap is the user's own act on one card, so it is allowed at once: it is judged
        # as the turn after the newest thing said. It does not advance the turn the model
        # acts in. If it did, tapping one card mid-turn would let the model confirm
        # another in the very turn it proposed it (SPN-2e).
        response = await self._rt.router.resolve(
            pending_id,
            decision,
            self._ctx(f"tap-{uuid.uuid4().hex}", via="tap", turn=self._turn + 1),
        )
        await self._send(
            "confirm.resolved",
            pending_id=pending_id,
            status=response.status,
            reason=response.reason.value if response.reason is not None else None,
            detail=response.detail or None,
        )
        action = "Approve" if decision == "confirm" else "Decline"
        note = f"[host] The user tapped {action} on the card for {pending_id}. Result: "
        self._queued.append(note + response.to_model())
        if self._task is None:
            self._begin()

    # ── A turn ───────────────────────────────────────────────────────────

    def _begin(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        """Turns, one after another, until nothing is waiting.

        `_task` stays set for the whole of it, so a second turn can never start beside
        this one: a message that arrives meanwhile is refused as busy, and a tap's note
        is picked up by the loop."""
        try:
            while not self._closed:
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
                    # A draft was only started on a history that needed no summary.
                    await self._compact_if_long()
                await self._one_turn()
                if not self._queued:
                    break
        finally:
            # Only its own: never a task that started after this one was let go.
            if self._task is asyncio.current_task():
                self._task = None
                if self._building_day:
                    # Before anything awaits, so no input of the user's has begun: their
                    # next answer to a card is theirs again, never taken for an ask.
                    self._building_day = False
                    self._via = "text"
            if self._using is not None:
                # Never reached its turn.
                await self._unuse()
        # A watch over her reply ends now unless something of hers is still to be heard.
        await self._watch_settles()
        await self._state(self._rest)

    async def _one_turn(self) -> None:
        rt = self._rt
        # The turn she acts in is fixed when it starts. Nothing that happens while she
        # works, a tap least of all, can move her into a later one.
        turn = self._turn
        provider = rt.provider
        if provider is None:  # pragma: no cover - callers check before starting a turn
            return
        speaking = False
        speaker: Speaker | None = None
        timeline: Timeline | None = None
        heard_as = turn
        if rt.tts is not None and rt.speech is not None and not self._muted:
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
                rt.tts, rt.speech.voice, heard_as, self._send, self._send_bytes, on_audio
            )
        self._speaker = speaker
        self._tail = None
        # Only a spoken reply to a spoken turn is cut to what was heard: a typed one was
        # read, and so was one with her voice off (D8).
        listened = speaker is not None and self._via == "voice"
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
                if is_outside(ev.name, rt.registry.definition(ev.name)) and r.status == "succeeded":
                    # From here to the end of the turn, what she writes to memory is
                    # something she read, not something the user said.
                    self._outside.add(turn)
                if r.status == "succeeded":
                    self._remember_read(turn, ev.name, ev.args)
                await self._send(
                    "tool.end",
                    call_id=ev.call_id,
                    ok=r.status in ("succeeded", "confirmation_required"),
                    summary=r.reason.value if r.reason is not None else r.status,
                )
                if r.status == "confirmation_required" and r.pending_id:
                    await self._send_card(r.pending_id)

        final = "idle"
        # The draft this turn replays as its first call, if it has one (VOI-12).
        using, self._using = self._using, None
        try:
            await self._state("processing")
            # Rules and the skills that are on, read once for this turn, off the event loop.
            system = await asyncio.to_thread(rt.system)
            if using is not None and list(using.system) != system:
                # A rule or skill changed while the draft ran: it sent an old prompt.
                trace.draft = "dropped"
                await self._let_go(using)
                using = None
            done = await run_turn(
                provider=provider,
                router=rt.router,
                registry=rt.registry,
                system=system,
                history=compact.with_summary(self._summary, self._history),
                ctx=lambda call_id: self._ctx(call_id, turn=turn),
                emit=emit,
                save=save,
                first=using.replay() if using is not None else None,
                heard=heard if listened else None,
            )
            written = True
            # The model failed: her one sentence for it, spoken if her voice still works
            # and shown either way (VOI-14). Authored, since there is nobody to compose it.
            down = (
                degraded("model", done.error_kind or "other")
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
            # Recorded before her last words are voiced: the model's work is done and
            # paid for even if she is talked over while they play.
            await asyncio.shield(
                rt.conversations.record_usage(
                    owner_id=OWNER,
                    conversation_id=self.id,
                    provider=provider.name,
                    model=provider.model,
                    usage=done.usage,
                    cost_usd=done.cost_usd,
                    now_ms=rt.clock(),
                )
            )
            if speaker is not None:
                if down is not None:
                    await speaker.pause()
                    await speaker.feed(down)
                await speaker.finish()
                self._spoken = (heard_as, rt.clock())
            if timeline is not None:
                timeline.finish()
            if down is not None:
                final = "error"
                await self._error("provider", kind=done.error_kind, sentence=down)
            elif done.stop != "end":
                await self._send("notice", code="stopped", reason=done.stop)
            # Kept before the page hears the turn is over, so the next one never waits on it.
            await self._keep_trace(trace, done.stop, voiced=speaker is not None)
            u = done.usage
            await self._send(
                "turn.complete",
                stop=done.stop,
                cost_usd=done.cost_usd,
                usage={
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_read_tokens": u.cache_read_tokens,
                    "cache_write_tokens": u.cache_write_tokens,
                },
            )
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

    async def _name(self, said: Msg, reply: Msg) -> None:
        rt = self._rt
        provider = rt.provider
        if provider is None:
            return
        named, usage = await naming.name(provider, [said, reply])
        try:
            # Recorded even if the session closes meanwhile: the call was paid for.
            await asyncio.shield(self._record_usage(provider, usage))
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
        built = await self._build_input("user", words, via="voice")
        # The same as `_one_turn` sends: its rules and skills, its history, its tools.
        system = await asyncio.to_thread(rt.system)
        # Read after the awaits, so a trim meanwhile is in what is sent.
        history = [*self._history, built.msg]
        # A history the turn would summarize first is not the history it would send.
        if compact.estimate(history) > rt.compact_at:
            return
        # The model is about to be sent her last reply: it can no longer be trimmed.
        self._tail = None
        self._draft = Draft.start(
            provider=provider,
            system=system,
            messages=compact.with_summary(self._summary, history),
            tools=tool_schemas(rt.registry),
            words=words,
            built=built,
            history_len=len(self._history),
            summary=self._summary,
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
        """Stop a draft nobody will replay, and count what it cost. A provider reports
        usage at the end of a call, so one stopped before then counts as nothing."""
        await draft.cancel()
        # The model it asked, which may no longer be the one in use.
        if draft.provider is not None:
            await self._record_usage(draft.provider, draft.usage())

    async def _record_usage(self, provider: ModelProvider, usage: Usage) -> None:
        """Count a side call's cost against this conversation, unless it used nothing."""
        if usage == Usage():
            return
        pricing = provider.pricing()
        await self._rt.conversations.record_usage(
            owner_id=OWNER,
            conversation_id=self.id,
            provider=provider.name,
            model=provider.model,
            usage=usage,
            cost_usd=pricing.cost(usage) if pricing is not None else None,
            now_ms=self._rt.clock(),
        )

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
            voice=rt.speech.kind if voiced and rt.speech is not None else None,
            ears=rt.listening.kind if trace.via == "voice" and rt.listening is not None else None,
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

    def _ctx(
        self,
        call_id: str,
        via: Literal["voice", "text", "tap", "channel"] | None = None,
        *,
        turn: int | None = None,
    ) -> ToolContext:
        at = self._turn if turn is None else turn
        return ToolContext(
            owner_id=OWNER,
            session_id=self.id,
            turn=at,
            call_id=call_id,
            via=via or self._via,
            granted_roots=self._rt.granted_roots,
            read_outside=at in self._outside,
        )

    async def _save(self, kind: Kind, msg: Msg) -> None:
        # Something after her last reply: it is no longer the one to trim.
        self._tail = None
        self._history.append(msg)
        self._kinds.append(kind)
        message_id = await self._rt.conversations.append(
            self.id, OWNER, kind, msg, self._rt.clock()
        )
        self._ids.append(message_id)

    async def _compact_if_long(self) -> None:
        """Summarize the oldest turns once the history outgrows the model (BRN-5).

        Before a turn, never during one. If the summary cannot be written, the turn
        goes ahead on the full history: a long prompt is better than a lost one."""
        rt = self._rt
        provider = rt.provider
        if provider is None or compact.estimate(self._history) <= rt.compact_at:
            return
        cut = compact.cut_point(self._kinds, self._history, rt.compact_keep)
        if cut is None:
            return
        try:
            summary = await asyncio.wait_for(
                compact.summarize(
                    provider, compaction(rt.identity), self._summary, self._history[:cut]
                ),
                compact.TIMEOUT_S,
            )
        except (ProviderError, compact.CompactionFailed, TimeoutError) as e:
            logger.warning("compaction in session %s failed: %s", self.id, type(e).__name__)
            return
        await rt.conversations.compact(self.id, OWNER, self._ids[cut - 1], summary.text, rt.clock())
        await rt.conversations.record_usage(
            owner_id=OWNER,
            conversation_id=self.id,
            provider=provider.name,
            model=provider.model,
            usage=summary.usage,
            cost_usd=pricing.cost(summary.usage) if (pricing := provider.pricing()) else None,
            now_ms=rt.clock(),
        )
        self._summary = summary.text
        del self._history[:cut], self._kinds[:cut], self._ids[:cut]
        await self._send("notice", code="compacted")

    async def _build_input(
        self,
        kind: Kind,
        text: str,
        *,
        via: Literal["voice", "text", "ask"] | None = None,
        marker: str | None = None,
    ) -> BuiltInput:
        """The message a user input is saved as, built but not saved. A draft (VOI-12)
        uses this, so a used draft saves exactly what it was given. `via` is how it was
        said, when that is not yet the session's. `marker` is a host note saved ahead
        of the text, saying what the text is."""
        via = via or self._via
        pending = [
            PendingNote(pending_id=a.id, summary=a.summary) for a in await self._open_actions()
        ]
        spoken = kind == "user" and via == "voice"
        # The user's own clock, so "at four" means four where they are (AGT-9).
        zone = self._rt.zone()
        recalled, brought = self._remembered(text) if kind == "user" else ("", frozenset())
        context = volatile_context(
            datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone(),
            pending,
            recalled,
            # Said aloud: she is told it is a transcript, and that she will be heard (#40).
            spoken=self._rt.identity if spoken else None,
            zone=zone or "",
            board=board_summary(await self._board()),
        )
        notes = (ContextBlock(text=ASKED),) if kind == "user" and via == "ask" else ()
        if marker is not None:
            notes = (*notes, ContextBlock(text=marker))
        blocks = (ContextBlock(text=context), *notes, TextBlock(text=text))
        return BuiltInput(msg=Msg(role="user", blocks=blocks), text=text, brought=brought, via=via)

    async def _commit_input(self, kind: Kind, built: BuiltInput) -> None:
        """Save a built input. History is only ever appended to."""
        self._brought.update(built.brought)
        if kind == "user" and built.via != "ask" and self._rt.web is not None:
            # Only what the user said themselves: never a tap's note, never a page, and
            # never an ask's words, which she wrote and a page could have chosen.
            self._rt.web.ledger.said(self.id, built.text)
        await self._save(kind, built.msg)
        if kind == "user" and self._title_source is None:
            await self._show_first_words()

    async def _show_first_words(self) -> None:
        """The store titled the conversation with the user's first words: the page
        shows them now, not only once she has named it (D4). Her name replaces them."""
        title, source = await self._rt.conversations.title_of(self.id, OWNER)
        if title is not None and source == "words":
            self._title_source = "words"
            await self._broadcast_title(title)

    async def _save_input(self, kind: Kind, text: str) -> None:
        """A user input, with the context it was said in. Both are saved together and
        replayed unchanged on every later turn: history is only ever appended to."""
        await self._commit_input(kind, await self._build_input(kind, text))

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
        await self._send(
            "confirm.request",
            pending_id=a.id,
            tool=a.tool,
            # A friendly name and the reason it needs the user, both from the host.
            tool_label=labels.tool_label(
                a.tool, labels.connected(reg.definition(a.tool), reg.get(a.tool))
            ),
            reason=labels.reason(a.risk, a.recoverable),
            summary=a.summary,
            expires_at=a.expires_at,
            # Spend and anything unrecoverable can only be approved here, by a tap (SPN-14).
            tap_only=a.risk is Risk.SPEND or not a.recoverable,
        )

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
        voice is on, best effort. A missed or failed one is shown by its title, never
        spoken (PER-11). Speech is queued, never waited for."""
        rt = self._rt
        if self._closed or not free(self.facts(), rt.clock()):
            raise NotFree
        said = [n.text for n in notices if n.text]
        if said:
            await self._save_all(
                "assistant", [Msg(role="assistant", blocks=(TextBlock(text=t),)) for t in said]
            )
        if any(n.kind == "ask" and n.text for n in notices):
            # A timed ask's answer can quote what it read: whatever she does next on the
            # strength of it is something she read, not something the user said (MEM-5).
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
            )
        if said and rt.tts is not None and rt.speech is not None and not self._muted:
            if self._task is None and not self._closed and not self._stopped:
                self._task = self._notice_task = asyncio.create_task(self._say_notices(said))

    async def _save_all(self, kind: Kind, msgs: Sequence[Msg]) -> None:
        """Like `_save`, for several at once: in the history before the write, so a turn
        that starts meanwhile has them, and taken out again if the write fails. Only
        these: a message saved meanwhile stays, wherever they now are."""
        self._tail = None
        self._history.extend(msgs)
        self._kinds.extend([kind] * len(msgs))
        try:
            ids = await self._rt.conversations.append_many(
                self.id, OWNER, kind, msgs, self._rt.clock()
            )
        except BaseException:
            mine = {id(m) for m in msgs}
            kept = [i for i, m in enumerate(self._history) if id(m) not in mine]
            self._history[:] = [self._history[i] for i in kept]
            self._kinds[:] = [self._kinds[i] for i in kept]
            raise
        self._ids.extend(ids)

    async def _say_notices(self, said: list[str]) -> None:
        """Her words for the notices, through the normal speech path. Runs as the
        session's task, so nothing else starts over it, and "stop" stops it."""
        rt = self._rt
        if rt.tts is None or rt.speech is None:  # pragma: no cover - checked by deliver
            self._task = None
            return
        turn = rt.out_of_turn()
        # Not a spoken turn: when she has said it, the microphone stays closed.
        self._via = "text"
        self._listen_after = False
        speaker = Speaker(rt.tts, rt.speech.voice, turn, self._send, self._send_bytes)
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
