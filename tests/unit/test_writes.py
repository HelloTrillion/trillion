"""Writes beside a turn (`api/writes.py`): every wait is bounded, and closing drains or
cancels, so a write that never finishes fails a test in seconds rather than hanging."""

from __future__ import annotations

import asyncio
import time

from trillion.api.writes import Writes


async def test_finished_writes_settle_at_once() -> None:
    w, done = Writes(), []

    async def write() -> None:
        done.append(1)

    w.start("x", write())
    assert await w.settle(1.0) is True and done == [1]
    assert await w.settle(1.0) is True  # nothing on its way


async def test_a_failing_write_is_swallowed() -> None:
    w = Writes()

    async def write() -> None:
        raise RuntimeError("no")

    w.start("x", write())
    assert await w.settle(1.0) is True


async def test_a_write_that_never_finishes_gives_up_and_is_cancelled_at_close() -> None:
    w, never = Writes(), asyncio.Event()
    cancelled = []

    async def write() -> None:
        try:
            await never.wait()
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    started = time.monotonic()
    w.start("x", write())
    assert await w.settle(0.1) is False
    await asyncio.wait_for(w.close(timeout_s=0.1), 3.0)
    assert cancelled == [1] and await w.settle(0.1) is True
    assert time.monotonic() - started < 3.0


async def test_after_close_a_write_is_not_started() -> None:
    w, ran = Writes(), []

    async def write() -> None:
        ran.append(1)

    await w.close()
    w.start("x", write())
    await asyncio.sleep(0)
    assert ran == [] and await w.settle(0.1) is True
