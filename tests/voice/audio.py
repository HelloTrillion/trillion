"""Made-up microphone audio for tests: tone for a voice, near-silence for a room."""

from __future__ import annotations

import math
import struct

RATE = 16_000


def tone(seconds: float, loud: float = 0.2) -> bytes:
    n = int(RATE * seconds)
    return struct.pack(
        f"<{n}h", *(int(32767 * loud * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(n))
    )


def room(seconds: float, loud: float = 0.001) -> bytes:
    """A quiet room: a faint hum, not digital zero."""
    return tone(seconds, loud)


def dead(seconds: float) -> bytes:
    return b"\0\0" * int(RATE * seconds)


def frames(pcm: bytes, ms: int = 60) -> list[bytes]:
    step = RATE * ms // 1000 * 2
    return [pcm[i : i + step] for i in range(0, len(pcm), step)]
