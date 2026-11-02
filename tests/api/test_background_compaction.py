"""Compaction waits for her reply (BRN-5): the user never waits for a summary.

A history past the summary point is summarized in the background once her turn is
over, and put in place at the next pause between turns. A turn that starts while the
summary is being written goes ahead on the whole history. Only a history past the
ceiling, too long to send as it is, waits for its summary before the turn.
"""

# ruff: noqa: F811 - the fixture is shared with the session tests

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from tests.api.test_session import Page, idle, rt  # noqa: F401 - the fixture
from tests.brain.fakes import USAGE, ScriptedProvider, is_naming, say
from trillion.api.runtime import Runtime
from trillion.api.session import OWNER, Session
from trillion.brain import compact
from trillion.brain.types import Done, ModelEvent, TextDelta

NOTES = "Sam talked about the garden."
FILLER = "Tell me about the garden, the roses and the soil. " * 4


class Summarizing(ScriptedProvider):
    """The scripted model, plus notes whenever it is asked for them. `log` is every
    call in order, a turn's or a summary's; a summary can be held at `gate`."""

    def __init__(self, *replies: list[ModelEvent]) -> None:
        super().__init__(*replies)
        self.log: list[str] = []
        self.gate: asyncio.Event | None = None

    async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:
        if is_naming(kw["system"]):
            async for ev in super().stream(**kw):
                yield ev
            return
        if kw["system"][0].text.startswith("You are writing notes"):
            self.log.append("summary")
            if self.gate is not None:
                await self.gate.wait()
            yield TextDelta(text=NOTES)
            yield Done(stop_reason="end", usage=USAGE)
            return
        self.log.append("turn")
        async for ev in super().stream(**kw):
            yield ev


def sent_text(provider: Summarizing, call: int) -> str:
    _, messages = provider.calls[call]
    return "\n".join(str(getattr(b, "text", "")) for m in messages for b in m.blocks)


async def say_and_wait(session: Session, page: Page, text: str, count: int) -> None:
    await session.handle({"type": "turn.text", "text": text})
    await page.until("turn.complete", count)
    await idle(session)


async def settled(session: Session) -> None:
    """The background summary, if one is being written, is done."""
    for _ in range(400):
        task = session._compacting
        if task is None or task.done():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the summary never finished")


async def test_a_long_history_is_summarized_after_her_reply_not_before(rt: Runtime) -> None:
    provider = Summarizing(*[say(f"Answer {i}. Roses like sun.") for i in range(3)])
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await say_and_wait(session, page, FILLER, 1)
    # The second turn starts with the history already past the summary point.
    # Past the summary point on the next turn, and well short of the ceiling.
    rt.compact_at = compact.estimate(session._history) + 20
    rt.compact_keep = 1
    await say_and_wait(session, page, FILLER, 2)
    await settled(session)

    # Her reply was asked for first, on the whole history; the notes only after it.
    assert provider.log == ["turn", "turn", "summary"]
    assert compact.estimate(provider.calls[1][1]) > rt.compact_at
    assert "conversation_summary" not in sent_text(provider, 1)
    kinds = [m["type"] for m in page.got]
    assert kinds.index("notice") > [i for i, k in enumerate(kinds) if k == "turn.complete"][1]
    assert page.all("notice")[0]["code"] == "compacted"

    # The next turn is sent the notes in place of the oldest turn.
    await say_and_wait(session, page, "and the tulips?", 3)
    text = sent_text(provider, 2)
    assert NOTES in text and "Answer 0." not in text
    summary, _ = await rt.conversations.context(session.id, OWNER)
    assert summary == NOTES
    await session.close()


async def test_a_turn_while_the_summary_is_written_is_not_held(rt: Runtime) -> None:
    provider = Summarizing(*[say(f"Answer {i}. Roses like sun.") for i in range(4)])
    provider.gate = asyncio.Event()
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await say_and_wait(session, page, FILLER, 1)
    # Past the summary point on the next turn, and well short of the ceiling.
    rt.compact_at = compact.estimate(session._history) + 20
    rt.compact_keep = 1
    await say_and_wait(session, page, FILLER, 2)
    assert provider.log[-1] == "summary"  # being written, and held

    # The user carries on: her turn goes ahead on the whole history.
    await say_and_wait(session, page, "and the tulips?", 3)
    assert provider.log == ["turn", "turn", "summary", "turn"]
    text = sent_text(provider, 2)
    assert "Answer 0." in text and NOTES not in text
    assert not page.all("notice")

    # The notes arrive: put in place, and covering only what they were written from.
    provider.gate.set()
    await settled(session)
    await page.until("notice")
    await say_and_wait(session, page, "and the lilies?", 4)
    text = sent_text(provider, 3)
    assert NOTES in text and "Answer 0." not in text
    assert "and the tulips?" in text and "Answer 2." in text
    await session.close()


async def test_a_history_past_the_ceiling_waits_for_its_summary(rt: Runtime) -> None:
    provider = Summarizing(*[say(f"Answer {i}. Roses like sun.") for i in range(2)])
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await say_and_wait(session, page, FILLER, 1)
    # Far too long to send as it is: the summary comes first, as it always did.
    rt.compact_at, rt.compact_keep = 10, 1
    assert compact.estimate(session._history) > compact.ceiling(rt.compact_at)
    await say_and_wait(session, page, FILLER, 2)
    assert provider.log[:3] == ["turn", "summary", "turn"]
    assert NOTES in sent_text(provider, 1)
    await session.close()


async def test_closing_lets_go_of_a_summary_being_written(rt: Runtime) -> None:
    provider = Summarizing(*[say(f"Answer {i}.") for i in range(2)])
    provider.gate = asyncio.Event()
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await say_and_wait(session, page, FILLER, 1)
    rt.compact_at, rt.compact_keep = compact.estimate(session._history) + 20, 1
    await say_and_wait(session, page, FILLER, 2)
    task = session._compacting
    assert task is not None and not task.done()
    await session.close()
    assert task.cancelled()
    summary, _ = await rt.conversations.context(session.id, OWNER)
    assert summary is None
