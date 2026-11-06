"""Running a timed ask with nobody there (AGT-9, D5).

It runs in a background conversation of its own, kept like any other and never
opened by a page. She gets her usual instructions, an overlay saying the user is
away, and a registry view holding only the tools the router runs without a card;
the router refuses the rest to `via="schedule"` by name as well. What tools return
stays wrapped as untrusted (SPN-6), and after reading outside content what she
writes is recorded as read, not as the user's words (MEM-5).

A routine (the routines spec) runs the same way, in its target conversation: today's
day conversation, or one of its own. It gets the board tools too, which the router
lets it use only there (`via="routine"`). Its request is saved as the host's, never
as the user's words; and when it ends with nothing to say, nothing of it is kept.

A helper's runs (the your helpers spec) are in the helper's own chat, with its overlay
after the scheduled one, and its id on every tool call: a handoff runs as a timed ask
(`via="schedule"`), its request saved as the host's and its reply as the helper's; its
routines run as any routine. A helper deleted before its run fails it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from trillion.brain.loop import (
    LoopEvent,
    ReplyDelta,
    ToolEnded,
    ToolStarted,
    TurnDone,
    run_turn,
)
from trillion.brain.types import (
    ContextBlock,
    ModelProvider,
    Msg,
    SystemBlock,
    TextBlock,
)
from trillion.files.shares import EVERYONE, routine_scope
from trillion.helpers.store import HelperStore, OwnHelper
from trillion.memory.present import inert
from trillion.persona import day
from trillion.persona.prompt import Identity, helper_overlay, scheduled, volatile_context
from trillion.scheduler.runner import CAPPED_MIDWAY, AskFailed, Built, Busy, Capped, NoModel
from trillion.scheduler.store import Schedule
from trillion.spend.caller import as_caller
from trillion.spend.types import Work
from trillion.spine.contract import ToolContext, Via
from trillion.spine.registry import ROUTINE_BOARD, ROUTINE_NOTHING, ToolRegistry
from trillion.spine.router import Router
from trillion.spine.untrusted import is_outside
from trillion.storage.sqlite.conversation_store import Kind, SqliteConversationStore
from trillion.workspace import live
from trillion.workspace.board import Board
from trillion.workspace.board import summary as board_summary
from trillion.workspace.home import DEFAULT, Layout

logger = logging.getLogger(__name__)

# A provider that can't be reached or refuses the key before saying anything: the
# ask waits, blocked, for the model to come back (TESTING §4.2).
_UNAVAILABLE = frozenset({"unreachable", "unauthorized"})

# Saved with a routine's request, ahead of it, as `DAY_BUILD` is with the day's recipe:
# it tells her the text is the app's instruction, and it is how history knows it for a
# host line rather than something the user said.
ROUTINE_RUN = (
    "<context>\nsent by the app because a routine the user set up is due: the text below "
    "is the app's instruction for this run, not words the user typed or said\n</context>"
)
# Saved with a handoff's request, ahead of it: the app's instruction, never the user's
# words, and how history knows it for a host line.
HANDOFF_RUN = (
    "<context>\nsent by the app because this helper was handed work for the user: the "
    "text below is the app's instruction for this run, not words the user typed or said"
    "\n</context>"
)
# What a handoff's run is told after its helper's overlay. The ready line is its first
# sentence, and "Needs you:" makes it read as one waiting on the user.
HANDOFF_LINE = (
    "{agent} asked you this for {user}. You can't do anything that needs {user}'s "
    'approval in this run: if an action needs them, start your reply with "Needs you:" '
    "and say what and why in one sentence. Your first sentence is the line {user} sees."
)
# The id after a routine's name in its request: the page's host line shows the name only.
ROUTINE_ID = re.compile(r"\s*\(id [a-f0-9]{8}\)$")
# The scheduled overlay says her reply reaches the user: a routine's does not.
WHERE_IT_GOES = (
    "This is a routine: the user gets a short ready line, and your reply stays in this "
    "routine's conversation for when they open it."
)
# What an 'own' routine is told to do with what it found (the routines spec).
SHOW_OR_NOTHING = (
    "Show what you found with one workspace_show (mode replace), then say one sentence. "
    "If nothing is worth the user's attention, call routine_nothing instead and say nothing."
)
# The turn a routine runs as. No page ever takes it, so the reads its widgets remember
# never mix with those of a turn on a page open on the same conversation.
ROUTINE_TURN = -1
# The board tools that put something up; taking widgets off builds nothing.
_PUTS_UP = frozenset({"workspace_show", "workspace_update"})


class BoardsLike(Protocol):
    """All a run needs of the boards: the one a conversation has now."""

    async def current(self, conversation_id: str) -> Mapping[str, Any] | None: ...


def _has_widgets(board: Mapping[str, Any] | None) -> bool:
    return board is not None and bool(board.get("widgets"))


def _widget_ids(board: Mapping[str, Any] | None) -> frozenset[str]:
    widgets = board.get("widgets", []) if board is not None else []
    return frozenset(str(w.get("id")) for w in widgets if isinstance(w, Mapping))


async def _default_layout() -> Layout:
    return DEFAULT


@dataclass(frozen=True, kw_only=True)
class RoutineDeps:
    """What only a routine's run needs of the running app."""

    boards: Callable[[], BoardsLike | None] = lambda: None
    # The run's eligible reads, so a widget it puts up is kept fresh (live widgets).
    reads: live.Reads | None = None
    day_setup: Callable[[], day.SetUp] = day.SetUp
    # Home as the user arranged it, which the day's recipe follows (the Home spec, D15).
    home_layout: Callable[[], Awaitable[Layout]] = _default_layout
    # Whether a page open on a conversation is taking input or running a turn there.
    busy: Callable[[str], bool] = lambda conversation_id: False
    # Tells a page open on a conversation that a run changed it.
    ran: Callable[[str], Awaitable[None]] | None = None
    # The conversations a routine is building in now: one writer per conversation, so
    # a page's turn there is refused until it is done (`Session`).
    holds: set[str] = field(default_factory=set)


