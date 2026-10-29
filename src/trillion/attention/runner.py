"""The attention runner (AGT-5, the speaking up spec): watchers on timers, inside the
server, beside the scheduler.

Watchers fetch in code; the model only words (D1). Every 15 s the runner looks at
each watcher that is switched on and due by its own interval, and runs its check with
a 20 s limit. One that fails or hangs is logged by its kind and tried again at its
next interval, never sooner. What it finds is claimed by key first, so a key is told
at most once, across restarts (D5); nothing new is a log line and nothing else (D6).
New finds from one check become one notice: her one line about all of them, written
by `words.line` (D3), or none, when it is shown by the watcher's title unspoken.

A notice's title is the watcher's own ("Meeting soon"), never the find's: the system
notification shows it on a lock screen, and an invite title or an email subject is
someone else's words. The specifics are only in her line.

A notice made from several finds takes the first one's key as its source; every key
is recorded told with the notice's id.

The runner never touches a surface. It writes notices and tells the door (SUR-9).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from trillion.attention import words
from trillion.attention.settings import AttentionSettings
from trillion.attention.store import AttentionStore
from trillion.brain.types import ModelProvider, Usage
from trillion.scheduler.store import Notice, ScheduleStore, https_link
from trillion.spine.clock import Clock

if TYPE_CHECKING:
    from trillion.connectors.google.connection import GoogleConnection
    from trillion.spine.registry import ToolRegistry

logger = logging.getLogger(__name__)

TICK_S = 15.0
CHECK_TIMEOUT_S = 20.0

# Anything JSON can carry: what a find hands the model.
JSON = dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class Find:
    """One thing a watcher found. `key` is stable (`meeting:<event id>:<start>`), so it
    is told at most once. `facts` is all the model is shown, wrapped as untrusted."""

    key: str
    # What it is about, for the watcher's own use and the logs' count; never shown on
    # a lock screen.
    title: str
    facts: JSON
    # After this the notice is shown, not spoken (D9).
    fresh_until_ms: int | None = None
    # https only; anything else is dropped from the notice.
    link: str | None = None


@dataclass(kw_only=True)
class WatchContext:
    """What a check may use. A watcher may move `cursor` on; it is saved when the check
    succeeds."""

    owner: str
    now_ms: int
    clock: Clock
    settings: AttentionSettings
    notes: str
    cursor: str | None
    last_check_ms: int | None
    provider: ModelProvider | None
    # The runtime's handles the watchers read through (Calendar and Gmail, MCP tools).
    google: GoogleConnection | None = None
    registry: ToolRegistry | None = None


class Watcher(Protocol):
    # Its stable name: settings, state and notes are kept under it.
    name: str
    # The notice's title, safe on a lock screen: "Meeting soon".
    title: str
    interval_s: float

    def enabled(self, settings: AttentionSettings) -> bool:
        """Switched on and its source connected."""
        ...

    async def check(self, ctx: WatchContext) -> list[Find]: ...


@dataclass(frozen=True, kw_only=True)
class Said:
    """A judging watcher's answer: her line (None to show by title, unspoken) and the
    finds worth telling. None told is silence (D6): every find stays silent."""

    text: str | None
    told: Sequence[Find]
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class Judge(Protocol):
    """A watcher that lets the model judge what is worth saying (urgent email) instead
    of the runner's `words.line`. Called only with new finds."""

    async def judge(self, ctx: WatchContext, finds: Sequence[Find]) -> Said: ...


class DoorLike(Protocol):
    """All the runner may do about a notice: say it was written."""

    async def added(self, notice: Notice) -> None: ...


Spent = Callable[[ModelProvider, Usage], Awaitable[None]]


