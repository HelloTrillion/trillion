"""The shapes every voice provider shares (VOI-11). No vendor's vocabulary appears here."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

# Where the sound is made. "core": this process synthesizes audio and streams it to
# the page. "client": the page speaks the sentence itself (the system voice), so no
# audio crosses the socket at all.
Where = Literal["core", "client"]


@dataclass(frozen=True, kw_only=True)
class AudioFormat:
    """Raw audio as the page receives it: signed 16-bit little-endian PCM."""

    sample_rate: int
    channels: int = 1


@dataclass(frozen=True, kw_only=True)
class Voice:
    id: str
    name: str
    preview_url: str | None = None


class VoiceError(Exception):
    """A voice provider failed. `kind` is safe to show and to log; the message may not be."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


class TTSProvider(Protocol):
    name: str
    where: Where
    # None exactly when `where` is "client".
    output_format: AudioFormat | None

    def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        """One sentence in, audio out, in whole samples. Empty text makes no request."""
        ...

    async def voices(self) -> list[Voice]:
        """The voices this account or machine offers. Free to call; it proves a key works."""
        ...
