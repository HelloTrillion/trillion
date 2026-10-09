"""The computer's own voice: free, no setup, and nothing leaves the machine (VOI-11b).

The page speaks each sentence with the operating system's speech synthesis, so
this provider produces no audio. It exists so the core treats "speak with the
system voice" exactly like any other choice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from trillion.voice.types import AudioFormat, Voice, Where

NAME = "system"


class SystemVoice:
    name = NAME
    where: Where = "client"
    output_format: AudioFormat | None = None

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        return
        yield b""  # pragma: no cover - makes this an async generator

    async def voices(self) -> list[Voice]:
        return []  # the page lists the voices its own system has
