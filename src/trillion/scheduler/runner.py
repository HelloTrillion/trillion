"""The scheduler (AGT-4, AGT-8): a loop inside the running server, its queue in SQLite.

Each tick moves every due schedule on by one run, however many times it was missed
(D3), then runs what is due one at a time. A reminder becomes a notice with her
words. A timed ask is run unattended (`ask.py`) and its reply becomes the notice;
one more than an hour late is reported missed instead of run stale, one with no
model waits as blocked, and one that fails is reported and never silently retried.

Every tick also reclaims runs a crashed process left running, and runs of its own
past their timeout: each is tried once more, or reported missed. One schedule or run
that breaks is logged and skipped, never allowed to hold up the rest.

The runner never touches a surface. It writes notices and tells the door (SUR-9).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from trillion.scheduler.store import (
    LATE_MS,
    Notice,
    NoticeKind,
    Run,
    RunStatus,
    Schedule,
    ScheduleStore,
)
from trillion.scheduler.when import WhenError, next_after
from trillion.spine.clock import Clock

logger = logging.getLogger(__name__)

TICK_S = 15.0
# A timed ask this far past due is reported missed instead of run stale (D3).
ASK_WINDOW_MS = 60 * 60 * 1000
ASK_TIMEOUT_S = 120.0
REMINDER_TIMEOUT_S = 10.0
# A run is tried at most twice: once, and once more after an interruption.
MAX_ATTEMPTS = 2


class NoModel(Exception):
    """No model to run a timed ask with. The run is blocked, not failed (TESTING §4.2)."""


class AskFailed(Exception):
    """A timed ask ran and did not produce a reply. `why` is short and holds no content."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


RunAsk = Callable[[Schedule], Awaitable[str]]


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
        tick_s: float = TICK_S,
        ask_timeout_s: float = ASK_TIMEOUT_S,
        worker: str | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._run_ask = run_ask
        self._door = door
        self.tick_s = tick_s
        self._ask_timeout_s = ask_timeout_s
        # Who claims runs: this process. A run another one left running was interrupted.
        self._worker = worker or uuid.uuid4().hex
        self._task: asyncio.Task[None] | None = None
        # One tick at a time, whoever calls it.
        self._ticking = asyncio.Lock()
        # When the last tick finished. Settings shows it; it goes stale if this stops.
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
        `checked_ms` moves only when the pass got through; a store that can't be read
        at all makes it raise, after the door has had its flush."""
        async with self._ticking:
            try:
                await self._pass()
            finally:
                try:
                    await self._door.flush()
                except Exception as e:
                    logger.warning("the door could not flush: %s", type(e).__name__)
            self.checked_ms = self._clock()

    async def _pass(self) -> None:
        await self._reclaim()
        started = self._clock()
        for s in await self._store.due(started):
            try:
                await self._enqueue(s, started)
            except Exception as e:
                logger.error("schedule %s could not be enqueued: %s", s.id, type(e).__name__)
        while True:
            now = self._clock()
            # A run that blocks during this pass waits for the next one.
            retry_before = min(now - round(self.tick_s * 1000), started - 1)
            run = await self._store.claim(self._worker, now, retry_before)
            if run is None:
                break
            try:
                await self._run(run)
            except Exception as e:
                await self._abandon(run, e)

    async def _enqueue(self, s: Schedule, now: int) -> None:
        if s.next_due_ms is None:  # pragma: no cover - `due` only returns ones with a time
            return
        try:
            following = next_after(s.at_local, s.repeat, s.zone, now)
        except WhenError:
            # Its zone left this computer's zone data: it runs this once, then stops.
            logger.warning("schedule %s can no longer be worked out", s.id)
            following = None
        timeout = self._ask_timeout_s if s.kind == "ask" else REMINDER_TIMEOUT_S
        await self._store.enqueue(s.id, s.next_due_ms, following, timeout, now)

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
        elif run.attempts < MAX_ATTEMPTS and (
            s.kind == "reminder" or now - run.due_ms <= ASK_WINDOW_MS
        ):
            # Unattended tools never ask and only do what the user can undo, so a
            # second attempt is safe. It is recorded as one.
            await self._store.requeue(run.id, "interrupted; tried again")
        else:
            await self._notice(run, s, "missed", None, "missed", "interrupted")

    async def _abandon(self, run: Run, e: Exception) -> None:
        """A run that broke is finished as failed, with a notice if its schedule can
        still be read, so it is neither stuck nor tried again."""
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
        if now - run.due_ms > ASK_WINDOW_MS:
            await self._notice(run, s, "missed", None, "missed", "more than an hour late")
            return
        limit = asyncio.timeout(self._ask_timeout_s)
        try:
            async with limit:
                said = await self._run_ask(s)
        except NoModel:
            await self._store.block(run.id, self._clock(), "no model")
            return
        except TimeoutError:
            # Ours only if the limit ran out; one raised inside the ask is its failure.
            why = (
                f"timed out after {self._ask_timeout_s:g} s" if limit.expired() else "TimeoutError"
            )
            await self._notice(run, s, "failed", None, "failed", why)
            return
        except AskFailed as e:
            await self._notice(run, s, "failed", None, "failed", e.why[:200])
            return
        except Exception as e:
            logger.warning("timed ask %s failed: %s", s.id, type(e).__name__)
            await self._notice(run, s, "failed", None, "failed", type(e).__name__)
            return
        if not said.strip():
            await self._notice(run, s, "failed", None, "failed", "no reply")
            return
        await self._notice(run, s, "ask", said, "late" if late else "delivered")

    async def _notice(
        self,
        run: Run,
        s: Schedule,
        kind: NoticeKind,
        text: str | None,
        status: RunStatus,
        detail: str | None = None,
    ) -> None:
        now = self._clock()
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
            title=s.title,
            text=text,
            due_ms=run.due_ms,
            late=now - run.due_ms > LATE_MS,
        )
        if notice is None:
            logger.warning("run %s was already finished", run.id)
            return
        try:
            await self._door.added(notice)
        except Exception as e:
            # It stays held: the next page that opens gets it.
            logger.warning("the door could not take notice %s: %s", notice.id, type(e).__name__)
