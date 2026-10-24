"""The session's own guarantees, driven directly: turn counting, one turn at a time,
one session per conversation. These came from an independent review of the first server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.title import TITLE_TOKENS
from trillion.brain.types import ModelEvent
from trillion.secrets.store import MemoryStore


class Gated(ScriptedProvider):
    """A scripted model that can be held before chosen calls, and counts overlap."""

    def __init__(self, *replies: list[ModelEvent]) -> None:
        super().__init__(*replies)
        self.gates: dict[int, asyncio.Event] = {}
        self.running = 0
        self.most_at_once = 0

    def hold(self, call: int) -> asyncio.Event:
        self.gates[call] = asyncio.Event()
        return self.gates[call]

    async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:
        if kw["max_tokens"] == TITLE_TOKENS:  # naming is not a turn: no gate, no count
            async for ev in super().stream(**kw):
                yield ev
            return
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            gate = self.gates.get(len(self.calls))
            if gate is not None:
                await gate.wait()
            async for ev in super().stream(**kw):
                yield ev
        finally:
            self.running -= 1


class Page:
    """What the browser would receive. Like the real page, it reports each card as shown
    once it arrives, which is what lets the card be approved (#4)."""

    def __init__(self, rt: Runtime | None = None) -> None:
        self.got: list[dict[str, Any]] = []
        self._rt = rt

    async def send(self, body: dict[str, Any]) -> None:
        self.got.append(body)
        if body.get("type") == "confirm.request" and self._rt is not None:
            await self._rt.router.mark_delivered(str(body["pending_id"]), "local", "web")

    def all(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.got if m["type"] == kind]

    async def until(self, kind: str, count: int = 1) -> None:
        for _ in range(400):
            if len(self.all(kind)) >= count:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"never saw {count} x {kind}: {[m['type'] for m in self.got]}")


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={}
    )
    yield runtime
    await runtime.close()


def create(rt: Runtime, name: str, call_id: str) -> list[ModelEvent]:
    path = str(rt.granted_roots[0] / name)
    return use("file_create", {"path": path, "content": "x"}, call_id)


async def idle(session: Session) -> None:
    for _ in range(400):
        if session._task is None:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the session never came to rest")


async def test_a_tap_mid_turn_cannot_unlock_a_self_confirmation(rt: Runtime) -> None:
    """The model proposes two writes. The user declines one while it is still working.
    The model then tries to confirm the other, in that turn and in the one the tap's
    note gives it. Neither may work: nobody said yes."""
    a, b = rt.granted_roots[0] / "a.txt", rt.granted_roots[0] / "b.txt"
    provider = Gated(create(rt, "a.txt", "c1"), create(rt, "b.txt", "c2"))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    held = provider.hold(2)

    await session.handle({"type": "turn.text", "text": "write both"})
    await page.until("confirm.request", 2)
    card_a, card_b = (c["pending_id"] for c in page.all("confirm.request"))
    provider._replies += [
        use("confirm_pending_action", {"pending_id": card_a}, "c3"),
        say("Shall I?"),
        use("confirm_pending_action", {"pending_id": card_a}, "c4"),  # the tap's turn
        say("Still waiting."),
    ]

    await session.handle({"type": "cancel", "pending_id": card_b})
    await session.handle({"type": "confirm", "pending_id": "no-such-card"})
    held.set()
    await idle(session)

    confirms = [m["summary"] for m in page.all("tool.end") if m["call_id"] in ("c3", "c4")]
    assert confirms == ["same_turn", "same_turn"]
    assert not a.exists() and not b.exists()

    # Once the user actually says yes, it goes through.
    provider._replies += [
        use("confirm_pending_action", {"pending_id": card_a}, "c5"),
        say("Done."),
    ]
    await session.handle({"type": "turn.text", "text": "yes, write a"})
    await idle(session)
    assert a.read_text(encoding="utf-8") == "x" and not b.exists()
    await session.close()


async def test_a_tap_still_approves_its_own_card_at_once(rt: Runtime) -> None:
    provider = Gated(create(rt, "a.txt", "c1"), say("Shall I?"), say("Done."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a"})
    await idle(session)
    await session.handle(
        {"type": "confirm", "pending_id": page.all("confirm.request")[0]["pending_id"]}
    )
    await idle(session)
    assert page.all("confirm.resolved")[0]["status"] == "succeeded"
    assert (rt.granted_roots[0] / "a.txt").exists()
    await session.close()


async def test_the_turn_is_the_same_after_a_reload(rt: Runtime) -> None:
    provider = Gated(create(rt, "a.txt", "c1"), say("Shall I?"), say("Declined."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a"})
    await idle(session)
    await session.handle(
        {"type": "cancel", "pending_id": page.all("confirm.request")[0]["pending_id"]}
    )
    await idle(session)
    before = session._turn
    await session.close()
    again = await Session.start(rt, Page(rt).send, session.id)
    assert again._turn == before == 1  # taps are not counted, live or reloaded
    await again.close()


async def test_two_turns_never_run_at_once(rt: Runtime) -> None:
    """A tap's note is queued mid-turn; a message arrives just as the turn ends."""
    provider = Gated(create(rt, "a.txt", "c1"), say("Shall I?"), say("Declined."), say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    held = provider.hold(1)
    await session.handle({"type": "turn.text", "text": "write a"})
    await page.until("confirm.request")
    await session.handle(
        {"type": "cancel", "pending_id": page.all("confirm.request")[0]["pending_id"]}
    )
    held.set()
    for _ in range(200):  # keep sending while the first turn ends and the note's turn starts
        await session.handle({"type": "turn.text", "text": "hello?"})
        await asyncio.sleep(0)
        if session._task is None:
            break
    await idle(session)
    assert provider.most_at_once == 1
    kinds = [k for k, _ in await rt.conversations.history(session.id, "local")]
    # Every assistant turn is answered in order: no two replies back to back.
    assert not any(x == y == "assistant" for x, y in pairwise(kinds))
    await session.close()


async def test_opening_a_conversation_elsewhere_ends_the_first_session(rt: Runtime) -> None:
    provider = Gated(create(rt, "a.txt", "c1"), say("Shall I?"), say("unreached"))
    rt.provider = provider
    first_page, second_page = Page(rt), Page(rt)
    first = await Session.start(rt, first_page.send, None)
    held = provider.hold(1)
    await first.handle({"type": "turn.text", "text": "write a"})
    await first_page.until("confirm.request")  # mid-turn: the tool call is recorded

    second = await Session.start(rt, second_page.send, first.id)
    held.set()
    assert first_page.all("error")[-1] == {"type": "error", "code": "replaced"}
    await first.handle({"type": "turn.text", "text": "ignored"})  # the old tab is deaf now
    # The second session read a history in which every tool call has its answer.
    kinds = [k for k, _ in await rt.conversations.history(first.id, "local")]
    assert kinds[:3] == ["user", "assistant", "tool_results"]
    assert len(second._history) == len(kinds)
    assert rt.by_conversation[first.id] is second
    await second.close()
    assert first.id not in rt.by_conversation


async def test_a_built_message_is_saved_byte_for_byte(rt: Runtime) -> None:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    built = await session._build_input("user", "hello there")
    assert session._history == []  # building saves nothing
    await session._commit_input("user", built)
    assert session._history[-1] == built.msg
    await session.close()
