"""Stand-in models for the browsing helper: each call is answered by a function of what
the model was sent, so a test can play a model that follows a page's instructions."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from trillion.brain.types import (
    Done,
    ModelEvent,
    Msg,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSchema,
    ToolUse,
    Usage,
)

USAGE = Usage(input_tokens=10, output_tokens=5)


def call(name: str, **args: Any) -> list[ModelEvent]:
    return [
        ToolUse(id=f"c{id(args)}", name=name, args=args),
        Done(stop_reason="tool_use", usage=USAGE),
    ]


def say(text: str) -> list[ModelEvent]:
    return [TextDelta(text=text), Done(stop_reason="end", usage=USAGE)]


def last_result(messages: Sequence[Msg]) -> str:
    for msg in reversed(messages):
        for block in msg.blocks:
            if isinstance(block, ToolResultBlock):
                return block.content if isinstance(block.content, str) else str(block.content)
            if isinstance(block, TextBlock) and msg.role == "user":
                return block.text
    return ""


def ref(text: str, pattern: str) -> str:
    # A tool result reaches the model as JSON: its quotes and line breaks escaped.
    text = text.replace("\\n", "\n").replace('\\"', '"')
    for line in text.splitlines():
        if re.search(pattern, line):
            m = re.search(r"\[ref=([a-z0-9]+)\]", line)
            if m:
                return m.group(1)
    return "e999"


class Steps:
    """A model that runs a list of steps, one per call. A step is either events, or a
    function of the last thing it was sent that returns events."""

    name = "stand-in"
    model = "stand-in-1"

    def __init__(
        self, *steps: list[ModelEvent] | Callable[[str], list[ModelEvent]], delay_s: float = 0.0
    ) -> None:
        self._steps = list(steps)
        self.tools: list[list[str]] = []
        self.calls = 0
        self._delay = delay_s

    def pricing(self) -> None:
        return None

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        quick: bool = False,
    ) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        self.tools.append([t.name for t in tools])
        if self._delay:
            await asyncio.sleep(self._delay)
        step = self._steps.pop(0) if self._steps else say("")
        events = step(last_result(messages)) if callable(step) else step
        for ev in events:
            yield ev


class Forever(Steps):
    """Reads the page on every call, never finishing."""

    def __init__(self, delay_s: float = 0.0) -> None:
        super().__init__(delay_s=delay_s)

    async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:  # type: ignore[override]
        self.calls += 1
        self.tools.append([t.name for t in kw["tools"]])
        if self._delay:
            await asyncio.sleep(self._delay)
        if not kw["tools"]:
            for ev in say("Found part of it at http://fixture.test/two.html."):
                yield ev
            return
        for ev in call("browser_read"):
            yield ev
