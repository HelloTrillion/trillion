"""Speak a reply as it streams (VOI-5, VOI-14, VOI-16).

The session feeds this the reply text as the model writes it. Each complete
sentence is cleaned for speech and announced to the page as `reply.sentence`.
If the chosen voice is made in the core, its audio follows as binary frames;
if it is made by the page (the system voice), the announcement is all the page
needs. Sentences are synthesized in order, one at a time: a hosted voice
arrives faster than it plays, so the next sentence is ready before the last
one ends.

If the voice fails, the turn carries on as text and says so once (VOI-14).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from collections.abc import Awaitable, Callable
from typing import Any

from trillion.persona.prompt import degraded
from trillion.voice.sentences import SentenceSplitter
from trillion.voice.speakable import speakable
from trillion.voice.types import TTSProvider, VoiceError

logger = logging.getLogger(__name__)

# Binary frame from core to page: kind (1 byte), turn (u32), sentence (u32), then PCM.
FRAME_AUDIO = 0x01
_HEADER = struct.Struct("<BII")
# Small enough to start playing quickly, large enough not to flood the socket.
_FRAME_BYTES = 8192
# Failures worth one more try: a voice service having a bad second (VOI-14). A refused
# key or an empty account will say the same thing again.
_PASSING = frozenset({"unreachable", "timeout", "provider_unavailable", "rate_limited"})
RETRY_S = 0.5

SendJson = Callable[..., Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


def frame(turn: int, sentence: int, pcm: bytes) -> bytes:
    return _HEADER.pack(FRAME_AUDIO, turn, sentence) + pcm


class Speaker:
    """One per turn. Never raises into the turn: speech is the optional half of a reply."""

    def __init__(
        self,
        provider: TTSProvider,
        voice: str,
        turn: int,
        send: SendJson,
        send_bytes: SendBytes,
    ) -> None:
        self._provider = provider
        self._voice = voice
        self._turn = turn
        self._send = send
        self._send_bytes = send_bytes
        # Her voice starts at the first natural pause, not the first full stop.
        self._splitter = SentenceSplitter(clause_first=True)
        self._count = 0
        self._failed = False
        self._queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def feed(self, text: str) -> None:
        for sentence in self._splitter.feed(text):
            await self._say(sentence)

    async def pause(self) -> None:
        """A tool is about to run: say what she has said so far rather than hold it."""
        for sentence in self._splitter.flush():
            await self._say(sentence)

    async def finish(self) -> None:
        """The reply is complete. Returns once every sentence has been sent."""
        await self.pause()
        if self._worker is not None:
            await self._queue.put(None)
            try:
                await self._worker
            except asyncio.CancelledError:
                # The worker stopped by `stop()` is fine; this task being stopped is not
                # something to swallow, or its turn would end as if it were heard.
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
        await self._send("audio.end", turn=self._turn, sentences=self._count)

    async def stop(self) -> None:
        """Interrupted: nothing more is synthesized or sent (VOI-9)."""
        self._failed = True
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._worker
        await self._send("audio.stop", turn=self._turn)

    async def _say(self, sentence: str) -> None:
        text = speakable(sentence)
        if not text or self._failed:
            return
        self._count += 1
        await self._send("reply.sentence", turn=self._turn, seq=self._count, text=text)
        if self._provider.where != "core":
            return
        if self._worker is None:
            self._worker = asyncio.create_task(self._synthesize())
        await self._queue.put((self._count, text))

    async def _synthesize(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            seq, text = item
            if self._failed:
                continue
            for attempt in (1, 2):
                sent = False
                try:
                    pending = b""
                    async for chunk in self._provider.synthesize(text, voice=self._voice):
                        pending += chunk
                        while len(pending) >= _FRAME_BYTES:
                            await self._send_bytes(frame(self._turn, seq, pending[:_FRAME_BYTES]))
                            pending = pending[_FRAME_BYTES:]
                            sent = True
                    if pending:
                        await self._send_bytes(frame(self._turn, seq, pending))
                    break
                except VoiceError as e:
                    # Speaking again is only ever speaking again: nothing the reply did is
                    # repeated, because the speaker holds text and never a tool call. A
                    # sentence already partly heard is not started over.
                    if attempt == 1 and not sent and e.kind in _PASSING:
                        await asyncio.sleep(RETRY_S)
                        continue
                    await self._fail(e.kind)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # The class name only: an exception message can carry what she was saying.
                    logger.error("speech failed in turn %d: %s", self._turn, type(e).__name__)
                    await self._fail("internal")
                    break

    async def _fail(self, kind: str) -> None:
        if self._failed:
            return
        self._failed = True
        logger.warning("speech provider %s failed: %s", self._provider.name, kind)
        # The reply itself is unaffected: it keeps arriving as text.
        await self._send("error", code="speech", kind=kind, sentence=degraded("voice", kind))


def describe(
    provider: TTSProvider | None, kind: str | None, voice: str | None
) -> dict[str, Any] | None:
    """What the page needs to know to play or speak a reply."""
    if provider is None or kind is None:
        return None
    fmt = provider.output_format
    return {
        "kind": kind,
        "where": provider.where,
        "voice": voice,
        "sample_rate": fmt.sample_rate if fmt is not None else None,
    }
