"""She names a conversation from its first exchange (SUR-1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from tests.brain.fakes import USAGE, ScriptedProvider, say
from trillion.brain import title
from trillion.brain.types import ModelEvent, Msg, TextBlock, Usage


def first(user: str, her: str) -> list[Msg]:
    return [
        Msg(role="user", blocks=(TextBlock(text=user),)),
        Msg(role="assistant", blocks=(TextBlock(text=her),)),
    ]


def test_clean_keeps_a_plain_title() -> None:
    assert title.clean('"Planning the trip."\n') == "Planning the trip"
    assert title.clean("Title: Moving the dentist") == "Moving the dentist"
    assert title.clean('"Moving the dentist".') == "Moving the dentist"
    assert title.clean("**Moving the dentist**") == "Moving the dentist"
    assert title.clean("_Moving the dentist_.") == "Moving the dentist"


def test_clean_refuses_empty_and_long() -> None:
    assert title.clean("   ") is None
    assert title.clean("x" * 81) is None
    assert title.clean("one two three four five six seven eight nine ten eleven") is None


async def test_name_asks_without_tools_and_wraps_the_conversation() -> None:
    model = ScriptedProvider(say("Moving the dentist"), names=True)
    named, usage = await title.name(
        model, first("move the dentist to monday", "Done, Monday at 3.")
    )
    assert named == "Moving the dentist"
    assert usage == USAGE
    _, messages = model.calls[0]
    body = messages[0].blocks[0]
    assert isinstance(body, TextBlock)
    assert "<untrusted_tool_result" in body.text and "move the dentist" in body.text
    assert model.tool_calls[0] == []  # no tools offered
    assert model.max_tokens == [title.TITLE_TOKENS] == [24]


async def test_a_failure_or_timeout_gives_none() -> None:
    down = ScriptedProvider(RuntimeError("down"), names=True)
    assert (await title.name(down, first("a", "b")))[0] is None

    class Slow(ScriptedProvider):
        async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:  # type: ignore[override]
            await asyncio.sleep(60)
            yield  # type: ignore[misc]

    assert (await title.name(Slow(names=True), first("a", "b"), timeout_s=0.01))[0] is None


async def test_an_unusable_answer_still_reports_its_usage() -> None:
    named, usage = await title.name(ScriptedProvider(say("x" * 200), names=True), first("a", "b"))
    assert named is None
    assert usage == USAGE


async def test_the_fake_skips_naming_calls_unless_asked() -> None:
    model = ScriptedProvider(say("Reply"))
    named, usage = await title.name(model, first("a", "b"))
    assert named is None and usage == Usage()
    assert model.calls == [] and model.naming_calls == 1  # the script is untouched
