"""When she says a listening cue, "mm-hm", while the user thinks aloud (PER-11, VOI-12).

On the phone a friend says "mm-hm" now and then, so you know they are with you. She
does the same, sparingly: only in a real pause mid-thought, by a recognizer that judges
turns (Flux), never when the turn sounds over, and often not even then. Pure: times and
words in, a cue or nothing out. Times are on the recognizer's clock.
"""

from __future__ import annotations

import random
from collections import Counter

from trillion.voice.overlap import words_of

# The cues, in her voice, and how often each is picked.
CUES: tuple[tuple[str, float], ...] = (("mm-hm", 0.4), ("mm", 0.3), ("yeah", 0.2), ("go on", 0.1))
# Many pauses get nothing: a coin decides each one.
CUE_CHANCE = 0.5
# At least this long between cues, and at most this many in one turn.
CUE_GAP_S = 8.0
CUE_MAX = 3
# Only once they have said this much in the turn.
MIN_SPEECH_S = 4.0
# "go on" only once the turn has run this long.
GO_ON_AFTER_S = 12.0
# A pause, not a stop: the last word ended this long ago.
PAUSE_MIN_S = 0.5
PAUSE_MAX_S = 1.0
# The recognizer must think the turn is not over: below this.
MAX_CONFIDENCE = 0.3
# Words that close a thought in an obvious way, said last.
_SIGN_OFFS = ("thanks", "thank you", "thank you very much", "bye", "goodbye", "cheers",
              "that's all", "that's it", "that is all")  # fmt: skip


# Said after the last word without changing what it is: quotes, brackets, spaces.
_CLOSERS = " \t\r\n\"')]}\u201d\u2019"


def asks(text: str) -> bool:
    """The words end in a question mark, closing quotes and brackets aside."""
    return text.rstrip(_CLOSERS).endswith("?")


def ends_the_thought(text: str) -> bool:
    """A question, or a sign-off like "thanks": the turn is likely over."""
    t = text.strip().rstrip(_CLOSERS)
    if not t:
        return True
    if asks(t):
        return True
    tail = " ".join(words_of(t)[-4:])
    return any(tail == s or tail.endswith(" " + s) for s in _SIGN_OFFS)


class CuePlan:
    """One user turn's cues: how many, which was last and when. `consider` is asked on
    each recognizer report during the turn."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()  # noqa: S311 - a cue, not a secret
        self.count = 0
        self.last: str | None = None
        self._last_at: float | None = None
        # The pause already decided, by when its last word ended: one flip per pause.
        self._decided: float | None = None
        # Each report that got no cue, by the first rule that said no. Only names.
        self.skipped: Counter[str] = Counter()

    def consider(
        self,
        *,
        now_s: float,
        heard_s: float,
        last_word_end_s: float | None,
        confidence: float | None,
        eager_since_word: bool,
        last_words: str,
    ) -> str | None:
        """A cue to play now, or None. Each rule alone can say no, and the first to say
        it is counted in `skipped`."""
        reason = self._refusal(
            now_s, heard_s, last_word_end_s, confidence, eager_since_word, last_words
        )
        if reason is None:
            self._decided = last_word_end_s
            if self._rng.random() >= CUE_CHANCE:
                reason = "coin"
        if reason is not None:
            self.skipped[reason] += 1
            return None
        cue = self._pick(heard_s)
        self.count += 1
        self.last, self._last_at = cue, now_s
        return cue

    def _refusal(
        self,
        now_s: float,
        heard_s: float,
        last_word_end_s: float | None,
        confidence: float | None,
        eager_since_word: bool,
        last_words: str,
    ) -> str | None:
        if last_word_end_s is None:
            return "no_words"
        if heard_s < MIN_SPEECH_S:
            return "short"
        if self.count >= CUE_MAX:
            return "max"
        if self._last_at is not None and now_s - self._last_at < CUE_GAP_S:
            return "gap"
        if not PAUSE_MIN_S <= now_s - last_word_end_s <= PAUSE_MAX_S:
            return "pause"
        if confidence is None or confidence >= MAX_CONFIDENCE:
            return "confident"
        if eager_since_word:
            return "eager"
        if ends_the_thought(last_words):
            return "ends"
        if self._decided == last_word_end_s:
            return "decided"
        return None

    def _pick(self, heard_s: float) -> str:
        options = [
            (cue, weight)
            for cue, weight in CUES
            if cue != self.last and (cue != "go on" or heard_s >= GO_ON_AFTER_S)
        ]
        names = [c for c, _ in options]
        return self._rng.choices(names, weights=[w for _, w in options])[0]