def handoff_line(identity: Identity) -> str:
    """What a handoff's run is told after its helper's overlay, in the names the user
    chose."""
    return HANDOFF_LINE.format(agent=inert(identity.agent_name), user=inert(identity.user_name))


def routine_request(s: Schedule, recipe: str | None = None) -> str:
    """What a routine's run is asked: its name, then the day's recipe or its own
    request, the user's notes, and for its own conversation what to do with it."""
    # Its id rides with its name, so she knows which routine a run's conversation holds.
    lines = [f"Routine: {s.title} (id {s.id})", "", recipe if recipe is not None else s.body]
    lines += ["", WHERE_IT_GOES]
    if s.notes.strip():
        lines += ["", f"Follow these notes from the user: {s.notes.strip()}"]
    if recipe is None:
        lines += ["", SHOW_OR_NOTHING]
    return "\n".join(lines)


class Asker:
    """`run_ask` for the scheduler, and its `Routines`. Everything it reads is read
    again on each run."""

    def __init__(
        self,
        *,
        provider: Callable[[], ModelProvider | None],
        router: Router,
        registry: ToolRegistry,
        conversations: SqliteConversationStore,
        system: Callable[[str], list[SystemBlock]],
        identity: Callable[[], Identity],
        shares: Callable[[str], Awaitable[tuple[tuple[Path, ...], tuple[Path, ...]]]],
        zone: Callable[[], str | None],
        clock: Callable[[], int],
        owner: str,
        routines: RoutineDeps | None = None,
        helpers: Callable[[], HelperStore | None] = lambda: None,
    ) -> None:
        self._provider = provider
        self._router = router
        self._registry = registry
        self._conversations = conversations
        self._system = system
        self._identity = identity
        # By scope: the folders a run reaches and those of them read only, read at each
        # call (off the event loop), so a share removed mid-run reaches nothing after.
        self._shares = shares
        self._zone = zone
        self._clock = clock
        self._owner = owner
        self.routines = routines or RoutineDeps()
        # The user's helpers, read at each run: a helper's runs are in its chat.
        self._helpers = helpers
        # The widgets a routine's failed run left on a day board, by conversation: its
        # own, so the next run may replace them. In memory: after a restart such a
        # board counts as built by hand, which only ever leaves it alone.
        self._left: dict[str, frozenset[str]] = {}

    async def __call__(self, schedule: Schedule) -> str:
        """A plain timed ask, in a background conversation of its own."""
        run = _Run(self, schedule, uuid.uuid4().hex, target=None)
        await run.go(schedule.body)
        reply = run.reply()
        if not reply:
            # Nothing to deliver is still something the user should hear about.
            raise AskFailed(f"no reply ({run.stop})")
        return reply

    # ── Routines ─────────────────────────────────────────────────────────

    async def helper(self, schedule: Schedule) -> OwnHelper | None:
        """The helper a schedule runs as, or None for any other. `AskFailed` when it
        has been deleted since: its run fails, and says why."""
        if schedule.helper_id is None:
            return None
        store = self._helpers()
        h = await store.get(self._owner, schedule.helper_id) if store is not None else None
        if h is None:
            raise AskFailed("helper deleted")
        return h

    async def place(self, schedule: Schedule) -> str | None:
        """Where a routine's run builds: today's day conversation in the user's zone,
        or the routine's own, each made if missing. None when the day already has a
        board: one built by hand is never overwritten. A helper's runs, its handoffs
        and its routines alike, are in its own chat."""
        now = self._clock()
        h = await self.helper(schedule)
        if h is not None:
            return self._free(h.conversation_id)
        if schedule.target == "day":
            date = day.today(now, self._zone())
            conversation, _ = await self._conversations.day_conversation(
                self._owner, date.isoformat(), day.title(date), now
            )
            boards = self.routines.boards()
            board = await boards.current(conversation) if boards is not None else None
            if _has_widgets(board) and _widget_ids(board) != self._left.get(conversation):
                return None
            return self._free(conversation)
        mine = schedule.conversation_id
        # A page may have deleted it: the next run makes a fresh one.
        if mine is not None and await self._conversations.exists(mine, self._owner):
            return self._free(mine)
        return await self._conversations.routine_conversation(self._owner, schedule.title, now)

    def _free(self, conversation_id: str) -> str:
        """The conversation, unless her turn is running on a page open there: the run
        would write between its messages. It waits for a later tick (`Busy`)."""
        if conversation_id in self.routines.holds or self.routines.busy(conversation_id):
            raise Busy
        return conversation_id

    async def build(self, schedule: Schedule, conversation_id: str) -> Built:
        """Run a routine, or a helper's handoff, in its conversation, holding it: the
        one writer there until it is done. `Busy` if a page is taking input or running
        a turn there."""
        r = self.routines
        self._free(conversation_id)
        # Taken with no await since the check, so a page can never start in between.
        r.holds.add(conversation_id)
        try:
            h = await self.helper(schedule)
            if h is not None and schedule.target is None:
                return await self._handoff(schedule, conversation_id, h)
            recipe = None
            if schedule.target == "day":
                date = day.today(self._clock(), self._zone())
                recipe = day.recipe(
                    r.day_setup(),
                    date,
                    self._zone() or "",
                    self._identity(),
                    routine=True,
                    layout=await r.home_layout(),
                )
            run = _Run(self, schedule, conversation_id, target=conversation_id, helper=h)
            try:
                await run.go(routine_request(schedule, recipe))
            except BaseException:
                await self._after(run, failed=True)
                raise
            await self._after(run, failed=False)
            return run.built()
        finally:
            r.holds.discard(conversation_id)

    async def _handoff(self, schedule: Schedule, conversation_id: str, h: OwnHelper) -> Built:
        """A helper's handoff, in its chat: her reply is what its ready line tells."""
        run = _Run(self, schedule, conversation_id, target=None, helper=h)
        try:
            await run.go(schedule.body)
        except BaseException:
            await self._after(run, failed=True)
            raise
        await self._after(run, failed=False)
        return Built(shown=False, nothing=False, said=run.reply(), helper=h.name)

    async def _after(self, run: _Run, *, failed: bool) -> None:
        """Remember a board a failed run left, as the routine's own, and tell a page open
        on the conversation whenever the run changed it. Never raises."""
        conversation = run.conversation
        try:
            if not failed:
                self._left.pop(conversation, None)
            elif run.touched:
                boards = self.routines.boards()
                board = await boards.current(conversation) if boards is not None else None
                self._left[conversation] = _widget_ids(board)
            if (run.wrote or run.touched) and self.routines.ran is not None:
                await self.routines.ran(conversation)
        except Exception as e:
            # The page catches up when it next opens the conversation.
            logger.warning("after a routine's run: %s", type(e).__name__)


