"""The one door (SUR-9): the only component that turns a notice into something the
user sees or hears.

A notice goes to the live page, the most recently active one, when that page is
free. Free is judged from facts the page's session reports, never from a flag
(VOI-17): no turn running, no microphone open, and the last thing sent to be heard
reported played. With no page open the computer shows a notification, once, and
the notice is held for the next page. Each notice is marked delivered before it is
sent, and only a held one can be, so none arrives twice. A session's `deliver`
must raise, taking none of them, if its page is no longer free when they arrive:
the door then holds them again for the next free page.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from trillion.delivery import notify as system
from trillion.scheduler import when
from trillion.scheduler.store import LATE_MS, Notice, NoticeKind, ScheduleStore
from trillion.spine.clock import Clock

logger = logging.getLogger(__name__)

# A page that never reports the end of what it played stops holding notices back
# after this long. Audio arrives faster than it plays; no reply is this long.
PLAYBACK_GRACE_MS = 120_000
# Shown for a timed ask: her answer may quote an email, and a lock screen is public.
READY = "Ready in Trillion"
_QUIET: dict[NoticeKind, str] = {
    "missed": "Missed: Trillion was not running at the time",
    "failed": "Didn't finish. Open Trillion to see why",
}


@dataclass(frozen=True, kw_only=True)
class Facts:
    """What one page's session can say about itself right now."""

    turn_running: bool
    listening: bool
    # The last turn sent to be heard, and when its audio finished sending.
    spoken_turn: int | None
    spoken_ms: int | None
    # The last turn the page reported played to its end.
    heard_turn: int | None
    # When the user last did something there: the live page is the latest.
    active_ms: int

    def __post_init__(self) -> None:
        # A turn sent to be heard always says when, so a page that never reports
        # playing it can't hold notices back forever.
        if self.spoken_turn is not None and self.spoken_ms is None:
            raise ValueError("spoken_turn needs spoken_ms")


def free(f: Facts, now_ms: int) -> bool:
    """Nothing is in flight (VOI-17): no reply being made, none still being heard."""
    if f.turn_running or f.listening:
        return False
    if f.spoken_turn is None or f.spoken_ms is None or f.heard_turn == f.spoken_turn:
        return True
    return now_ms - f.spoken_ms > PLAYBACK_GRACE_MS


@dataclass(frozen=True, kw_only=True)
class Delivery:
    """One notice, as a page shows it."""

    id: str
    kind: NoticeKind
    title: str
    text: str | None
    due_ms: int
    late: bool
    # When it was due, in words and in the schedule's own zone ("Sat 4:00 pm"). Empty
    # when that can't be told: its schedule is gone, or its zone unknown here.
    due: str = ""


class NotFree(Exception):
    """Raised by a session's `deliver` when its page is no longer free: it took none."""


class SessionLike(Protocol):
    """What the door needs of a page's session. `api/session.py` is the real one."""

    @property
    def id(self) -> str: ...

    def facts(self) -> Facts: ...

    async def deliver(self, notices: Sequence[Delivery]) -> None:
        """Save each as her message and show it; speak the ones with words. Queues the
        speech and returns, never waiting for it to play. Raises `NotFree`, taking none,
        if the page is no longer free."""
        ...


Notify = Callable[[str, str], bool]


def notification(n: Notice) -> tuple[str, str]:
    """What the computer shows: the task's own title, and her words only for a
    reminder, which she wrote to be seen."""
    if n.kind == "reminder" and n.text:
        return n.title, n.text
    return n.title, _QUIET.get(n.kind, READY)


class Door:
    def __init__(
        self,
        store: ScheduleStore,
        sessions: Collection[SessionLike],
        *,
        clock: Clock,
        owner: str,
        notify: Notify = system.notify,
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._clock = clock
        self._owner = owner
        # Public so a test can put a fake in its place: nothing else may call it.
        self.notify = notify
        self._lock = asyncio.Lock()
        self._notifying = asyncio.Lock()

    def live(self) -> SessionLike | None:
        """The most recently active connected page."""
        return max(self._sessions, key=lambda s: s.facts().active_ms, default=None)

    async def added(self, notice: Notice) -> None:
        """A notice was written. Delivered now if the live page is free; held otherwise,
        with a system notification when no page is open at all."""
        live = self.live()
        if live is not None:
            await self.offer(live)
            return
        async with self._notifying:
            # As stored now, not as the caller saw it: shown once, never once delivered.
            kept = await self._store.notice(notice.id)
            if kept is None or kept.status != "held" or kept.notified_ms is not None:
                return
            title, body = notification(kept)
            # It can take seconds: never on the event loop.
            if await asyncio.to_thread(self.notify, title, body):
                await self._store.mark_notified(kept.id, self._clock())

    async def offer(self, session: SessionLike) -> int:
        """A page opened or came free: it gets every held notice, oldest first, if it is
        the live page. Returns how many it got."""
        async with self._lock:
            now = self._clock()
            if session is not self.live() or not free(session.facts(), now):
                return 0
            out: list[Delivery] = []
            zones: dict[str, str] = {}
            for n in await self._store.held(self._owner):
                if await self._store.mark_delivered(n.id, session.id, now):
                    out.append(
                        Delivery(
                            id=n.id,
                            kind=n.kind,
                            title=n.title,
                            # Only she writes words; a missed or failed notice has none (D7).
                            text=n.text if n.kind in ("reminder", "ask") else None,
                            due_ms=n.due_ms,
                            late=n.late or now - n.due_ms > LATE_MS,
                            due=await self._due(n, now, zones),
                        )
                    )
        if not out:
            return 0
        # Its own task, so a tick cut short never cuts a hand-over short: past its save
        # the session must not stop half way, or they would be held and saved again.
        handing = asyncio.ensure_future(session.deliver(out))
        try:
            await asyncio.shield(handing)
        except asyncio.CancelledError:
            await _finished(handing)
            if not handing.cancelled() and handing.exception() is not None:
                await asyncio.shield(self._hold(out, session))
            raise
        except Exception as e:
            # It raised before taking any: they are held again for the next free page.
            logger.warning("page %s could not take notices: %s", session.id, type(e).__name__)
            await self._hold(out, session)
            return 0
        return len(out)

    async def _due(self, n: Notice, now: int, zones: dict[str, str]) -> str:
        if n.source_id not in zones:
            s = await self._store.get(n.source_id)
            zones[n.source_id] = s.zone if s is not None else ""
        try:
            return when.due_words(n.due_ms, zones[n.source_id], now)
        except when.WhenError:
            return ""

    async def _hold(self, out: Sequence[Delivery], session: SessionLike) -> None:
        """Only for a hand-over that raised: by the contract it took none of them."""
        for d in out:
            await self._store.unmark_delivered(d.id, session.id)

    async def flush(self) -> None:
        """On every scheduler tick: a page that came free without saying so gets its due."""
        live = self.live()
        if live is not None:
            await self.offer(live)


async def _finished(task: asyncio.Future[None]) -> None:
    """Wait for a hand-over to end, however often the waiting is cancelled. It is short:
    one write and the sends, never the speech."""
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait({task})
