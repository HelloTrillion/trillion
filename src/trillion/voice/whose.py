"""Is a word hers (her echo) or theirs (the user)? Judged by when it was said (#102).

Outside her playback: theirs. Inside and matching what she was saying then: hers.
Inside and not matching: a misheard echo, unless the microphone was clearly louder
than her echo would be. Pure.
"""

from __future__ import annotations

from collections.abc import Callable
from statistics import median
from typing import Literal

from trillion.voice.overlap import is_stop, matches, words_of
from trillion.voice.timeline import Timeline
from trillion.voice.types import Word

Whose = Literal["hers", "theirs"]
# Theirs takes a microphone this many times louder than her echo would be,
OVER_ECHO = 2.5
# and this many times louder than the room.
OVER_ROOM = 4.0
# The room's level when nothing better is known.
ROOM = 0.002
_SHARE_START = 0.5  # cautious: a misheard echo stays hers
_SHARE_MIN, _SHARE_MAX = 0.02, 1.0
_SHARE_KEEP = 50
_HER_SILENT = 0.01  # below this she is too quiet to learn from

# Mean microphone level over a span of the recognizer's clock.
Level = Callable[[float, float], float]


def whose(word: Word, timeline: Timeline, mic: Level, *, share: float, room: float = ROOM) -> Whose:
    """Hers or theirs. `share` is how much of her comes back (`EchoShare.value`);
    `room` is the microphone's level when nobody speaks."""
    s = timeline.at(word.mid_s)
    if s is None:
        return "theirs"
    hers = set(words_of(f"{s.written} {s.spoken}"))
    # "twenty-six" or "$5" is several words: hers only if all of them are.
    heard = words_of(word.text)
    if heard and all(matches(w, hers, stop=is_stop(w)) for w in heard):
        return "hers"
    expected = timeline.her_level(word.start_s, word.end_s) * share
    loud = mic(word.start_s, word.end_s)
    return "theirs" if loud >= max(expected * OVER_ECHO, room * OVER_ROOM) else "hers"


class EchoShare:
    """How much of her comes back through the microphone, learned from her words."""

    def __init__(self) -> None:
        self._seen: list[float] = []

    def learn(self, *, mic_level: float, her_level: float) -> None:
        """One word judged hers by its match: the microphone's level and hers."""
        if her_level > _HER_SILENT:
            self._seen.append(min(_SHARE_MAX, max(_SHARE_MIN, mic_level / her_level)))
            del self._seen[:-_SHARE_KEEP]

    @property
    def value(self) -> float:
        return median(self._seen) if self._seen else _SHARE_START
