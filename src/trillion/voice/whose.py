"""Is a word hers (her echo) or theirs (the user)? Judged by when it was said (#102).

Outside her playback: theirs. Inside and matching what she was saying then: hers.
Inside and not matching: a misheard echo, unless the microphone was clearly louder
than her echo would be. Pure.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from statistics import median
from typing import Literal

from trillion.voice.overlap import is_stop, matches, words_of
from trillion.voice.timeline import WINDOW_S, Timeline
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
_SHARE_KEEP = 20
_HER_SILENT = 0.01  # below this she is too quiet to learn from
# Her own word is the user's only once her echo is learned from this many words,
LEARNED_ENOUGH = 5
# with her level taken this much either side of the word.
WIDEN_S = 0.3
# Her echo is one of the words she was saying then: this many either side of where she
# had got to, by time, allowing for her echo coming back late.
NEAR_WORDS = 3
# Until her echo is learned, a word that is not hers is judged as if this much of her
# came back at most. The page cancels her echo, so little of it reaches the microphone,
# and the cautious start took the user's own words for hers.
UNLEARNED_SHARE = 0.2

# Mean microphone level over a span of the recognizer's clock.
Level = Callable[[float, float], float]


def _her_peak(timeline: Timeline, t0: float, t1: float) -> float:
    """Her loudest 20 ms over a span: her echo may peak where the word was heard."""
    n = max(1, math.ceil((t1 - t0) / WINDOW_S))
    return max(timeline.her_level(t0 + i * WINDOW_S, t0 + (i + 1) * WINDOW_S) for i in range(n))


def matched(word: Word, timeline: Timeline) -> bool:
    """Whether the word is one she was saying then: of the sentence playing, and near
    where she had got to in it (`NEAR_WORDS`), not anywhere in it, so the user's own
    "what" over her is not her "what" from the start of the sentence. "twenty-six" or
    "$5" is several words: matched only if all of them are."""
    found = timeline.placed(word.mid_s)
    if found is None:
        return False
    s, start, end = found
    where = min(1.0, max(0.0, (word.mid_s - start) / max(end - start, WINDOW_S)))
    hers: set[str] = set()
    for form in {s.written, s.spoken}:
        said = words_of(form)
        if not said:
            continue
        i = round(where * (len(said) - 1))
        hers.update(said[max(0, i - NEAR_WORDS) : i + NEAR_WORDS + 1])
    heard = words_of(word.text)
    return bool(heard) and all(matches(w, hers, stop=is_stop(w)) for w in heard)


def whose(
    word: Word,
    timeline: Timeline,
    mic: Level,
    *,
    share: float,
    room: float = ROOM,
    learned: int = 0,
) -> Whose:
    """Hers or theirs. `share` is how much of her comes back (`EchoShare.value`),
    learned from `learned` words (`EchoShare.learned`); `room` is the microphone's
    level when nobody speaks."""
    if timeline.at(word.mid_s) is None:
        return "theirs"
    loud = mic(word.start_s, word.end_s)
    floor = room * OVER_ROOM
    if matched(word, timeline):
        # The user saying her words over her ("send it" over "Should I send it?") is
        # theirs only when far louder than her echo, and only once her echo is known.
        # Her level over a wider span, since a word's times can be a little off hers.
        if learned < LEARNED_ENOUGH:
            return "hers"
        her = _her_peak(timeline, word.start_s - WIDEN_S, word.end_s + WIDEN_S)
        return "theirs" if loud >= max(2 * OVER_ECHO * share * her, floor) else "hers"
    if learned < LEARNED_ENOUGH:
        share = min(share, UNLEARNED_SHARE)
    expected = timeline.her_level(word.start_s, word.end_s) * share
    return "theirs" if loud >= max(expected * OVER_ECHO, floor) else "hers"


class EchoShare:
    """How much of her comes back through the microphone, learned from her words."""

    def __init__(self) -> None:
        self._seen: list[float] = []

    def learn(self, *, mic_level: float, her_level: float) -> None:
        """One word that matched hers: the microphone's level and hers."""
        if her_level <= _HER_SILENT:
            return
        ratio = min(_SHARE_MAX, max(_SHARE_MIN, mic_level / her_level))
        # Once enough is known, one word far louder than her echo so far (the user
        # saying her word) counts only as that much louder: her echo turned up still
        # climbs, a step at a time, but one loud word can't jump it.
        if self.learned >= LEARNED_ENOUGH:
            ratio = min(ratio, self.value * OVER_ECHO)
        self._seen.append(ratio)
        del self._seen[:-_SHARE_KEEP]

    @property
    def value(self) -> float:
        return median(self._seen) if self._seen else _SHARE_START

    @property
    def learned(self) -> int:
        """How many words it was learned from (the last `_SHARE_KEEP` at most)."""
        return len(self._seen)
