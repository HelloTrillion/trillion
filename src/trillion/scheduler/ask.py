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
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from trillion.brain.loop import (
    CallTimed,
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
    Usage,
)
from trillion.persona import day
from trillion.persona.prompt import Identity, scheduled, volatile_context
from trillion.scheduler.runner import AskFailed, Built, Busy, NoModel
from trillion.scheduler.store import Schedule
from trillion.spine.contract import ToolContext, Via
from trillion.spine.registry import ROUTINE_BOARD, ROUTINE_NOTHING, ToolRegistry
from trillion.spine.router import Router
from trillion.spine.untrusted import is_outside
from trillion.storage.sqlite.conversation_store import Kind, SqliteConversationStore
from trillion.workspace import live

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


@dataclass(frozen=True, kw_only=True)
class RoutineDeps:
    """What only a routine's run needs of the running app."""

    boards: Callable[[], BoardsLike | None] = lambda: None
    # The run's eligible reads, so a widget it puts up is kept fresh (live widgets).
    reads: live.Reads | None = None
    day_setup: Callable[[], day.SetUp] = day.SetUp
    # Whether a page open on a conversation is taking input or running a turn there.
    busy: Callable[[str], bool] = lambda conversation_id: False
    # Tells a page open on a conversation that a run changed it.
    ran: Callable[[str], Awaitable[None]] | None = None
    # The conversations a routine is building in now: one writer per conversation, so
    # a page's turn there is refused until it is done (`Session`).
    holds: set[str] = field(default_factory=set)


def routine_request(s: Schedule, recipe: str | None = None) -> str:
    """What a routine's run is asked: its name, then the day's recipe or its own
    request, the user's notes, and for its own conversation what to do with it."""
    lines = [f"Routine: {s.title}", "", recipe if recipe is not None else s.body]
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
        system: Callable[[], list[SystemBlock]],
        identity: Callable[[], Identity],
        granted_roots: Callable[[], tuple[Path, ...]],
        zone: Callable[[], str | None],
        clock: Callable[[], int],
        owner: str,
        routines: RoutineDeps | None = None,
    ) -> None:
        self._provider = provider
        self._router = router
        self._registry = registry
        self._conversations = conversations
        self._system = system
        self._identity = identity
        self._roots = granted_roots
        self._zone = zone
        self._clock = clock
        self._owner = owner
        self.routines = routines or RoutineDeps()
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

    async def place(self, schedule: Schedule) -> str | None:
        """Where a routine's run builds: today's day conversation in the user's zone,
        or the routine's own, each made if missing. None when the day already has a
        board: one built by hand is never overwritten."""
        now = self._clock()
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
        """Run a routine in its conversation, holding it: the one writer there until
        it is done. `Busy` if a page is taking input or running a turn there."""
        r = self.routines
        self._free(conversation_id)
        # Taken with no await since the check, so a page can never start in between.
        r.holds.add(conversation_id)
        try:
            recipe = None
            if schedule.target == "day":
                date = day.today(self._clock(), self._zone())
                recipe = day.recipe(
                    r.day_setup(), date, self._zone() or "", self._identity(), routine=True
                )
            run = _Run(self, schedule, conversation_id, target=conversation_id)
            try:
                await run.go(routine_request(schedule, recipe))
            except BaseException:
                await self._after(run, failed=True)
                raise
            await self._after(run, failed=False)
            return run.built()
        finally:
            r.holds.discard(conversation_id)

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

    def __init__(self, asker: Asker, schedule: Schedule, conversation: str, *, target: str | None):
        self.a = asker
        self.schedule = schedule
        self.conversation = conversation
        self.target = target
        self.routine = target is not None
        self.via: Via = "routine" if self.routine else "schedule"
        self.turn = ROUTINE_TURN if self.routine else 1
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

    def built(self) -> Built:
        return Built(shown=self.shown, nothing=self.nothing, said=self.reply())

    async def go(self, text: str) -> None:
        a = self.a
        provider = a._provider()
        if provider is None:
            raise NoModel
        system = [
            *await asyncio.to_thread(a._system),
            SystemBlock(text=scheduled(a._identity())),
        ]
        zone = a._zone() or ""
        now = datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone()
        notes = (ContextBlock(text=ROUTINE_RUN),) if self.routine else ()
        ask = Msg(
            role="user",
            blocks=(
                ContextBlock(text=volatile_context(now, [], zone=zone)),
                *notes,
                TextBlock(text=text),
            ),
        )
        # A routine's request is the host's, saved as a tap is: never the user's words,
        # and it advances no turn of theirs.
        asked: Kind = "tap" if self.routine else "user"
        roots = a._roots()
        pricing = provider.pricing()
        spent = Usage()
        cost: float | None = 0.0 if pricing is not None else None

        async def ensure() -> None:
            # Kept only once something happens in it: an ask blocked on every tick for an
            # hour leaves no empty conversation behind each time. A routine's is there.
            if not self.created:
                now_ms = a._clock()
                if not self.routine:
                    await a._conversations.create(self.conversation, a._owner, now_ms, "scheduled")
                    await a._conversations.append(self.conversation, a._owner, asked, ask, now_ms)
                else:
                    self.held.append((asked, ask, now_ms))
                self.created = True

        async def settle(usage: Usage, cost_usd: float | None) -> None:
            if not self.created and usage == Usage():
                return
            await ensure()
            # A routine's cost is its conversation's, so the cost line there has it.
            await a._conversations.record_usage(
                owner_id=a._owner,
                conversation_id=self.conversation,
                provider=provider.name,
                model=provider.model,
                usage=usage,
                cost_usd=cost_usd,
                now_ms=a._clock(),
            )

        def ctx(call_id: str) -> ToolContext:
            return ToolContext(
                owner_id=a._owner,
                session_id=self.conversation,
                turn=self.turn,
                call_id=call_id,
                via=self.via,
                granted_roots=roots,
                read_outside=self.outside,
                routine_target=self.target,
            )

        async def emit(ev: LoopEvent) -> None:
            nonlocal spent, cost
            if isinstance(ev, CallTimed):
                # Counted as it is spent, so a run cut off still records what it used.
                spent = spent + ev.usage
                if pricing is not None and cost is not None:
                    cost += pricing.cost(ev.usage)
            elif isinstance(ev, ReplyDelta):
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
        except BaseException:
            # Timed out, cancelled or broken: what it spent is recorded all the same, and
            # a second cancellation cannot cut the write short.
            try:
                await asyncio.shield(settle(spent, cost))
            except Exception as e:
                logger.warning(
                    "usage of timed ask %s not recorded: %s", self.schedule.id, type(e).__name__
                )
            raise
        finally:
            reads = a.routines.reads
            if self.routine and reads is not None:
                # Its reads are this run's: nothing later remembers them. Kept under its
                # own turn, so a page on this conversation closing mid-run (which ends
                # only its own turns, and `Refresher.forget`, which holds no reads)
                # never drops them.
                reads.end(self.conversation, self.turn)
        await settle(done.usage, done.cost_usd)
        self.stop = done.stop
        if done.stop == "provider_error":
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
