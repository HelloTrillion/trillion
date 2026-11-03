"""A recognizer for tests: it hears whatever the test says it hears."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

from trillion.voice.types import Choice, Heard, VoiceError, Word


class FakeStream:
    def __init__(self, owner: FakeSTT) -> None:
        self._owner = owner
        self._events: asyncio.Queue[Heard | None] = asyncio.Queue()
        self.audio = bytearray()
        self.finished = False
        self.closed = False

    def say(
        self,
        text: str,
        *,
        final: bool = True,
        pause: bool = False,
        turn: Literal["maybe", "resumed", "done"] | None = None,
        words: tuple[Word, ...] = (),
        confidence: float | None = None,
    ) -> None:
        self._events.put_nowait(
            Heard(
                text=text, final=final, pause=pause, turn=turn, words=words, confidence=confidence
            )
        )

    async def send(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def finish(self) -> None:
        self.finished = True
        if self._owner.delay:
            await asyncio.sleep(self._owner.delay)
        for text in self._owner.on_finish:
            self.say(text)
        self._events.put_nowait(None)

    async def close(self) -> None:
        self.closed = True
        self._events.put_nowait(None)

    async def events(self) -> AsyncIterator[Heard]:
        while True:
            item = await self._events.get()
            if item is None:
                return
            yield item


class FakeSTT:
    name = "fake-ears"

    def __init__(
        self,
        *,
        streaming: bool = True,
        turns: bool = False,
        word_times: bool = False,
        fail: str | None = None,
    ) -> None:
        self.streaming = streaming
        self.turns = turns
        self.word_times = word_times
        self.fail = fail
        # What it reports once the audio stops, as a batch recognizer does.
        self.on_finish: list[str] = []
        self.delay = 0.0
        self.opening = asyncio.Event()
        self.opening.set()
        self.streams: list[FakeStream] = []
        self.choices_asked: list[str] = []

    @property
    def stream(self) -> FakeStream:
        return self.streams[-1]

    async def open(self, *, choice: str) -> FakeStream:
        self.choices_asked.append(choice)
        await self.opening.wait()
        if self.fail is not None:
            raise VoiceError(self.fail)
        self.streams.append(FakeStream(self))
        return self.streams[-1]

    async def choices(self) -> list[Choice]:
        return [Choice(id="one", name="One")]

    async def prepare(self, choice: str) -> None:
        return None
