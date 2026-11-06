"""The scheduler (AGT-4, AGT-8): a loop inside the running server, its queue in SQLite.

Each tick moves every due schedule on by one run, however many times it was missed
(D3), then runs what is due one at a time, reminders first. A reminder becomes a
notice with her words. A timed ask is run unattended (`ask.py`) and its reply becomes
the notice; one more than an hour late is reported missed instead of run stale, one
with no model waits as blocked, and one that fails is reported and never silently
retried. An ask can take minutes: while it runs, the scheduler goes on looking, so a
reminder due meanwhile goes out on time and Settings never sees a stopped scheduler.

Every tick also reclaims runs a crashed process left running, and runs of its own
past their timeout: a reminder is tried once more, or reported missed; a timed ask,
whose tools may already have acted, is reported failed and never run again. One schedule or run
that breaks is logged and skipped, never allowed to hold up the rest.

A routine is a timed ask with a target (the routines spec): run as one, in today's day
conversation or its own, where it builds a board. A board becomes a 'ready' notice
naming the conversation to open; nothing worth saying, no notice at all (AGT-6). A
paused routine is not run, and after three failures in a row it pauses itself.

A helper's handoff (the your helpers spec) is a one-off timed ask run in the helper's
chat: it always ends with one 'ready' notice named for the helper, opening that chat,
its title the helper's first sentence. A helper's routines run as any routine.

At a hard spend cap (the spend caps spec) a routine or timed ask is not run: its run is
`skipped` with the reason `capped`, never failed, retried or counted toward a pause.
The schedule has already moved on, so it next runs at its next time, if the cap has
lifted by then. One that had already begun (said something or called a tool) when its
next call was refused may have acted: it is reported failed, like any run cut off.

The runner never touches a surface. It writes notices and tells the door (SUR-9).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from trillion.scheduler.store import (
    LATE_MS,
    PAUSE_AFTER,
    PAUSE_AFTER_WORDS,
    Kind,
    Notice,
    NoticeKind,
    Run,
    RunStatus,
    Schedule,
    ScheduleStore,
)
from trillion.scheduler.when import WhenError, next_after
from trillion.spend.gate import SpendCapped
from trillion.spend.types import Work
from trillion.spine.clock import Clock

logger = logging.getLogger(__name__)

# A routine blocked by the user's own turn: the page says it in its words
# (`BUSY_DETAIL` in apps/web/src/reminders.ts), so the two stay the same text.
BUSY_DETAIL = "a turn is running there"

TICK_S = 15.0
# A timed ask this far past due is reported missed instead of run stale (D3).
ASK_WINDOW_MS = 60 * 60 * 1000
ASK_TIMEOUT_S = 120.0
# A routine reads several sources and builds a board: it is given longer.
ROUTINE_TIMEOUT_S = 300.0
REMINDER_TIMEOUT_S = 10.0
# A run is tried at most twice: once, and once more after an interruption.
MAX_ATTEMPTS = 2


class NoModel(Exception):
    """No model to run a timed ask with. The run is blocked, not failed (TESTING §4.2)."""


class Busy(Exception):
    """A page is open on the routine's conversation and her turn is running there. The
    run waits, blocked, for a later tick: two writers would interleave its history."""


class Capped(Exception):
    """Her model refused the run at its hard spend cap. Skipped, never failed."""

    def __init__(self, provider_id: str | None) -> None:
        super().__init__("capped")
        self.provider_id = provider_id


# A run not made because its model is at its hard spend cap: the run log's reason.
CAPPED = "capped"
# A run that had begun when its model reached the hard cap: failed, with this reason.
CAPPED_MIDWAY = "stopped partway: its model reached the spend cap"


class AskFailed(Exception):
    """A timed ask ran and did not produce a reply. `why` is short and holds no content."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


