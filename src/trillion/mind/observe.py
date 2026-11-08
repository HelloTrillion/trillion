"""The bus the Living Mind's pulses travel on (GAL-10, the spec's decision 7).

Whatever she does that the map shows (a tool, a turn, a memory, a helper's run, a
critical notice) calls `publish(kind, **ids)`. Each page watching through `/ws/observe`
holds a `Subscription`: a bounded queue that drops its oldest event when the page is
slow, so a stalled tab can never slow her. Publishing never blocks and never raises.

Only the kinds and fields in `EVENTS` pass, and only as ids, names and flags: a
field not listed, or a value that is not a short string, a number, a flag or a short
list of strings, is dropped. A tool's arguments or result, a memory's text and a
message's words cannot ride here even by mistake (SEC-4).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Each kind and the fields it may carry.
EVENTS: dict[str, frozenset[str]] = {
    "turn": frozenset({"conversation_id"}),
    "tool.start": frozenset({"name"}),
    "tool.end": frozenset({"name", "ok"}),
    "memory.written": frozenset({"id"}),
    "memory.recalled": frozenset({"ids"}),
    "helper.started": frozenset({"id"}),
    "helper.done": frozenset({"id", "ok"}),
    "alert": frozenset({"critical"}),
}
# Events a slow page may fall behind by before its oldest are dropped.
QUEUE_SIZE = 256
# Longer than any id or tool name; a value longer than this is no id.
MAX_ID = 200
MAX_IDS = 50

# What a source of events is given: `Observer.publish`, or nothing.
Publish = Callable[..., None]


def _clean(value: Any) -> Any:
    """An id, a name, a flag or a count as it is; anything else None (dropped)."""
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_ID else None
    if isinstance(value, list | tuple | frozenset | set):
        items = [v for v in value if isinstance(v, str) and len(v) <= MAX_ID]
        return sorted(items)[:MAX_IDS] if isinstance(value, set | frozenset) else items[:MAX_IDS]
    return None


class Subscription:
    """One page's queue. `get` waits for the next event; the oldest is dropped when full."""

    def __init__(self, observer: Observer, size: int) -> None:
        self._observer = observer
        self._queue: deque[dict[str, Any]] = deque(maxlen=size)
        self._ready = asyncio.Event()
        self.dropped = 0

    def put(self, event: dict[str, Any]) -> None:
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
        self._queue.append(event)
        self._ready.set()

    async def get(self) -> dict[str, Any]:
        while not self._queue:
            self._ready.clear()
            await self._ready.wait()
        return self._queue.popleft()

    def pending(self) -> int:
        return len(self._queue)

    def close(self) -> None:
        self._observer.unsubscribe(self)

    def __enter__(self) -> Subscription:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class Observer:
    def __init__(self, *, size: int = QUEUE_SIZE) -> None:
        self._size = size
        self._subscribers: set[Subscription] = set()

    @property
    def watched(self) -> bool:
        return bool(self._subscribers)

    def subscribe(self) -> Subscription:
        s = Subscription(self, self._size)
        self._subscribers.add(s)
        return s

    def unsubscribe(self, s: Subscription) -> None:
        self._subscribers.discard(s)

    def publish(self, kind: str, **ids: Any) -> None:
        """Tell every page watching. Call it on the event loop; it never blocks or raises."""
        if not self._subscribers:
            return
        try:
            allowed = EVENTS.get(kind)
            if allowed is None:
                logger.warning("an unknown pulse was not sent: %s", kind)
                return
            event: dict[str, Any] = {"type": kind}
            for name, value in ids.items():
                cleaned = _clean(value)
                if name in allowed and cleaned is not None:
                    event[name] = cleaned
            for s in list(self._subscribers):
                s.put(event)
        except Exception as e:
            logger.warning("a pulse was not sent: %s", type(e).__name__)
