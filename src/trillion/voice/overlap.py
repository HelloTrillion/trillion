"""Deciding what the user meant by talking over her (VOI-9).

Pure: words, whether the recognizer says they are done, and how long it has been,
in; one of three answers out. An acknowledgement ("yeah", "mm-hm") lets her carry
on, and so does nothing at all (a cough, a door). Any other word stops her, at
once. Words that are all in what she is saying are her own voice coming back.
The list is English; in another language every word stops her, which is the safe
way to be wrong.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

Decision = Literal["wait", "carry_on", "interrupt"]

# Acknowledgements alone carry on once the recognizer says they are done, or after this.
ACK_AFTER_S = 1.2
# By this long after they started, it is decided either way.
DECIDE_BY_S = 1.5
# More than this many acknowledgements is saying something.
_MOST_ACKS = 3
# Spoken as two words; "uh huh" is how a recognizer may spell "uh-huh".
_PHRASES = re.compile(r"(?<!\S)(?:got it|i see|uh huh)(?!\S)")
_ACKS = frozenset(
    "yeah yes yep mm-hm mm-hmm mmhmm mhm mm uh-huh right okay ok sure exactly totally true".split()
)
# Hyphens stay inside a word, so "mm-hm" and "uh-huh" are one word each.
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _acks(words: list[str]) -> int | None:
    """How many acknowledgements the words are, or None if any word is something else."""
    text, count = _PHRASES.subn(" ", " ".join(words))
    rest = text.split()
    if any(w not in _ACKS for w in rest):
        return None
    return count + len(rest)


def decide(words: str, *, done: bool, elapsed_s: float, playing: Sequence[str]) -> Decision:
    heard = _words(words)
    hers = {w for sentence in playing for w in _words(sentence)}
    # Trade-off: if the user says only words she is saying (say "stop" while she says
    # "stop the timer"), we can't tell them from her echo, so she carries on at the limit.
    if heard and all(w in hers for w in heard):
        heard = []  # her own voice, come back through the microphone
    if not heard:
        return "carry_on" if elapsed_s >= DECIDE_BY_S else "wait"
    acks = _acks(heard)
    if acks is None or acks > _MOST_ACKS:
        return "interrupt"
    return "carry_on" if done or elapsed_s >= ACK_AFTER_S else "wait"
