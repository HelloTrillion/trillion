"""Deciding what the user meant by talking over her (VOI-9).

Pure: words, whether the recognizer says they are done, and how long it has been,
in; one of three answers out. An acknowledgement ("yeah", "mm-hm") lets her carry
on, and so does nothing at all (a cough, a door). Any other word stops her, at
once. Words that are all in what she is saying are her own voice coming back.
The list is English; in another language every word stops her, which is the safe
way to be wrong.

Before any of that, while she speaks, the words heard are watched (`judge_watch`):
mostly hers is her own voice coming back, and only words that are mostly not hers
start the deciding.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

Decision = Literal["wait", "carry_on", "interrupt"]
Watch = Literal["nothing", "echo", "candidate"]

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
# Words heard while she speaks are her echo when at least this share of them is in
# what she is saying: a recognizer mishears some of her, and a stray word of the user's
# among hers is not yet them talking.
ECHO_SHARE = 0.6
# Any script and digits count as words. Hyphens and apostrophes stay inside a word,
# so "mm-hm", "uh-huh" and "don't" are one word each.
_WORD = re.compile(r"[^\W_]+(?:['-][^\W_]+)*")
# Words that mean "stop". They are never taken for her echo.
_STOPS = frozenset("stop wait no nope hold hang pause cancel enough quiet shh hey".split())


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("\u2019", "'"))


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
    # Trade-off: a stop word is always the user, even when she said it herself. If her
    # own "no" or "wait" leaks back, she may stop; that is recoverable. Ignoring the
    # user is not.
    if heard and all(w in hers for w in heard) and not any(w in _STOPS for w in heard):
        heard = []  # her own voice, come back through the microphone
    if not heard:
        return "carry_on" if elapsed_s >= DECIDE_BY_S else "wait"
    acks = _acks(heard)
    if acks is None or acks > _MOST_ACKS:
        return "interrupt"
    return "carry_on" if done or elapsed_s >= ACK_AFTER_S else "wait"


def keys(text: str) -> tuple[str, ...]:
    """Each word as the recognizer spaced it, without case or punctuation: for telling
    whether words already judged are still the start of what is heard."""
    return tuple(" ".join(_words(token)) for token in text.split())


def judged_prefix(words: str, judged: Sequence[str]) -> int:
    """How many of the words, from the start, are the ones already judged. None of them
    when the recognizer has revised those words since: then all are judged again."""
    heard = keys(words)
    n = len(judged)
    return n if n and heard[:n] == tuple(judged) else 0


def judge_watch(words: str, playing: Sequence[str]) -> Watch:
    """Words heard while she speaks: nothing, her own voice coming back, or perhaps the
    user. `playing` is what she is saying now: the sentence being heard, the one before
    and the one after, since her audio runs ahead of its reports.

    Stop words get no say here: her own "No, the train..." comes back as hers. Once
    words are a candidate, `decide` weighs them, and there a stop word stops her."""
    heard = _words(words)
    if not heard:
        return "nothing"
    hers = {w for sentence in playing for w in _words(sentence)}
    share = sum(w in hers for w in heard) / len(heard)
    return "echo" if share >= ECHO_SHARE else "candidate"
