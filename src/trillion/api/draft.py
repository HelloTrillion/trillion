"""A draft: her first model call, started while the user is probably finishing (VOI-12).

It only records what the model sends. Nothing it receives reaches the page, the
speaker, the history or a tool: a used draft is replayed into a confirmed turn,
which does all of that as usual, and a dropped one is simply let go.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from trillion.brain.loop import MAX_TOKENS
from trillion.brain.types import (
    Done,
    ModelEvent,
    ModelProvider,
    Msg,
    ProviderError,
    SystemBlock,
    ToolSchema,
    Usage,
)

logger = logging.getLogger(__name__)

_NOT_WORDS = re.compile(r"[^\w\s]")


def same_words(a: str, b: str) -> bool:
    """The same words, whatever the case, spacing or punctuation (VOI-12)."""

    def norm(s: str) -> list[str]:
        return _NOT_WORDS.sub("", s.lower()).split()

    return norm(a) == norm(b)


class Draft:
    """One early first call, recorded and held until the turn is real (VOI-12).

    Use `Draft.start`. A draft has one reader: the confirmed turn, through `replay()`.
    """

    def __init__(self, *, words: str, built: Any, history_len: int, summary: Any) -> None:
        self.words = words
        # The message the confirmed turn will save (the session's BuiltInput). Typed
        # loosely so this module does not import the session.
        self.built = built
        # What it was built against: if either has changed, the draft is not used.
        self.history_len = history_len
        self.summary = summary
        # The model it asked: a different one at the end means it is not used, and
        # what it spent is that model's.
        self.provider: ModelProvider | None = None
        self.failed = False
        self.cancelled = False
        self.finished = False
        self._events: list[ModelEvent] = []
        self._more = asyncio.Event()
        self._error: Exception | None = None
        self._task: asyncio.Task[None] | None = None
        self._replayed = False

    @classmethod
    def start(
        cls,
        *,
        provider: ModelProvider,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        words: str,
        built: Any,
        history_len: int,
        summary: Any,
    ) -> Draft:
        d = cls(words=words, built=built, history_len=history_len, summary=summary)
        d.provider = provider
        d._task = asyncio.create_task(d._run(provider, system, messages, tools))
        # On the task, not in _run: a task cancelled before it starts never runs _run.
        d._task.add_done_callback(lambda _: d._over())
        return d

    def _over(self) -> None:
        self.finished = True
        self._more.set()

    async def _run(
        self,
        provider: ModelProvider,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
    ) -> None:
        try:
            async for ev in provider.stream(
                system=system, messages=messages, tools=tools, max_tokens=MAX_TOKENS
            ):
                self._events.append(ev)
                self._more.set()
        except Exception as e:
            # Kept, not raised: nobody awaits a draft until a turn replays it, and the
            # replay fails the same way a live call would. Its kind only: a provider's
            # message can quote what was said.
            logger.warning("draft failed: %s", getattr(e, "kind", type(e).__name__))
            self.failed = True
            self._error = e

    async def replay(self) -> AsyncIterator[ModelEvent]:
        """Everything recorded, then the rest as it arrives, for the confirmed turn.

        Works whether the draft is still running or already over. A failed draft ends
        with the error it failed with, so the turn reports it as it would a live call.
        Only one replay is allowed. If the reader stops early or is cancelled, the
        draft is cancelled too: nobody else would ever read what it goes on to say.
        """
        if self._replayed:
            raise RuntimeError("a draft is replayed only once")
        self._replayed = True
        i = 0
        complete = False
        try:
            while True:
                if i < len(self._events):
                    i += 1
                    yield self._events[i - 1]
                    continue
                if self.finished:
                    break
                # Nothing awaits between the checks above and this wait, so no event
                # can slip in unseen.
                self._more.clear()
                await self._more.wait()
            complete = True
        finally:
            if not complete and self._task is not None and not self._task.done():
                # Not awaited: this may run while the generator is being closed.
                self.cancelled = True
                self._task.cancel()
        if self._error is not None:
            raise self._error
        if self.cancelled:
            # Cut short by its owner: what came is not the whole reply.
            raise ProviderError("draft_cancelled")

    async def settled(self) -> None:
        """Wait until the draft has stopped, however it stopped.

        The draft's own failure or cancellation is never raised here. Cancelling the
        caller still cancels the wait, and leaves the draft running.
        """
        if self._task is not None:
            await asyncio.wait([self._task])

    async def cancel(self) -> None:
        """Stop the draft and wait for it. Safe to call more than once."""
        if self._task is not None and not self._task.done():
            self.cancelled = True
            self._task.cancel()
        await self.settled()

    def usage(self) -> Usage:
        """What the model has reported spending so far.

        Providers report usage with `Done`, so a draft stopped before its end counts
        as nothing here even though it cost something.
        """
        total = Usage()
        for ev in self._events:
            if isinstance(ev, Done):
                total = total + ev.usage
        return total
