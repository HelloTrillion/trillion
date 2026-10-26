"""Live widgets refresh while the board is seen (the live widgets spec): due only while
seen, a catch-up on seen, one in flight, 60 an hour, three failures then quiet until
the board is opened anew, waiting for her turn, the user's switch, and nothing added
to the conversation but its usage."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page
from tests.brain.fakes import USAGE
from tests.workspace.test_live import KPIS, mcp_tool
from trillion.api.runtime import OWNER, Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import (
    Done,
    ModelEvent,
    Msg,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolSchema,
)
from trillion.connectors.mcp.tool import Outcome
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import JSON, Risk
from trillion.workspace.fill import INSTRUCTIONS
from trillion.workspace.refresh import MAX_FAILS, PER_HOUR
from trillion.workspace.service import for_page

MINUTE = 60_000
T0 = 1_790_000_000_000


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> int:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += int(minutes * MINUTE)


class Filler:
    """Answers every fill-in with the next value, or with what it is told to."""

    name = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.n = 0
        self.answer: str | None = None
        self.calls: list[tuple[Sequence[SystemBlock], list[Msg], int]] = []

    def pricing(self) -> None:
        return None

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        assert system[0].text == INSTRUCTIONS
        self.calls.append((system, list(messages), len(tools)))
        self.n += 1
        text = self.answer
        if text is None:
            metrics = [
                {"label": "MRR", "value": f"${5000 + self.n}"},
                {"label": "New", "value": "3"},
            ]
            text = json.dumps({"type": "kpis", "metrics": metrics})
        yield TextDelta(text=text)
        yield Done(stop_reason="end", usage=USAGE)


class World:
    def __init__(self, rt: Runtime, s: Session, page: Page, clock: Clock, filler: Filler) -> None:
        self.rt, self.s, self.page, self.clock, self.filler = rt, s, page, clock, filler
        self.called: list[tuple[str, JSON]] = []

    @property
    def refresher(self) -> Any:
        assert self.rt.refresher is not None
        return self.rt.refresher

    async def board(self) -> dict[str, Any]:
        board = await self.rt.board_store.get(self.s.id, OWNER)
        assert board is not None
        return board

    async def value(self, i: int = 0) -> str:
        return str((await self.board())["widgets"][i]["metrics"][0]["value"])

    async def step(self) -> int:
        ran: int = await self.refresher.step()
        return ran


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    rt = await open_runtime(persona=tmp_path, secrets=MemoryStore(), env={})
    clock = Clock()
    rt.clock = clock
    filler = Filler()
    rt.provider = filler  # type: ignore[assignment]
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    w = World(rt, s, page, clock, filler)

    async def call(name: str, args: JSON, timeout: float) -> Outcome:
        w.called.append((name, dict(args)))
        return Outcome(ok=True, text="MRR $5,000")

    for remote, risk in (("list_charges", Risk.READ), ("create_refund", Risk.WRITE)):
        rt.registry.register(mcp_tool(remote, risk=risk, call=call), module="trillion.mcp")
    rt.reads.record(s.id, 1, "mcp_stripe_list_charges", {"limit": 10}, "Stripe")
    assert rt.boards is not None
    await rt.boards.show(s.id, "Money", [KPIS], turn=1)
    rt.reads.end(s.id, 1)
    yield w
    await s.close()
    await rt.close()


async def test_nothing_refreshes_while_no_page_sees_the_board(world: World) -> None:
    world.clock.advance(60)
    assert await world.step() == 0
    assert world.called == [] and world.filler.n == 0


async def test_seen_catches_up_and_the_widget_is_refreshed_in_place(world: World) -> None:
    before = await world.board()
    history = await world.rt.conversations.history(world.s.id, OWNER)
    world.clock.advance(16)
    await world.s.handle({"type": "board.seen", "on": True})
    assert await world.step() == 1
    # The read replayed with the arguments it was called with.
    assert world.called == [("list_charges", {"limit": 10})]
    after = await world.board()
    (w,) = after["widgets"]
    assert w["id"] == "w1" and w["metrics"][0]["value"] == "$5001"
    assert w["title"] == "Revenue" and w["source"]["name"] == "Stripe"
    assert w["source"]["at"] != before["widgets"][0]["source"]["at"]
    assert w["live"] == before["widgets"][0]["live"]
    # The fill-in had no tools; the fresh result reached it wrapped.
    _, messages, tools = world.filler.calls[0]
    assert tools == 0
    text = messages[0].blocks[0]
    assert isinstance(text, TextBlock) and "<untrusted_tool_result" in text.text
    # The page: the pulse on and off, then the board.
    kinds = [m["type"] for m in world.page.got]
    on = kinds.index("board.refreshing")
    assert world.page.all("board.refreshing") == [
        {"type": "board.refreshing", "id": "w1", "on": True},
        {"type": "board.refreshing", "id": "w1", "on": False},
    ]
    assert "board" in kinds[on:]
    assert world.page.all("board")[-1]["board"]["widgets"][0]["live"]["state"] == "idle"
    # Nothing is added to the conversation; the call's usage is recorded with it.
    assert await world.rt.conversations.history(world.s.id, OWNER) == history
    conn = world.rt.conversations._conn
    async with conn.execute(
        "SELECT conversation_id, input_tokens FROM usage WHERE owner_id = ?", (OWNER,)
    ) as cur:
        rows = await cur.fetchall()
    assert [tuple(r) for r in rows] == [(world.s.id, USAGE.input_tokens)]


async def test_due_only_after_its_interval(world: World) -> None:
    world.refresher.seen(world.s.id, True)
    world.clock.advance(14)
    assert await world.step() == 0
    world.clock.advance(1)
    assert await world.step() == 1
    world.clock.advance(14)
    assert await world.step() == 0
    # Hidden: nothing, however late.
    await world.s.handle({"type": "board.seen", "on": False})
    world.clock.advance(120)
    assert await world.step() == 0


async def test_off_and_a_closed_board_are_never_refreshed(world: World) -> None:
    assert world.rt.boards is not None
    world.refresher.seen(world.s.id, True)
    await world.rt.boards.update(world.s.id, "w1", {**KPIS, "refresh": "off"}, turn=2)
    world.clock.advance(120)
    assert await world.step() == 0
    await world.rt.boards.update(world.s.id, "w1", KPIS, turn=3)
    await world.rt.boards.close(world.s.id)
    assert await world.step() == 0


async def test_board_refresh_is_now_and_ignored_while_in_flight(world: World) -> None:
    await world.s.handle({"type": "board.seen", "on": True})
    await world.s.handle({"type": "board.refresh", "id": "w1"})
    assert await world.step() == 1  # not due, but asked for
    world.refresher.in_flight = (world.s.id, "w1")
    world.refresher.now(world.s.id, "w1")
    world.refresher.in_flight = None
    assert await world.step() == 0
    await world.s.handle({"type": "board.refresh"})
    await world.s.handle({"type": "board.seen", "on": "yes"})
    assert [e["code"] for e in world.page.all("error")] == ["bad_message", "bad_message"]


async def test_one_in_flight_at_a_time(world: World) -> None:
    world.refresher.seen(world.s.id, True)
    world.clock.advance(16)
    gate = asyncio.Event()
    inside: list[int] = []
    real = world.filler.stream

    async def slow(**kw: Any) -> AsyncIterator[ModelEvent]:
        inside.append(1)
        await gate.wait()
        async for ev in real(**kw):
            yield ev

    world.filler.stream = slow  # type: ignore[method-assign]
    first = asyncio.create_task(world.step())
    second = asyncio.create_task(world.step())
    for _ in range(100):
        await asyncio.sleep(0)
    assert inside == [1] and world.refresher.in_flight == (world.s.id, "w1")
    gate.set()
    assert sorted(await asyncio.gather(first, second)) == [0, 1]
    assert inside == [1]


async def test_at_most_sixty_an_hour(world: World) -> None:
    world.refresher.seen(world.s.id, True)
    for _ in range(PER_HOUR):
        world.refresher.now(world.s.id, "w1")
        assert await world.step() == 1
        world.clock.advance(0.5)
    world.refresher.now(world.s.id, "w1")
    assert await world.step() == 0
    # A rolling hour: the first one falls out of it.
    world.clock.advance(30.5)
    assert await world.step() == 1


async def test_three_failures_stop_it_until_the_board_is_seen_again(world: World) -> None:
    world.refresher.seen(world.s.id, True)
    world.filler.answer = "not json"
    for _ in range(MAX_FAILS):
        world.clock.advance(15)
        assert await world.step() == 1
    board = await world.board()
    assert await world.value() == "$4,200"  # the old data stays
    assert board["widgets"][0]["live"]["failed"]["count"] == MAX_FAILS
    page = for_page(board)
    assert page is not None and page["widgets"][0]["live"]["state"] == "failed"
    world.clock.advance(60)
    assert await world.step() == 0
    # Opened anew: it tries again, and a success clears the failure.
    world.refresher.seen(world.s.id, False)
    world.refresher.seen(world.s.id, True)
    world.filler.answer = None
    assert await world.step() == 1
    w = (await world.board())["widgets"][0]
    assert "failed" not in w["live"] and w["metrics"][0]["value"] != "$4,200"


async def test_an_answer_the_kit_refuses_keeps_the_old_data(world: World) -> None:
    world.refresher.seen(world.s.id, True)
    world.filler.answer = json.dumps({"metrics": "lots"})
    world.clock.advance(15)
    assert await world.step() == 1
    assert await world.value() == "$4,200"
    assert (await world.board())["widgets"][0]["live"]["failed"]["count"] == 1


async def test_a_saved_read_that_may_not_be_replayed_is_a_failure(world: World) -> None:
    board = await world.board()
    board["widgets"][0]["live"]["reads"] = [
        {"tool": "mcp_stripe_create_refund", "args": {"charge": "ch_1"}}
    ]
    await world.rt.board_store.put(world.s.id, OWNER, board, world.clock())
    world.refresher.seen(world.s.id, True)
    world.clock.advance(15)
    assert await world.step() == 1
    assert world.called == [] and world.filler.n == 0
    assert (await world.board())["widgets"][0]["live"]["failed"]["count"] == 1


async def test_a_running_turn_makes_it_wait(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.refresher.seen(world.s.id, True)
    world.clock.advance(16)
    monkeypatch.setattr(world.s, "busy", lambda: True)
    assert world.rt.turn_running(world.s.id)
    assert await world.step() == 0
    monkeypatch.setattr(world.s, "busy", lambda: False)
    assert await world.step() == 1


async def test_the_switch_stops_it(world: World) -> None:
    world.refresher.seen(world.s.id, True)
    world.clock.advance(16)
    world.rt.widgets_fresh = False
    assert await world.step() == 0
    world.refresher.now(world.s.id, "w1")
    assert await world.step() == 0
    world.rt.widgets_fresh = True
    assert await world.step() == 1


async def test_a_closed_page_no_longer_sees_the_board(world: World) -> None:
    world.refresher.seen(world.s.id, True)
    await world.s.close()
    assert not world.refresher.is_seen(world.s.id)


async def test_the_task_runs_with_the_runtime_and_wakes_on_seen(world: World) -> None:
    world.refresher.tick_s = 3600
    world.clock.advance(16)
    world.rt.start_background()
    try:
        await asyncio.sleep(0.01)
        assert world.filler.n == 0
        await world.s.handle({"type": "board.seen", "on": True})
        for _ in range(200):
            if world.filler.n:
                break
            await asyncio.sleep(0.005)
        assert world.filler.n == 1
    finally:
        await world.rt.stop_background()
    assert not world.refresher.running
