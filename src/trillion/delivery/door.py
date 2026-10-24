"""The one door (SUR-9): the only component that turns a notice into something the
user sees or hears.

A notice goes to the live page, the most recently active one, when that page is
free. Free is judged from facts the page's session reports, never from a flag
(VOI-17): no turn running, no microphone open, and the last thing sent to be heard
reported played. With no page open the computer shows a notification, once, and
the notice is held for the next page. Each notice is marked delivered before it is
sent, and only a held one can be, so none arrives twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from trillion.delivery import notify as system
from trillion.scheduler.store import LATE_MS, Notice, NoticeKind, ScheduleStore
from trillion.spine.clock import Clock

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


def free(f: Facts, now_ms: int) -> bool:
    """Nothing is in flight (VOI-17): no reply being made, none still being heard."""
    if f.turn_running or f.listening:
        return False
    if f.spoken_turn is None or f.heard_turn == f.spoken_turn:
        return True
    return f.spoken_ms is not None and now_ms - f.spoken_ms > PLAYBACK_GRACE_MS


@dataclass(frozen=True, kw_only=True)
class Delivery:
    """One notice, as a page shows it."""

    id: str
    kind: NoticeKind
    title: str
    text: str | None
    due_ms: int
    late: bool


class SessionLike(Protocol):
    """What the door needs of a page's session. `api/session.py` is the real one."""

    @property
    def id(self) -> str: ...

    def facts(self) -> Facts: ...

    async def deliver(self, notices: Sequence[Delivery]) -> None:
        """Save each as her message and show it; speak the ones with words."""
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
        # As stored now, not as the caller saw it: shown once, and never once delivered.
        kept = await self._store.notice(notice.id)
        if kept is None or kept.status != "held" or kept.notified_ms is not None:
            return
        title, body = notification(kept)
        if await asyncio.to_thread(self.notify, title, body):
            await self._store.mark_notified(kept.id, self._clock())

    async def offer(self, session: SessionLike) -> int:
        """A page opened or came free: it gets every held notice, oldest first, if it is
        the live page. Returns how many it got."""
        now = self._clock()
        async with self._lock:
            if session is not self.live() or not free(session.facts(), now):
                return 0
            out: list[Delivery] = []
            for n in await self._store.held(self._owner):
                if await self._store.mark_delivered(n.id, session.id, now):
                    out.append(
                        Delivery(
                            id=n.id,
                            kind=n.kind,
                            title=n.title,
                            text=n.text,
                            due_ms=n.due_ms,
                            late=n.late or now - n.due_ms > LATE_MS,
                        )
                    )
        if out:
            await session.deliver(out)
        return len(out)

    async def flush(self) -> None:
        """On every scheduler tick: a page that came free without saying so gets its due."""
        live = self.live()
        if live is not None:
            await self.offer(live)