class AttentionRunner:
    def __init__(
        self,
        store: AttentionStore,
        notices: ScheduleStore,
        door: DoorLike,
        *,
        clock: Clock,
        owner: str,
        provider: Callable[[], ModelProvider | None],
        settings: Callable[[], AttentionSettings],
        spent: Spent | None = None,
        google: Callable[[], GoogleConnection | None] = lambda: None,
        registry: ToolRegistry | None = None,
        tick_s: float = TICK_S,
        check_timeout_s: float = CHECK_TIMEOUT_S,
    ) -> None:
        self._store = store
        self._notices = notices
        self._door = door
        self._clock = clock
        self._owner = owner
        self._provider = provider
        self._settings = settings
        self._spent = spent
        self._google = google
        self._registry = registry
        self.tick_s = tick_s
        self._check_timeout_s = check_timeout_s
        self._watchers: list[Watcher] = []
        # When each watcher is next due, in this process: the first tick runs them all.
        self._next: dict[str, int] = {}
        self._task: asyncio.Task[None] | None = None
        self._ticking = asyncio.Lock()

    def add(self, watcher: Watcher) -> None:
        if any(w.name == watcher.name for w in self._watchers):
            raise ValueError(f"a watcher named {watcher.name} is already running")
        self._watchers.append(watcher)

    @property
    def watchers(self) -> list[Watcher]:
        return list(self._watchers)

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
                logger.error("attention tick failed: %s", type(e).__name__)
            await asyncio.sleep(self.tick_s)

    async def tick(self) -> None:
        """Run every watcher that is on and due, one at a time. One that breaks is
        logged and never holds up the rest."""
        async with self._ticking:
            settings = self._settings()
            for w in self._watchers:
                now = self._clock()
                if now < self._next.get(w.name, 0):
                    continue
                try:
                    if not w.enabled(settings):
                        continue
                except Exception as e:
                    logger.warning("attention %s: can't tell if on: %s", w.name, type(e).__name__)
                    continue
                # Due again a whole interval on, however this one goes (never sooner).
                self._next[w.name] = now + int(w.interval_s * 1000)
                try:
                    await self._run(w, settings, now)
                except Exception as e:
                    logger.error("attention %s failed: %s", w.name, type(e).__name__)

    async def _run(self, w: Watcher, settings: AttentionSettings, now: int) -> None:
        state = await self._store.state(self._owner, w.name)
        ctx = WatchContext(
            owner=self._owner,
            now_ms=now,
            clock=self._clock,
            settings=settings,
            notes=state.notes,
            cursor=state.cursor,
            last_check_ms=state.last_check_ms,
            provider=self._provider(),
            google=self._google(),
            registry=self._registry,
        )
        try:
            finds = await asyncio.wait_for(w.check(ctx), self._check_timeout_s)
        except Exception as e:
            # The kind only: a message could carry an email subject or an invite title.
            logger.warning("attention %s check failed: %s", w.name, type(e).__name__)
            await self._store.failed(self._owner, w.name, type(e).__name__)
            return
        moved = ctx.cursor if ctx.cursor != state.cursor else None
        await self._store.checked(self._owner, w.name, now, cursor=moved)
        by_key = {f.key: f for f in finds}
        new = await self._store.claim(self._owner, w.name, list(by_key), now)
        if not new:
            logger.info("attention %s: nothing new", w.name)
            return
        await self._tell(w, ctx, [by_key[k] for k in new])

    async def _tell(self, w: Watcher, ctx: WatchContext, finds: list[Find]) -> None:
        provider = ctx.provider
        if isinstance(w, Judge):
            said = await w.judge(ctx, finds)
            if provider is not None:
                await self._spend(provider, said.usage)
            told, text = list(said.told), said.text
            if not told:
                logger.info("attention %s: nothing worth saying in %d", w.name, len(finds))
                return
        else:
            told, text = finds, None
            if provider is not None:
                text, usage = await words.line(provider, w.name, finds, ctx.notes)
                await self._spend(provider, usage)
        notice = await self._notices.attention_notice(
            self._owner,
            told[0].key,
            title=w.title,
            text=text,
            now_ms=ctx.now_ms,
            link=_link(next((f.link for f in told if f.link), None)),
            fresh_until_ms=min(
                (f.fresh_until_ms for f in told if f.fresh_until_ms is not None), default=None
            ),
        )
        await self._store.told(self._owner, [f.key for f in told], notice.id)
        logger.info("attention %s: told %d", w.name, len(told))
        await self._door.added(notice)

    async def _spend(self, provider: ModelProvider, usage: Usage) -> None:
        if self._spent is None or usage == Usage():
            return
        try:
            await self._spent(provider, usage)
        except Exception as e:
            logger.warning("attention usage not recorded: %s", type(e).__name__)


def _link(link: str | None) -> str | None:
    """A find's link as a notice may carry it: https, or none at all."""
    try:
        return https_link(link)
    except ValueError:
        return None
