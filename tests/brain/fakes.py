from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from trillion.brain.title import TITLE_TOKENS
from trillion.brain.types import (
    Done,
    ModelEvent,
    Msg,
    Pricing,
    ProviderError,
    SystemBlock,
    TextDelta,
    ToolSchema,
    ToolUse,
    Usage,
)

USAGE = Usage(input_tokens=10, output_tokens=5)


def say(text: str) -> list[ModelEvent]:
    return [TextDelta(text=text), Done(stop_reason="end", usage=USAGE)]


def use(name: str, args: dict[str, Any], call_id: str = "c1", text: str = "") -> list[ModelEvent]:
    head: list[ModelEvent] = [TextDelta(text=text)] if text else []
    return [
        *head,
        ToolUse(id=call_id, name=name, args=args),
        Done(stop_reason="tool_use", usage=USAGE),
    ]


class ScriptedProvider:
    """Replays one scripted reply per model call and records what it was sent."""

    name = "fake"
    model = "fake-1"

    def __init__(
        self,
        *replies: list[ModelEvent] | Exception,
        pricing: Pricing | None = None,
        names: bool = False,
    ) -> None:
        self._names = names
        self.tool_calls: list[Sequence[ToolSchema]] = []
        self._replies = list(replies)
        self._pricing = pricing
        self.calls: list[tuple[Sequence[SystemBlock], list[Msg]]] = []

    def pricing(self) -> Pricing | None:
        return self._pricing

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        if max_tokens == TITLE_TOKENS and not self._names:
            # She names the conversation after her first reply; scripts are about turns.
            yield Done(stop_reason="end", usage=Usage())
            return
        self.calls.append((system, list(messages)))
        self.tool_calls.append(list(tools))
        if not self._replies:
            raise ProviderError("script_exhausted")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        for ev in reply:
            yield ev
