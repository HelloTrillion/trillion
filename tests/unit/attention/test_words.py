"""Her one line for a watcher's finds (D3): written by the model from untrusted data,
with no tools; anything wrong and there is no line."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from tests.brain.fakes import USAGE, ScriptedProvider, say
from trillion.attention.runner import Find
from trillion.attention.words import INSTRUCTIONS, MAX_CHARS, line
from trillion.brain.types import (
    ModelEvent,
    Msg,
    Pricing,
    ProviderError,
    SystemBlock,
    TextBlock,
    ToolSchema,
    Usage,
)

FIND = Find(
    key="meeting:e1:1",
    title="Planning with Sam",
    facts={"title": "Planning with Sam", "starts": "10:00", "with": ["Sam"]},
    fresh_until_ms=None,
    link=None,
)


async def test_one_line_from_the_finds() -> None:
    p = ScriptedProvider(say('  "Your 10:00 with Sam starts in five minutes."\n'))
    text, usage = await line(p, "meetings", [FIND], "")
    assert text == "Your 10:00 with Sam starts in five minutes."
    assert usage == USAGE
    ((system, messages),) = p.calls
    assert system[0].text == INSTRUCTIONS
    (block,) = messages[0].blocks
    assert isinstance(block, TextBlock)
    assert block.text.count("<untrusted_tool_result") == 1
    assert "Planning with Sam" in block.text and "10:00" in block.text
    assert p.tool_calls == [[]]


async def test_the_users_notes_ride_along_outside_the_untrusted_part() -> None:
    p = ScriptedProvider(say("Sam is waiting."))
    await line(p, "email", [FIND], "Only Sam matters.")
    ((_, messages),) = p.calls
    (block,) = messages[0].blocks
    assert isinstance(block, TextBlock)
    notes_at = block.text.index("Only Sam matters.")
    assert notes_at > block.text.index("</untrusted_tool_result>")


async def test_a_failure_empty_or_overlong_answer_is_no_line() -> None:
    assert (await line(ScriptedProvider(ProviderError("down")), "meetings", [FIND], ""))[0] is None
    assert (await line(ScriptedProvider(say("   ")), "meetings", [FIND], ""))[0] is None
    long = "word " * MAX_CHARS
    assert (await line(ScriptedProvider(say(long)), "meetings", [FIND], ""))[0] is None


class Slow:
    name = "slow"
    model = "slow-1"

    def pricing(self) -> Pricing | None:
        return None

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        await asyncio.sleep(3600)
        yield say("late")[0]


async def test_a_slow_model_is_no_line() -> None:
    text, usage = await line(Slow(), "meetings", [FIND], "", timeout_s=0.05)
    assert text is None and usage == Usage()