RunAsk = Callable[[Schedule], Awaitable[str]]
T = TypeVar("T")
Outcome = Literal["ready", "nothing"]
DAY_READY = "Your day is ready"
DAY_FAILED = "Your day couldn't be built"
# A handoff's ready line, at most: one line, never a paragraph.
HANDOFF_TITLE_MAX = 140
# How a handoff's reply says an action needs the user (its overlay tells it to).
NEEDS_YOU = re.compile(r"^needs you:\s*", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


@dataclass(frozen=True, kw_only=True)
class Built:
    """What a routine's run came to: whether it put up a board, whether she called
    `routine_nothing`, and her last words."""

    shown: bool
    nothing: bool
    said: str
    # The helper it ran as, by name: a handoff's ready line is named for it.
    helper: str | None = None

    @property
    def outcome(self) -> Outcome:
        """Her `routine_nothing` wins, board or not: she judged it not worth telling.
        Otherwise a board, or words in the routine's conversation, are ready to see."""
        if self.nothing or not (self.shown or self.said.strip()):
            return "nothing"
        return "ready"


class Routines(Protocol):
    """How a routine is run (`ask.py`): where it lands, then the run itself."""

    async def place(self, s: Schedule) -> str | None:
        """The conversation this run builds in, made if missing. None to skip it: a
        day already built by hand is never overwritten."""
        ...

    async def build(self, s: Schedule, conversation_id: str) -> Built: ...


def handoff_title(name: str, said: str) -> str:
    """A handoff's ready line: "Invoices: 3 invoices are overdue.", its first sentence,
    or "Invoices needs you: approve sending 3 reminders." With nothing said it still
    says so: it was asked for, so silence would read as lost."""
    text = " ".join(said.split())
    needs = NEEDS_YOU.match(text)
    if needs is not None:
        text = text[needs.end() :]
    first = _SENTENCE_END.split(text, maxsplit=1)[0] if text else ""
    if needs is not None:
        title = f"{name} needs you: {first}" if first else f"{name} needs you"
    else:
        title = f"{name}: {first or 'nothing to report'}"
    if len(title) > HANDOFF_TITLE_MAX:
        title = title[: HANDOFF_TITLE_MAX - 1].rstrip() + "\u2026"
    return title


def ready_title(s: Schedule) -> str:
    """The ready line: "Your Monday revenue is ready", or "Your day is ready"."""
    return DAY_READY if s.target == "day" else f"Your {s.title} is ready"


async def resume(store: ScheduleStore, owner_id: str, schedule_id: str, now_ms: int) -> bool:
    """Resume a paused routine from its next due time after now: a time it was paused
    through is never run late. False if there was no paused routine to resume."""
    s = await store.get(schedule_id)
    if s is None or s.owner_id != owner_id or s.paused_ms is None:
        return False
    try:
        following = next_after(s.at_local, s.repeat, s.zone, now_ms)
    except WhenError:
        following = None
    return await store.resume(owner_id, schedule_id, following)


def paused_title(s: Schedule) -> str:
    """The one notice that says a routine paused itself."""
    return f"{s.title} failed {PAUSE_AFTER_WORDS} times, so I've paused it"


class _Handled(Exception):
    """A run's failure was dealt with: blocked or reported."""


class DoorLike(Protocol):
    """All the runner may do about a notice: say it was written."""

    async def added(self, notice: Notice) -> None: ...

    async def flush(self) -> None: ...


class Scheduler:
    def __init__(
        self,
        store: ScheduleStore,
        clock: Clock,
        run_ask: RunAsk,
        door: DoorLike,
        *,
        routines: Routines | None = None,
        held: Callable[[Work], Awaitable[bool]] | None = None,
        tick_s: float = TICK_S,
        ask_timeout_s: float = ASK_TIMEOUT_S,
        routine_timeout_s: float = ROUTINE_TIMEOUT_S,
        worker: str | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._run_ask = run_ask
        self._door = door
        self._routines = routines
        # Whether her model is at its hard spend cap, so this kind of work waits.
        self._held = held
        self.tick_s = tick_s
        self._ask_timeout_s = ask_timeout_s
        self._routine_timeout_s = routine_timeout_s
        # Who claims runs: this process. A run another one left running was interrupted.
        self._worker = worker or uuid.uuid4().hex
        self._task: asyncio.Task[None] | None = None
        # One tick at a time, whoever calls it.
        self._ticking = asyncio.Lock()
        # When the scheduler last read its queue: at the start of each look, and at the
        # end of each tick. Settings shows it; it goes stale if this stops or can't read.
        self.checked_ms: int | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:
                # The class name only: a message could carry what she was asked to do.
                logger.error("scheduler tick failed: %s", type(e).__name__)
            await asyncio.sleep(self.tick_s)

    async def tick(self) -> None:
        """One pass: reclaim, enqueue what is due, run it, then let the door flush.
        `checked_ms` moves only once the queue could be read; a store that can't be read
        at all makes it raise, after the door has had its flush. A cancelled tick
        skips the flush."""
        async with self._ticking:
            try:
                await self._pass()
            except asyncio.CancelledError:
                raise  # shutting down: never wait on a page
            except Exception:
                await self._flush()
                raise
            await self._flush()
            self.checked_ms = self._clock()

    async def _flush(self) -> None:
        try:
            await self._door.flush()
        except Exception as e:
            logger.warning("the door could not flush: %s", type(e).__name__)

    async def _pass(self) -> None:
        await self._reclaim()
        started = self._clock()
        await self._reminders(started)
        # Then the timed asks, one at a time, still looking for reminders while each runs.
        while (run := await self._claim("ask", started)) is not None:
            await self._ask(run, started)

    async def _reminders(self, started: int) -> None:
        """Enqueue everything due, then deliver every due reminder. Reading the queue is
        the heartbeat: it says the scheduler is checking, even while an ask runs long."""
        now = self._clock()
        for s in await self._store.due(now):
            try:
                await self._enqueue(s, now)
            except Exception as e:
                logger.error("schedule %s could not be enqueued: %s", s.id, type(e).__name__)
        self.checked_ms = now
        while (run := await self._claim("reminder", started)) is not None:
            await self._settle(run)

    async def _claim(self, kind: Kind, started: int) -> Run | None:
        now = self._clock()
        # A run that blocks during this pass waits for the next one.
        retry_before = min(now - round(self.tick_s * 1000), started - 1)
        return await self._store.claim(self._worker, now, retry_before, kind)

    async def _settle(self, run: Run) -> None:
        try:
            await self._run(run)
        except Exception as e:
            await self._abandon(run, e)

    async def _ask(self, run: Run, started: int) -> None:
        """Run one timed ask. It can take up to its timeout: every tick meanwhile, what
        came due is enqueued and the reminders among it delivered, on time."""
        asking = asyncio.ensure_future(self._settle(run))
        try:
            while not asking.done():
                await asyncio.wait({asking}, timeout=self.tick_s)
                if asking.done():
                    break
                try:
                    await self._reminders(started)
                except Exception as e:
                    # The ask goes on; `checked_ms` stays where it was, as it should.
                    logger.error("scheduler look failed: %s", type(e).__name__)
                await self._flush()
        except BaseException:
            # Shutting down: the ask stops with the tick. Its run is reclaimed later.
            asking.cancel()
            await asyncio.wait({asking})
            raise
        await asking

    async def _enqueue(self, s: Schedule, now: int) -> None:
        if s.next_due_ms is None:  # pragma: no cover - `due` only returns ones with a time
            return
        try:
            following = next_after(s.at_local, s.repeat, s.zone, now)
        except WhenError:
            # Its zone left this computer's zone data: it runs this once, then stops.
            logger.warning("schedule %s can no longer be worked out", s.id)
            following = None
        timeout = self._timeout(s) if s.kind == "ask" else REMINDER_TIMEOUT_S
        await self._store.enqueue(s.id, s.next_due_ms, following, timeout, now)

    def _timeout(self, s: Schedule) -> float:
        return self._routine_timeout_s if s.target is not None else self._ask_timeout_s

    async def _reclaim(self) -> None:
        now = self._clock()
        for run in await self._store.reclaim(self._worker, now):
            try:
                await self._retry_or_miss(run, now)
            except Exception as e:
                await self._abandon(run, e)

    async def _retry_or_miss(self, run: Run, now: int) -> None:
        s = await self._store.get(run.schedule_id)
        if s is None or s.status == "cancelled":
            await self._store.finish(run.id, "cancelled", now, detail="cancelled")
        elif s.kind == "ask":
            # Never run again: unattended tools never ask, but a write the user let run
            # without asking need not be one they can undo, and it may have happened.
            await self._notice(run, s, "failed", None, "failed", "interrupted")
        elif run.attempts < MAX_ATTEMPTS:
            # A reminder runs no tools: a second attempt is safe, and recorded as one.
            await self._store.requeue(run.id, "interrupted; tried again")
        else:
            await self._notice(run, s, "missed", None, "missed", "interrupted")

    async def _abandon(self, run: Run, e: Exception) -> None:
        """A run that broke is finished as failed, with a notice if its schedule can
        still be read, so it is neither stuck nor tried again. This holds even for a
        store error that would have passed: by design a run is never retried silently,
        and the user is told it didn't finish."""
        why = type(e).__name__
        logger.error("run %s broke: %s", run.id, why)
        try:
            s = await self._store.get(run.schedule_id)
            if s is not None:
                await self._notice(run, s, "failed", None, "failed", why)
                return
        except Exception as again:
            logger.error("run %s: no notice: %s", run.id, type(again).__name__)
        try:
            await self._store.finish(run.id, "failed", self._clock(), detail=why)
        except Exception as again:
            logger.error("run %s could not be finished: %s", run.id, type(again).__name__)

    async def _run(self, run: Run) -> None:
        now = self._clock()
        s = await self._store.get(run.schedule_id)
        if s is None or s.status == "cancelled":
            await self._store.finish(run.id, "cancelled", now, detail="cancelled")
            return
        late = now - run.due_ms > LATE_MS
        if s.kind == "reminder":
            await self._notice(run, s, "reminder", s.body, "late" if late else "delivered")
            return
        if s.target is not None and s.paused_ms is not None:
            logger.info("routine %s is paused: not run", s.id)
            await self._store.finish(run.id, "skipped", now, detail="paused")
            return
        if now - run.due_ms > ASK_WINDOW_MS:
            await self._notice(run, s, "missed", None, "missed", "more than an hour late")
            return
        kind: Work = "helpers" if s.helper_id is not None else "routines" if s.target else "asks"
        if self._held is not None and await self._held(kind):
            await self._capped(run, s)
            return
        if s.target is not None or s.helper_id is not None:
            # A helper's handoff runs in its chat, placed and built as a routine is.
            await self._routine(run, s, late)
            return
        try:
            said = await self._attempt(run, s, lambda: self._run_ask(s))
        except _Handled:
            return
        if not said.strip():
            await self._notice(run, s, "failed", None, "failed", "no reply")
            return
        await self._notice(run, s, "ask", said, "late" if late else "delivered")

    async def _attempt(self, run: Run, s: Schedule, work: Callable[[], Awaitable[T]]) -> T:
        """Run an ask within its timeout. No model blocks the run; any failure is
        reported once and never retried. Either way it raises `_Handled`."""
        seconds = self._timeout(s)
        limit = asyncio.timeout(seconds)
        try:
            async with limit:
                return await work()
        except (Capped, SpendCapped):
            await self._capped(run, s)
        except NoModel:
            await self._store.block(run.id, self._clock(), "no model")
        except Busy:
            await self._store.block(run.id, self._clock(), BUSY_DETAIL)
        except TimeoutError:
            # Ours only if the limit ran out; one raised inside the ask is its failure.
            why = f"timed out after {seconds:g} s" if limit.expired() else "TimeoutError"
            await self._notice(run, s, "failed", None, "failed", why)
        except AskFailed as e:
            await self._notice(run, s, "failed", None, "failed", e.why[:200])
        except Exception as e:
            logger.warning("timed ask %s failed: %s", s.id, type(e).__name__)
            await self._notice(run, s, "failed", None, "failed", type(e).__name__)
        raise _Handled

    async def _capped(self, run: Run, s: Schedule) -> None:
        """Not run at a hard spend cap: skipped, and nobody told but the one notice the
        spend desk gives."""
        logger.info("schedule %s not run: its model is at its spend cap", s.id)
        await self._store.finish(run.id, "skipped", self._clock(), detail=CAPPED)

    async def _routine(self, run: Run, s: Schedule, late: bool) -> None:
        """Run a routine where it lands, and tell the user only when there is
        something to see. A helper's handoff runs here too, in the helper's chat."""
        routines = self._routines
        if routines is None:
            await self._notice(run, s, "failed", None, "failed", "routines are not set up")
            return

        async def work() -> tuple[str | None, Built | None]:
            target = await routines.place(s)
            if target is None:
                return None, None
            if s.target == "own" and target != s.conversation_id:
                await self._store.set_conversation(s.id, target)
            return target, await routines.build(s, target)

        try:
            target, built = await self._attempt(run, s, work)
        except _Handled:
            return
        if s.target is None and target is not None and built is not None:
            await self._handed_back(run, s, late, target, built)
            return
        if target is None or built is None:
            # The day was built by hand: hers stays, and nobody is told anything.
            logger.info("routine %s skipped: its day already has a board", s.id)
            await self._store.finish(run.id, "skipped", self._clock(), detail="already built")
            return
        if s.target == "day" and built.outcome == "ready" and not built.shown:
            # Words but no board: the day was not built, and the user hears that.
            await self._notice(run, s, "failed", None, "failed", "no board", title=DAY_FAILED)
            return
        try:
            await self._store.succeeded(s.id)
        except Exception as e:
            logger.warning("routine %s: failures not reset: %s", s.id, type(e).__name__)
        if built.outcome == "nothing":
            # Nothing worth the user's attention: a log line, never a notice (AGT-6).
            logger.info("routine %s had nothing to say", s.id)
            await self._store.finish(run.id, "nothing", self._clock(), detail="nothing")
            return
        # Her words, if any, stay in the routine's conversation. The notice is one line
        # of the host's, spoken once like a reminder's (D3).
        title = ready_title(s)
        status: RunStatus = "late" if late else "delivered"
        await self._notice(
            run, s, "ready", f"{title}.", status, title=title, conversation_id=target
        )

    async def _handed_back(
        self, run: Run, s: Schedule, late: bool, target: str, built: Built
    ) -> None:
        """A helper's handoff ends with one ready line, named for the helper, that opens
        its chat. It was asked for, so it is never silent."""
        title = handoff_title(built.helper or s.title, built.said)
        text = title if title.endswith((".", "!", "?", "\u2026")) else f"{title}."
        status: RunStatus = "late" if late else "delivered"
        await self._notice(run, s, "ready", text, status, title=title, conversation_id=target)

    async def _notice(
        self,
        run: Run,
        s: Schedule,
        kind: NoticeKind,
        text: str | None,
        status: RunStatus,
        detail: str | None = None,
        *,
        title: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        now = self._clock()
        # A routine's failure is counted with its notice, once (`pauses_as`).
        pauses = paused_title(s) if kind == "failed" and s.target is not None else None
        # The notice and the run's end are one change: a crash between them can never
        # leave the run to be tried again with its notice already written.
        notice = await self._store.finish_with_notice(
            run.id,
            status,
            now,
            detail=detail,
            owner_id=s.owner_id,
            source_id=s.id,
            kind=kind,
            title=title or s.title,
            text=text,
            due_ms=run.due_ms,
            late=now - run.due_ms > LATE_MS,
            conversation_id=conversation_id,
            pauses_as=pauses,
        )
        if notice is None:
            logger.info("run %s was already finished, or its schedule cancelled", run.id)
            return
        if pauses is not None and notice.title == pauses:
            logger.info("routine %s failed %d times in a row: paused", s.id, PAUSE_AFTER)
        try:
            await self._door.added(notice)
        except Exception as e:
            # It stays held: the next page that opens gets it.
            logger.warning("the door could not take notice %s: %s", notice.id, type(e).__name__)
