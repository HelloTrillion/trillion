"""A draft (VOI-12): the model's first call, started before the user has finished."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from tests.brain.fakes import USAGE, ScriptedProvider, say, use
from trillion.api.draft import Draft, same_words
from trillion.brain.types import Done, ModelEvent, Msg, ProviderError, TextBlock, TextDelta, ToolUse


def _msg(text: str) -> Msg:
    return Msg(role="user", blocks=(TextBlock(text=text),))


def _draft(provider: ScriptedProvider) -> Draft:
    return Draft.start(
        provider=provider,
        system=[],
        messages=[_msg("hi")],
        tools=[],
        words="hi",
        built=None,
        history_len=0,
        summary=None,
    )


class Gated(ScriptedProvider):
    """Sends "Half", then waits for the gate before finishing."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.closed = False

    async def stream(self, **kw: object) -> AsyncIterator[ModelEvent]:  # type: ignore[override]
        try:
            yield TextDelta(text="Half")
            await self.gate.wait()
            yield Done(stop_reason="end", usage=USAGE)
        finally:
            self.closed = True


def test_same_words_ignores_case_spacing_and_punctuation() -> None:
    assert same_words("Call the dentist.", "call  the dentist")
    assert not same_words("call the dentist", "call the dentist tomorrow")


async def test_a_draft_replays_what_it_recorded_after_it_finished() -> None:
    d = _draft(ScriptedProvider(say("Hello there.")))
    await d.settled()
    got = [ev async for ev in d.replay()]
    assert [type(e) for e in got] == [TextDelta, Done]
    assert d.usage() == USAGE and d.finished and not d.failed


async def test_a_draft_replays_what_it_recorded_then_what_is_still_coming() -> None:
    p = Gated()
    d = _draft(p)
    got: list[ModelEvent] = []

    async def read() -> None:
        async for ev in d.replay():
            got.append(ev)

    reader = asyncio.create_task(read())
    for _ in range(5):
        await asyncio.sleep(0)
    # The recorded event is out already; the rest has not arrived.
    assert got == [TextDelta(text="Half")] and not reader.done()
    p.gate.set()
    await reader
    assert [type(e) for e in got] == [TextDelta, Done]


async def test_a_replay_started_before_anything_arrives_gets_everything() -> None:
    d = _draft(ScriptedProvider(say("Hi.")))
    # No yield to the loop first: the draft has not begun when the replay does.
    got = [ev async for ev in d.replay()]
    assert [type(e) for e in got] == [TextDelta, Done]


async def test_a_draft_that_reaches_a_tool_call_ends_there_without_running_it() -> None:
    d = _draft(ScriptedProvider(use("note_read", {})))
    got = [ev async for ev in d.replay()]
    assert isinstance(got[0], ToolUse) and isinstance(got[-1], Done)


async def test_a_cancelled_draft_stops_and_keeps_its_usage_so_far() -> None:
    p = Gated()
    d = _draft(p)
    await asyncio.sleep(0)
    await d.cancel()
    await d.cancel()  # a second cancel is harmless
    assert d.cancelled and d.finished and p.closed
    # Usage comes only with Done, so a draft stopped before it has spent nothing known.
    assert d.usage().output_tokens == 0


async def test_a_failed_draft_says_so_and_its_replay_fails_the_same_way() -> None:
    d = _draft(ScriptedProvider(ProviderError("unreachable")))
    await d.settled()
    assert d.failed
    with pytest.raises(ProviderError) as e:
        async for _ in d.replay():
            pass
    assert e.value.kind == "unreachable"


async def test_a_draft_that_fails_midway_replays_its_text_then_the_failure() -> None:
    class Dies(ScriptedProvider):
        async def stream(self, **kw: object) -> AsyncIterator[ModelEvent]:  # type: ignore[override]
            yield TextDelta(text="So far")
            raise ProviderError("rate_limited")

    d = _draft(Dies())
    got: list[ModelEvent] = []
    with pytest.raises(ProviderError) as e:
        async for ev in d.replay():
            got.append(ev)
    assert got == [TextDelta(text="So far")] and e.value.kind == "rate_limited"


async def test_settled_never_raises_even_on_an_unexpected_error() -> None:
    d = _draft(ScriptedProvider(RuntimeError("bug")))
    await d.settled()
    assert d.failed


async def test_closing_the_replay_early_stops_the_draft() -> None:
    p = Gated()
    d = _draft(p)
    replay = d.replay()
    assert await anext(replay) == TextDelta(text="Half")
    await replay.aclose()
    await d.settled()
    assert p.closed and d.finished


async def test_cancelling_the_turn_that_replays_stops_the_draft() -> None:
    p = Gated()
    d = _draft(p)

    async def read() -> None:
        async for _ in d.replay():
            pass

    reader = asyncio.create_task(read())
    for _ in range(5):
        await asyncio.sleep(0)
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader
    await d.settled()
    assert p.closed and d.finished


async def test_a_draft_has_only_one_replay() -> None:
    d = _draft(ScriptedProvider(say("Hi.")))
    [ev async for ev in d.replay()]
    with pytest.raises(RuntimeError):
        async for _ in d.replay():
            pass


async def test_a_draft_cancelled_before_it_began_is_over_and_its_replay_ends() -> None:
    d = _draft(ScriptedProvider(say("Hi.")))
    await d.cancel()
    assert d.cancelled and d.finished
    with pytest.raises(ProviderError) as e:
        async for _ in d.replay():
            pass
    assert e.value.kind == "draft_cancelled"


async def test_waiting_for_a_draft_can_itself_be_cancelled() -> None:
    p = Gated()
    d = _draft(p)
    waiter = asyncio.create_task(d.settled())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    # The draft itself carries on.
    assert not d.finished
    p.gate.set()
    await d.settled()
    assert d.finished and not d.failed
