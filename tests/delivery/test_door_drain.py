"""The door's drain at a stop: bounded, and never fooled by a finished notification still
in its set because the loop shut down before that one's done-callback ran."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any, cast

from tests.scheduler.conftest import FakeClock
from trillion.delivery.door import Door

# Far past a drain's own bound: a drain still running then never ends.
GUARD_S = 10.0


def door() -> Door:
    # The drain touches neither the store nor the pages.
    return Door(cast(Any, None), [], clock=FakeClock(0), owner="local")


def ends(run: Callable[[], Coroutine[Any, Any, None]]) -> float:
    """Run `run` on its own loop in another thread, and how long it took. A drain that
    spins can't be cancelled from its own loop, so it is watched from outside."""
    took: list[float] = []

    def main() -> None:
        t0 = time.monotonic()
        asyncio.run(run())
        took.append(time.monotonic() - t0)

    worker = threading.Thread(target=main, daemon=True)
    worker.start()
    worker.join(GUARD_S)
    assert not worker.is_alive(), "drain did not end"
    assert took, "drain raised"
    return took[0]


def test_a_finished_one_left_in_the_set_does_not_hang_the_drain() -> None:
    async def run() -> None:
        d = door()
        stale = asyncio.get_running_loop().create_future()
        stale.set_result(None)
        # Done, but its discard never ran: the loop shut down first.
        d._sending.add(stale)
        await d.drain()
        assert not d._sending

    assert ends(run) < 1.0


def test_one_that_never_finishes_is_cancelled_after_the_bound() -> None:
    cancelled: list[bool] = []

    async def run() -> None:
        d = door()

        async def stuck() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        task = asyncio.ensure_future(stuck())
        d._sending.add(task)
        task.add_done_callback(d._sending.discard)
        await asyncio.sleep(0)
        await d.drain(timeout_s=0.2)
        assert task.cancelled()
        assert not d._sending

    assert ends(run) < 2.0
    assert cancelled == [True]


async def test_it_waits_for_those_still_on_their_way_and_for_ones_added_meanwhile() -> None:
    d = door()
    done: list[str] = []

    async def later(name: str, s: float, then: Callable[[], None] | None = None) -> None:
        await asyncio.sleep(s)
        done.append(name)
        if then is not None:
            then()

    def add(name: str) -> None:
        t = asyncio.ensure_future(later(name, 0.01))
        d._sending.add(t)
        t.add_done_callback(d._sending.discard)

    first = asyncio.ensure_future(later("first", 0.01, lambda: add("second")))
    d._sending.add(first)
    first.add_done_callback(d._sending.discard)
    await d.drain()
    assert done == ["first", "second"]
    assert not d._sending
