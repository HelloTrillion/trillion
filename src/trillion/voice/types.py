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


# ── Listening ────────────────────────────────────────────────────────────

# What the page sends and every recognizer accepts: 16 kHz mono PCM16 (VOI-1).
CAPTURE = AudioFormat(sample_rate=16_000)


@dataclass(frozen=True, kw_only=True)
class Heard:
    """Words a recognizer made out of the audio so far.

    `final` means this stretch will not change; it does not mean the user has
    finished the thought (VOI-15a). A message can span several final segments,
    and deciding that it is over is the listener's job, not the recognizer's."""

    text: str
    final: bool
    # The recognizer itself heard the speaker stop here (Deepgram's speech_final).
    # A hint for the listener, never the decision.
    pause: bool = False
    # From a recognizer that judges turns itself (VOI-12): "maybe" (probably finished),
    # "resumed" (they carried on after a maybe) or "done" (over; `text` is the whole
    # turn). None from recognizers that leave the decision to the listener.
    turn: Literal["maybe", "resumed", "done"] | None = None


@dataclass(frozen=True, kw_only=True)
class Choice:
    """One thing a provider lets the user pick, such as a recognition model."""

    id: str
    name: str
    # Set when choosing it needs a one-time download, in bytes.
    download: int | None = None


class STTStream(Protocol):
    async def send(self, pcm: bytes) -> None:
        """More audio, in whole samples."""
        ...

    async def finish(self) -> None:
        """No more audio is coming. What was said is delivered, then `events` ends."""
        ...

    async def close(self) -> None:
        """Abandon the stream. Safe to call twice, and after `finish`."""
        ...

    def events(self) -> AsyncIterator[Heard]: ...


class STTProvider(Protocol):
    name: str
    # True when words arrive while the user is still speaking. False when the whole
    # utterance is transcribed after it ends, so there is nothing to show meanwhile.
    streaming: bool
    # True when the recognizer itself decides that the user has finished (VOI-12).
    turns: bool

    async def open(self, *, choice: str) -> STTStream:
        """Start listening. Raises VoiceError if it cannot."""
        ...

    async def choices(self) -> list[Choice]:
        """What can be picked. Free to call; for a hosted provider it proves the key works."""
        ...

    async def prepare(self, choice: str) -> None:
        """One-time setup for a choice, such as a download. Most providers need none."""
        ...
