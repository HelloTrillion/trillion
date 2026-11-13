"""Writes that go on beside a turn, owned by one runtime (the Seamless measures).

A turn never waits on one of these: it takes its moment when it happens, and the write
runs as a task on the runtime's own event loop. The runtime owns them, never the
module: each runtime drains its own before its database closes, and a write from one
runtime can never be waited on from another's loop.

Every wait here is bounded. `settle` looks at whether each task is done, not at
whether a done-callback has tidied it away yet, so it can neither spin nor wait
forever on a write that never finishes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Far longer than one small write takes; a write that has not finished by then won't.
SETTLE_S = 5.0


class Writes:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def start(self, what: str, write: Coroutine[Any, Any, None]) -> None:
        """Run `write` beside the caller. A failure is logged at debug, by class only."""
        if self._closed:
            # The runtime is closing: its database will not take another write.
            write.close()
            return

        async def run() -> None:
            try:
                await write
            except Exception as e:
                logger.debug("%s not recorded: %s", what, type(e).__name__)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _pending(self) -> list[asyncio.Task[None]]:
        return [t for t in self._tasks if not t.done()]

    async def settle(self, timeout_s: float = SETTLE_S) -> bool:
        """Wait, at most `timeout_s`, for the writes on their way. True if all finished."""
        pending = self._pending()
        if not pending:
            return True
        _, still = await asyncio.wait(pending, timeout=timeout_s)
        return not still

    async def close(self, timeout_s: float = SETTLE_S) -> None:
        """Take no more, let the ones on their way finish for a bounded time, then cancel
        whatever is left, so nothing outlives the runtime or waits on a closed database."""
        self._closed = True
        if not await self.settle(timeout_s):
            left = self._pending()
            logger.debug("%d measure write(s) cancelled at close", len(left))
            for t in left:
                t.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait(left, timeout=timeout_s)
        self._tasks.clear()
