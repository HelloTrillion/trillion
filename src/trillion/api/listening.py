"""One stretch of listening: from the microphone opening to the words she is given.

The recognizer turns audio into segments; this decides when the user has
finished and assembles what they said (VOI-15a). Nothing heard is ever logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from trillion.voice.endpoint import NOTHING_AFTER_S, EndOfSpeech, Verdict, sounds_finished
from trillion.voice.types import STTProvider, STTStream, VoiceError

logger = logging.getLogger(__name__)

# How long the recognizer gets to hand over its last words once the audio stops.
STREAMING_WRAP_UP_S = 3.0
BATCH_WRAP_UP_S = 60.0
# Audio that arrives while the recognizer is still connecting is kept, so the first
# syllable is never clipped (VOI-2). Bounded: ten seconds of 16 kHz PCM16.
_MAX_BACKLOG = 16_000 * 2 * 10
# The window that reopens after she speaks waits this long for a first word (VOI-8).
FOLLOW_UP_S = 3.0


@dataclass(frozen=True, kw_only=True)
class Utterance:
    text: str
    # The seam for knowing who spoke (VOI-15). Nothing fills it yet.
    speaker: tuple[float, ...] | None = None


def describe(
    provider: STTProvider | None, kind: str | None, choice: str | None
) -> dict[str, Any] | None:
    """What the page needs to know about listening: that it can, and how words arrive."""
    if provider is None or kind is None:
        return None
    return {"kind": kind, "choice": choice, "streaming": provider.streaming}


class Listener:
    def __init__(
        self,
        provider: STTProvider,
        choice: str,
        *,
        on_partial: Callable[[str], Awaitable[None]],
        patience_s: float = NOTHING_AFTER_S,
    ) -> None:
        self._provider = provider
        self._choice = choice
        self._on_partial = on_partial
        self._rules = EndOfSpeech(streaming=provider.streaming, patience_s=patience_s)
        self._stream: STTStream | None = None
        self._reader: asyncio.Task[None] | None = None
        self._backlog = bytearray()
        self._finals: list[str] = []
        self._interim = ""
        self._error: VoiceError | None = None
        self._verdict: Verdict | None = None

    @property
    def open(self) -> bool:
        return self._stream is not None

    @property
    def verdict(self) -> Verdict | None:
        return self._verdict

    @property
    def quiet_s(self) -> float:
        """How long the user has been quiet, in the audio received so far."""
        return self._rules.quiet_s

    def text(self) -> str:
        return " ".join(p for p in (*self._finals, self._interim) if p).strip()

    async def start(self) -> None:
        """Connect to the recognizer. Raises VoiceError; audio sent meanwhile is kept."""
        stream = await self._provider.open(choice=self._choice)
        self._stream = stream
        self._reader = asyncio.create_task(self._read(stream))
        if self._backlog:
            held, self._backlog = bytes(self._backlog), bytearray()
            await self._feed(held)

    async def audio(self, pcm: bytes) -> Verdict | None:
        """More from the microphone. Returns a verdict once, when listening should end."""
        if self._verdict is not None:
            return None
        if self._error is not None:
            raise self._error
        if self._stream is None:
            if len(self._backlog) + len(pcm) <= _MAX_BACKLOG:
                self._backlog.extend(pcm)
            return None
        return await self._feed(pcm)

    async def _feed(self, pcm: bytes) -> Verdict | None:
        assert self._stream is not None  # noqa: S101 - narrowed by the callers
        await self._stream.send(pcm)
        self._verdict = self._rules.audio(pcm)
        return self._verdict

    async def _read(self, stream: STTStream) -> None:
        try:
            async for heard in stream.events():
                if heard.final:
                    if heard.text:
                        self._finals.append(heard.text)
                    self._interim = ""
                else:
                    self._interim = heard.text
                # Ended quickly only when the recognizer heard a pause and the whole
                # message so far reads as finished; otherwise the patient rules apply.
                done = heard.pause and sounds_finished(self.text())
                self._rules.heard(final=heard.final, has_text=bool(heard.text), done=done)
                if self._provider.streaming and (heard.text or heard.final):
                    await self._on_partial(self.text())
        except VoiceError as e:
            self._error = e
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The class name only: a recognizer's message can quote what it heard.
            logger.error("recognizer %s failed: %s", self._provider.name, type(e).__name__)
            self._error = VoiceError("internal")

    async def end(self) -> Utterance:
        """Stop the audio and collect everything that was said."""
        stream, reader = self._stream, self._reader
        if stream is None or reader is None:
            return Utterance(text="")

        async def wrap_up() -> None:
            await stream.finish()
            await reader

        try:
            limit = STREAMING_WRAP_UP_S if self._provider.streaming else BATCH_WRAP_UP_S
            # Words already heard are kept even if the last ones never arrive.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wrap_up(), timeout=limit)
        finally:
            await self.abandon()
        if self._error is not None and not self.text():
            raise self._error
        return Utterance(text=self.text())

    async def abandon(self) -> None:
        """Stop without using what was heard. Safe to call twice."""
        reader, stream = self._reader, self._stream
        self._reader = self._stream = None
        self._backlog.clear()
        if reader is not None and not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.close()
