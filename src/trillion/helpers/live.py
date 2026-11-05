"""Live updates for the helpers panel (the helpers panel spec, Live updates).

Anything that changes a helper calls `changed()`: it only marks the list dirty. One
task waits `delay_s` (250 ms), builds the list once and sends it, so a burst of
changes is one push and pushes are at least `delay_s` apart. A change while it builds
is another push after that. A build or a send that fails is logged by its kind (never
its content) and tried again at the next change.

A card lapses with no change anyone makes: each build says when the earliest open one
expires, and a timer marks the list dirty then, so its dot clears on time.

With no page open (`wanted` says no) nothing is built: a page reads the list fresh
when it opens, and its opening marks the list dirty, so the lapse timer is set again
from that build.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

DELAY_S = 0.25

# The API body, and when the list next changes by itself (a card lapsing), if ever.
Built = tuple[dict[str, Any], int | None]


class Notifier:
    def __init__(
        self,
        build: Callable[[], Awaitable[Built]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        clock: Callable[[], int],
        delay_s: float = DELAY_S,
        wanted: Callable[[], bool] = lambda: True,
    ) -> None:
        self._build = build
        self._wanted = wanted
        self._send = send
        self._clock = clock
        self._delay_s = delay_s
        self._dirty = False
        self._stopped = False
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.TimerHandle | None = None

    def changed(self) -> None:
        """Something a helper shows changed. Safe anywhere on the event loop; outside
        one, or once stopped, it does nothing."""
        if self._stopped:
            return
        self._dirty = True
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        while self._dirty and not self._stopped:
            await asyncio.sleep(self._delay_s)
            self._dirty = False
            if not self._wanted():
                # Nobody to tell. The lapse timer, if set, stands: it only marks dirty.
                continue
            try:
                body, wake_ms = await self._build()
            except Exception as e:
                logger.warning("the helper list was not built: %s", type(e).__name__)
                continue
            self._arm(wake_ms)
            try:
                await self._send({"type": "helpers.changed", **body})
            except Exception as e:
                logger.warning("the helper list was not sent: %s", type(e).__name__)

    def _arm(self, wake_ms: int | None) -> None:
        if self._wake is not None:
            self._wake.cancel()
            self._wake = None
        if wake_ms is None or self._stopped:
            return
        wait_s = max(0.0, (wake_ms - self._clock()) / 1000)
        self._wake = asyncio.get_running_loop().call_later(wait_s, self.changed)

    async def stop(self) -> None:
        self._stopped = True
        if self._wake is not None:
            self._wake.cancel()
            self._wake = None
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