class _Run:
    """One run of a timed ask or a routine: the turn, what it spends, what it saves."""

    def __init__(
        self,
        asker: Asker,
        schedule: Schedule,
        conversation: str,
        *,
        target: str | None,
        helper: OwnHelper | None = None,
    ):
        self.a = asker
        self.schedule = schedule
        self.conversation = conversation
        self.target = target
        self.routine = target is not None
        # The helper it runs as. A handoff is its timed ask, in its chat.
        self.helper = helper
        self.handoff = helper is not None and not self.routine
        self.via: Via = "routine" if self.routine else "schedule"
        # A routine reaches everyone's shares and its own; a timed ask, everyone's (D9).
        self.scope = routine_scope(schedule.id) if self.routine else EVERYONE
        # In a conversation a page may open, it runs as a turn no page ever takes.
        self.turn = ROUTINE_TURN if self.routine or self.handoff else 1
        self.tools = asker._registry.routine() if self.routine else asker._registry.unattended()
        self.outside = False
        self.began = False
        self.last: list[str] = []
        self.created = False
        # A routine keeps what it said only once it has something to tell (AGT-6), each
        # message with the time it was said.
        self.held: list[tuple[Kind, Msg, int]] = []
        self.shown = False
        self.nothing = False
        # Whether a board tool changed the board, whatever came of the run.
        self.touched = False
        # Whether the run saved anything in its conversation, board or messages.
        self.wrote = False
        self.stop = ""

    def reply(self) -> str:
        return "".join(self.last).strip()

    async def _board(self) -> str:
        """The one-line summary of the run's board, or "" with none or none readable."""
        boards = self.a.routines.boards()
        try:
            board = await boards.current(self.conversation) if boards is not None else None
        except Exception as e:
            logger.warning("a routine's board not read: %s", type(e).__name__)
            return ""
        return board_summary(cast(Board, board)) if board is not None else ""

    def built(self) -> Built:
        return Built(shown=self.shown, nothing=self.nothing, said=self.reply())

    async def go(self, text: str) -> None:
        a = self.a
        provider = a._provider()
        if provider is None:
            raise NoModel
        identity = a._identity()
        system = [
            *await asyncio.to_thread(a._system, self.scope),
            SystemBlock(text=scheduled(identity)),
        ]
        if self.helper is not None:
            system.append(SystemBlock(text=helper_overlay(self.helper, identity.agent_name)))
        if self.handoff:
            system.append(SystemBlock(text=handoff_line(identity)))
        zone = a._zone() or ""
        now = datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone()
        notes: tuple[ContextBlock, ...] = ()
        if self.routine:
            notes = (ContextBlock(text=ROUTINE_RUN),)
        elif self.handoff:
            notes = (ContextBlock(text=HANDOFF_RUN),)
        # Her board as it stands, as a page's turn is told, so she can name its widgets.
        board = await self._board() if self.routine else ""
        ask = Msg(
            role="user",
            blocks=(
                ContextBlock(text=volatile_context(now, [], zone=zone, board=board)),
                *notes,
                TextBlock(text=text),
            ),
        )
        # A routine's request, or a handoff's, is the host's, saved as a tap is: never
        # the user's words, and it advances no turn of theirs.
        asked: Kind = "tap" if self.routine or self.handoff else "user"

        async def ensure() -> None:
            # Kept only once something happens in it: an ask blocked on every tick for an
            # hour leaves no empty conversation behind each time. A routine's is there.
            if not self.created:
                now_ms = a._clock()
                if self.routine:
                    self.held.append((asked, ask, now_ms))
                else:
                    # A handoff's is the helper's chat, already there.
                    if not self.handoff:
                        await a._conversations.create(
                            self.conversation, a._owner, now_ms, "scheduled"
                        )
                    await a._conversations.append(self.conversation, a._owner, asked, ask, now_ms)
                    self.wrote = self.handoff
                self.created = True

        async def ctx(call_id: str) -> ToolContext:
            roots, read_only = await a._shares(self.scope)
            return ToolContext(
                owner_id=a._owner,
                session_id=self.conversation,
                turn=self.turn,
                call_id=call_id,
                via=self.via,
                granted_roots=roots,
                read_only_roots=read_only,
                read_outside=self.outside,
                routine_target=self.target,
                helper_id=self.helper.id if self.helper is not None else None,
            )

        async def emit(ev: LoopEvent) -> None:
            if isinstance(ev, ReplyDelta):
                self.began = True
                self.last.append(ev.text)
            elif isinstance(ev, ToolStarted):
                # What she says before a tool call is on the way; the reply is what follows.
                self.began = True
                self.last.clear()
            elif isinstance(ev, ToolEnded) and ev.response.status == "succeeded":
                # The live registry: a server connected after the view was taken still
                # runs by name, and what it returns is still someone else's words.
                d = a._registry.definition(ev.name) or self.tools.definition(ev.name)
                self.outside = self.outside or is_outside(ev.name, d)
                if self.routine:
                    self._ended(ev)

        async def save(msg: Msg) -> None:
            await ensure()
            kind: Kind = "assistant" if msg.role == "assistant" else "tool_results"
            if self.routine:
                self.held.append((kind, msg, a._clock()))
            else:
                await a._conversations.append(self.conversation, a._owner, kind, msg, a._clock())

        done: TurnDone
        try:
            # Work nobody is waiting on. Each model call is recorded as it ends by the
            # metered provider, so a run cut off still records what it used, with its
            # conversation: a routine's cost is its conversation's, so the cost line there
            # has it. A timed ask's conversation is only kept once something happens in
            # it, and a paid call is something: it is made to exist before the row.
            work: Work = (
                "helpers" if self.helper is not None else "routines" if self.routine else "asks"
            )
            with as_caller("background", self.conversation, ready=ensure, work=work):
                done = await run_turn(
                    provider=provider,
                    router=a._router,
                    registry=self.tools,
                    system=system,
                    history=[ask],
                    ctx=ctx,
                    emit=emit,
                    save=save,
                )
        finally:
            reads = a.routines.reads
            if self.routine and reads is not None:
                # Its reads are this run's: nothing later remembers them. Kept under its
                # own turn, so a page on this conversation closing mid-run (which ends
                # only its own turns, and `Refresher.forget`, which holds no reads)
                # never drops them.
                reads.end(self.conversation, self.turn)
        self.stop = done.stop
        if done.stop == "provider_error":
            if done.error_kind == "capped":
                if self.began or self.touched:
                    # It had begun, and may have acted: a run cut off, told as failed.
                    raise AskFailed(CAPPED_MIDWAY)
                # Refused before it began: skipped, never failed (the spend caps spec).
                raise Capped(getattr(provider, "provider_id", None))
            if not self.began and done.error_kind in _UNAVAILABLE:
                raise NoModel
            raise AskFailed(f"the model failed: {done.error_kind or 'other'}")
        if self.routine:
            await self._keep()

    def _ended(self, ev: ToolEnded) -> None:
        """A routine's call that succeeded: a board shown, nothing to say, or a read
        a widget it puts up can remember."""
        a = self.a
        reads = a.routines.reads
        if ev.name in ROUTINE_BOARD:
            self.touched = True
        if ev.name in _PUTS_UP:
            self.shown = True
        elif ev.name == ROUTINE_NOTHING:
            self.nothing = True
        elif reads is not None and live.eligible(a._registry.definition(ev.name)):
            title = live.connector(ev.name, a._registry.get(ev.name))
            if title is not None:
                reads.record(self.conversation, self.turn, ev.name, ev.args, title)

    async def _keep(self) -> None:
        """Save a routine's run in its conversation, unless it came to nothing."""
        a = self.a
        if self.shown:
            # A board shown and then taken off again is no board.
            boards = a.routines.boards()
            self.shown = boards is None or _has_widgets(await boards.current(self.conversation))
        # Nothing to tell and no board: nothing of it is kept. A board she put up and
        # then judged not worth telling stays, with what made it, but nobody is told.
        if self.built().outcome == "nothing" and not self.shown:
            return
        # One statement: a tool call is never kept without its result.
        await a._conversations.append_run(self.conversation, a._owner, self.held)
        self.wrote = True
