"""Her listening cues, made once per voice and kept in memory (PER-11).

"mm-hm", "mm", "yeah" and "go on" in her own voice, so a cue plays at once with no
call to anyone. Made with the voice's own text-to-speech, trimmed of the silence
around them. Only a voice made in the core has audio to send: the system voice,
spoken by the page, makes no cues. Nothing made here is ever logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from trillion.api.speech import loudness
from trillion.voice.cues import CUES
from trillion.voice.timeline import WINDOW_S
from trillion.voice.types import TTSProvider

if TYPE_CHECKING:
    from trillion.api.runtime import Runtime

logger = logging.getLogger(__name__)

# Quieter than this share of the loudest 20 ms is the silence trimmed from either end,
# and never below the floor.
_TRIM_SHARE = 0.05
_TRIM_FLOOR = 0.003


@dataclass(frozen=True)
class Cue:
    name: str
    pcm: bytes
    sample_rate: int
    # Her loudness per 20 ms, for telling its echo from the user (#102).
    levels: tuple[float, ...]


def possible(rt: Runtime) -> bool:
    """Cues can play: hearing that judges turns (Flux), in English, and her voice made
    in the core, which has audio to send."""
    stt, s, tts, speech = rt.stt, rt.listening, rt.tts, rt.speech
    return (
        stt is not None
        and s is not None
        and stt.turns
        and stt.word_times
        and s.choice != "multi"
        and tts is not None
        and tts.where == "core"
        and tts.output_format is not None
        and speech is not None
    )


def trim(pcm: bytes, window_bytes: int) -> bytes:
    """Without the silence before and after the sound."""
    levels = loudness(pcm, window_bytes)
    if not levels:
        return pcm
    bar = max(_TRIM_FLOOR, max(levels) * _TRIM_SHARE)
    loud = [i for i, v in enumerate(levels) if v >= bar]
    if not loud:
        return b""
    return pcm[loud[0] * window_bytes : (loud[-1] + 1) * window_bytes]


class CueBank:
    """The cues per voice: made in the background, read at once."""

    def __init__(self) -> None:
        self._made: dict[tuple[str, str], dict[str, Cue]] = {}
        self._making: dict[tuple[str, str], asyncio.Task[None]] = {}

    def get(self, tts: TTSProvider, voice: str) -> dict[str, Cue] | None:
        """The cues for this voice, or None while they are not made."""
        return self._made.get((tts.name, voice))

    def ensure(self, tts: TTSProvider, voice: str) -> None:
        """Start making this voice's cues, unless they are made or being made."""
        key = (tts.name, voice)
        if key in self._made or key in self._making:
            return
        task = asyncio.create_task(self._make(key, tts, voice))
        self._making[key] = task
        task.add_done_callback(lambda _t: self._making.pop(key, None))

    async def wait(self) -> None:
        """Until every cue being made is made or has failed."""
        while self._making:
            await asyncio.gather(*list(self._making.values()), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._making.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._making.clear()

    async def _make(self, key: tuple[str, str], tts: TTSProvider, voice: str) -> None:
        fmt = tts.output_format
        if tts.where != "core" or fmt is None:
            return
        rate = fmt.sample_rate * fmt.channels
        window = 2 * max(1, round(rate * WINDOW_S))
        made: dict[str, Cue] = {}
        for name, _weight in CUES:
            try:
                pcm = b"".join([chunk async for chunk in tts.synthesize(name, voice=voice)])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # The kind only: never the audio, nor a message that could carry it.
                kind = getattr(e, "kind", type(e).__name__)
                logger.warning("listening cues for %s not made: %s", tts.name, kind)
                return
            pcm = trim(pcm[: len(pcm) - len(pcm) % 2], window)
            if not pcm:
                logger.warning("listening cues for %s not made: silent", tts.name)
                return
            made[name] = Cue(name, pcm, fmt.sample_rate, tuple(loudness(pcm, window)))
        self._made[key] = made
