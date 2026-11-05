"""The helpers panel's live updates: coalesced, retried at the next change, woken when
a card lapses, and stopped with the runtime."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import pytest

from trillion.helpers.live import Built, Notifier

DELAY = 0.02


def clock() -> int:
    return int(time.time() * 1000)


class World:
    def __init__(self) -> None:
        self.builds = 0
        self.sent: list[dict[str, Any]] = []
        self.boom = False
        self.wake_ms: int | None = None

    async def build(self) -> Built:
        self.builds += 1
        if self.boom:
            raise RuntimeError("secret helper title")
        return {"helpers": [], "needs_you": self.builds}, self.wake_ms

    async def send(self, body: dict[str, Any]) -> None:
        self.sent.append(body)


async def settle(n: float = 4) -> None:
    await asyncio.sleep(DELAY * n)


async def test_a_burst_of_changes_is_one_push() -> None:
    w = World()
    n = Notifier(w.build, w.send, clock=clock, delay_s=DELAY)
    for _ in range(10):
        n.changed()
    await settle()
    assert w.sent == [{"type": "helpers.changed", "helpers": [], "needs_you": 1}]
    n.changed()
    await settle()
    assert len(w.sent) == 2
    await n.stop()


async def test_a_change_while_building_is_pushed_after() -> None:
    w = World()
    n = Notifier(w.build, w.send, clock=clock, delay_s=DELAY)
    gate = asyncio.Event()
    build = w.build

    async def slow() -> Built:
        await gate.wait()
        return await build()

    n._build = slow
    n.changed()
    await settle(2)
    n.changed()  # while the first is building
    gate.set()
    await settle()
    assert [b["needs_you"] for b in w.sent] == [1, 2]
    await n.stop()


async def test_a_failed_build_is_logged_by_kind_and_tried_at_the_next_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w = World()
    w.boom = True
    n = Notifier(w.build, w.send, clock=clock, delay_s=DELAY)
    with caplog.at_level(logging.WARNING):
        n.changed()
        await settle()
    assert w.sent == [] and "RuntimeError" in caplog.text and "secret" not in caplog.text
    w.boom = False
    n.changed()
    await settle()
    assert len(w.sent) == 1
    await n.stop()


async def test_a_card_lapsing_wakes_it() -> None:
    w = World()
    w.wake_ms = clock() + 60
    n = Notifier(w.build, w.send, clock=clock, delay_s=DELAY)
    n.changed()
    await settle()
    assert len(w.sent) == 1
    w.wake_ms = None
    await asyncio.sleep(0.15)
    assert len(w.sent) == 2
    await asyncio.sleep(0.1)
    assert len(w.sent) == 2
    await n.stop()


async def test_once_stopped_nothing_is_pushed() -> None:
    w = World()
    w.wake_ms = clock() + 200
    n = Notifier(w.build, w.send, clock=clock, delay_s=DELAY)
    n.changed()
    await settle()
    await n.stop()
    n.changed()
    await asyncio.sleep(0.3)
    assert len(w.sent) == 1


def test_outside_a_loop_a_change_does_nothing() -> None:
    w = World()
    Notifier(w.build, w.send, clock=clock).changed()
    assert w.builds == 0
