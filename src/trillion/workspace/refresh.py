"""Live widgets refresh while the board is seen (the live widgets spec).

One runtime task works a queue. It knows, per conversation, whether a page shows its
board (`board.seen`), and only those boards' widgets are refreshed: a widget is due
when it has `live`, `refresh` is not off, and its facts are older than `every`. Opening
the board catches up at once. At most one refresh is in flight and at most 60 start in
any hour across the runtime; while her own turn runs on a conversation, its refreshes
wait; the user's "Keep widgets fresh" switch stops all of them.

A refresh replays the widget's saved reads through the router as `via="refresh"`
(reads only, never a card), asks the fill-in (`fill.py`) for the new data, and saves
it in place. Any failure keeps the old data and is counted on the widget; after three
in a row it is left alone until the board is opened anew. Nothing is added to the
conversation; what the model call used is recorded with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from trillion.brain.types import ModelProvider, Usage
from trillion.spine.contract import ToolCall, ToolContext, ToolDefinition
from trillion.spine.router import RouterResponse
from trillion.workspace import fill, live
from trillion.workspace.board import Board, BoardError
from trillion.workspace.recipe_kit import OFF

logger = logging.getLogger(__name__)

TICK_S = 30.0
PER_HOUR = 60
HOUR_MS = 60 * 60 * 1000
# Failures in a row before a widget is left alone until the board is opened anew.
MAX_FAILS = 3
# Turn numbers for refreshes, far from any conversation's own.
TURN_BASE = 1 << 40

Key = tuple[str, str]


class Dispatch(Protocol):
    async def dispatch(self, call: ToolCall, ctx: ToolContext) -> RouterResponse: ...


class BoardsLike(Protocol):
    async def current(self, conversation_id: str) -> Board | None: ...

    async def refreshed(
        self, conversation_id: str, before: Mapping[str, Any], after: Any
    ) -> bool: ...

    async def refresh_failed(self, conversation_id: str, before: Mapping[str, Any]) -> bool: ...


def _ms(at: object) -> int | None:
    if not isinstance(at, str):
        return None
    try:
        return int(datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _at(widget: Mapping[str, Any]) -> int | None:
    source = widget.get("source")
    return _ms(source.get("at")) if isinstance(source, Mapping) else None


class Refresher:
    def __init__(
        self,
        *,
        boards: BoardsLike,
        router: Dispatch,
        definition: Callable[[str], ToolDefinition | None],
        provider: Callable[[], ModelProvider | None],
        clock: Callable[[], int],
        owner: str,
        busy: Callable[[str], bool],
        enabled: Callable[[], bool],
        tell: Callable[[str, str, bool], Awaitable[None]],
        spent: Callable[[str, ModelProvider, Usage], Awaitable[None]],
        granted_roots: Callable[[], tuple[Path, ...]] = lambda: (),
        tick_s: float = TICK_S,
    ) -> None:
        self._boards = boards
        self._router = router
        self._definition = definition
        self._provider = provider
        self._clock = clock
        self._owner = owner
        # Her own turn is running on that conversation.
        self._busy = busy
        # The user's "Keep widgets fresh".
        self._enabled = enabled
        # `board.refreshing {id, on}` to the pages on a conversation.
        self._tell = tell
        # Records a model call's usage with a conversation.
        self._spent = spent
        self._roots = granted_roots
        self.tick_s = tick_s
        self._seen: set[str] = set()
        # In order; a refresh the user asked for goes first.
        self._queue: OrderedDict[Key, None] = OrderedDict()
        # When each refresh started, for the rolling hour.
        self._starts: deque[int] = deque()
        # When each widget was last tried, so a failed one waits its interval again.
        self._tried: dict[Key, int] = {}
        # Failures in a row since its board was last opened.
        self._fails: dict[Key, int] = {}
        self.in_flight: Key | None = None
        self._count = 0
        self._work = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # ── What the pages say ───────────────────────────────────────────────

    def seen(self, conversation_id: str, on: bool) -> None:
        """A page shows this conversation's board, or no longer does. Opening it anew
        catches up at once and gives up on no widget."""
        if on:
            if conversation_id not in self._seen:
                self._seen.add(conversation_id)
                for key in [k for k in self._fails if k[0] == conversation_id]:
                    del self._fails[key]
                    self._tried.pop(key, None)
            self._wake.set()
        else:
            self._seen.discard(conversation_id)
            for key in [k for k in self._queue if k[0] == conversation_id]:
                del self._queue[key]

    def is_seen(self, conversation_id: str) -> bool:
        return conversation_id in self._seen

    def now(self, conversation_id: str, widget_id: str) -> None:
        """The user asked for this widget now: first in the queue. Ignored while one is
        in flight for it, or when no page shows the board."""
        key = (conversation_id, widget_id)
        if conversation_id not in self._seen or self.in_flight == key:
            return
        self._queue[key] = None
        self._queue.move_to_end(key, last=False)
        self._wake.set()

    # ── The task ─────────────────────────────────────────────────────────

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
            self._wake.clear()
            try:
                await self.step()
            except Exception as e:
                logger.error("refresh step failed: %s", type(e).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.tick_s)

    async def step(self) -> int:
        """Queue what is due on every board that is seen, then refresh what may run now,
        one at a time. How many ran."""
        async with self._work:
            if not self._enabled():
                self._queue.clear()
                return 0
            for conversation_id in list(self._seen):
                await self._queue_due(conversation_id)
            ran = 0
            for key in list(self._queue):
                if not self._enabled():
                    break
                if key[0] not in self._seen:
                    del self._queue[key]
                    continue
                if self._busy(key[0]):
                    continue  # her turn first; it stays queued
                if not self._room():
                    break
                del self._queue[key]
                if await self._refresh(*key):
                    ran += 1
            return ran

    def _room(self) -> bool:
        since = self._clock() - HOUR_MS
        while self._starts and self._starts[0] <= since:
            self._starts.popleft()
        return len(self._starts) < PER_HOUR

    async def _queue_due(self, conversation_id: str) -> None:
        try:
            board = await self._boards.current(conversation_id)
        except Exception as e:
            logger.warning("board not read for refresh: %s", type(e).__name__)
            return
        if board is None or not board.get("open"):
            return
        now = self._clock()
        for widget in board.get("widgets", []):
            key = (conversation_id, str(widget.get("id")))
            if self._fails.get(key, 0) >= MAX_FAILS or not self._live(widget):
                continue
            last = max(_at(widget) or 0, self._tried.get(key, 0))
            if now - last >= live.every(widget) * 60_000:
                self._queue.setdefault(key, None)

    @staticmethod
    def _live(widget: Mapping[str, Any]) -> bool:
        kept = widget.get("live")
        return (
            isinstance(kept, Mapping) and bool(kept.get("reads")) and widget.get("refresh") != OFF
        )

    # ── One refresh ──────────────────────────────────────────────────────

    async def _refresh(self, conversation_id: str, widget_id: str) -> bool:
        board = await self._boards.current(conversation_id)
        mine = [w for w in (board or {}).get("widgets", []) if w.get("id") == widget_id]
        provider = self._provider()
        if len(mine) != 1 or not self._live(mine[0]) or provider is None:
            return False
        widget = mine[0]
        key = (conversation_id, widget_id)
        now = self._clock()
        self._starts.append(now)
        self._tried[key] = now
        self.in_flight = key
        await self._tell_safely(conversation_id, widget_id, True)
        try:
            ok = await self._run(conversation_id, widget, provider)
        except Exception as e:
            logger.warning("refresh of a widget failed: %s", type(e).__name__)
            ok = False
        finally:
            self.in_flight = None
            await self._tell_safely(conversation_id, widget_id, False)
        if ok:
            self._fails.pop(key, None)
        else:
            self._fails[key] = self._fails.get(key, 0) + 1
            with contextlib.suppress(BoardError):
                await self._boards.refresh_failed(conversation_id, widget)
        return True

    async def _run(
        self, conversation_id: str, widget: Mapping[str, Any], provider: ModelProvider
    ) -> bool:
        self._count += 1
        turn = TURN_BASE + self._count
        results: list[tuple[str, str]] = []
        for i, read in enumerate(widget["live"]["reads"]):
            name, args = read.get("tool"), read.get("args")
            # Checked again: what may be replayed is decided now, not when it was saved.
            if not isinstance(name, str) or not isinstance(args, Mapping):
                return False
            if not live.eligible(self._definition(name)):
                return False
            ctx = ToolContext(
                owner_id=self._owner,
                session_id=conversation_id,
                turn=turn,
                call_id=f"refresh-{self._count}-{i}",
                via="refresh",
                granted_roots=self._roots(),
            )
            r = await self._router.dispatch(ToolCall(name=name, args=dict(args)), ctx)
            if r.status != "succeeded":
                return False
            results.append((name, r.content))
        answer, usage = await fill.fill(provider, widget, results)
        try:
            # Recorded even if this task is stopped meanwhile: the call was paid for.
            await asyncio.shield(self._spent(conversation_id, provider, usage))
        except Exception as e:
            logger.warning("refresh usage not recorded: %s", type(e).__name__)
        if answer is None:
            return False
        try:
            after = fill.merge(widget, answer)
        except BoardError:
            return False
        try:
            await self._boards.refreshed(conversation_id, widget, after)
        except BoardError:
            return False
        # Changed meanwhile is not a failure: she, or the user, has the last word.
        return True

    async def _tell_safely(self, conversation_id: str, widget_id: str, on: bool) -> None:
        try:
            await self._tell(conversation_id, widget_id, on)
        except Exception as e:
            logger.warning("board.refreshing not sent: %s", type(e).__name__)
