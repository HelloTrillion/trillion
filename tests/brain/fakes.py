from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from trillion.brain.title import INSTRUCTIONS
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


def is_naming(system: Sequence[SystemBlock]) -> bool:
    """Whether this is her naming a conversation, which is not a turn."""
    return bool(system) and system[0].text == INSTRUCTIONS


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
        self.naming_calls = 0
        self.max_tokens: list[int] = []
        # Whether each call was a spoken turn's, asking for its first word sooner.
        self.quick: list[bool] = []
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
        quick: bool = False,
    ) -> AsyncIterator[ModelEvent]:
        if is_naming(system):
            self.naming_calls += 1
            if not self._names:
                # She names the conversation after her first reply; scripts are about turns.
                yield Done(stop_reason="end", usage=Usage())
                return
        self.max_tokens.append(max_tokens)
        self.quick.append(quick)
        self.calls.append((system, list(messages)))
        self.tool_calls.append(list(tools))
        if not self._replies:
            raise ProviderError("script_exhausted")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        for ev in reply:
            yield ev
