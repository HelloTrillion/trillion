"""Deciding what the user meant by talking over her (VOI-9).

Pure: words, whether the recognizer says they are done, and how long it has been,
in; one of three answers out. An acknowledgement ("yeah", "mm-hm") lets her carry
on, and so does nothing at all (a cough, a door). Any other word stops her, at
once. Words that are all in what she is saying are her own voice coming back.
The list is English; in another language every word stops her, which is the safe
way to be wrong.

While she speaks, whose each word is (hers or the user's) is judged by when it was
said (`voice/whose.py`); only the user's words reach `confirm`. The word matching
that judgement uses lives here (`words_of`, `matches`, `is_stop`).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Literal

Decision = Literal["wait", "carry_on", "interrupt"]

# Acknowledgements alone carry on once the recognizer says they are done, or after this.
ACK_AFTER_S = 1.2
# By this long after they started, it is decided either way.
DECIDE_BY_S = 1.5
# While watching, words that were not hers duck her; unless more of theirs follow
# within this long, it was her echo misheard, and she carries on.
CONFIRM_S = 1.0
# This many words not hers, before or after the duck, are the user: a misheard
# fragment of her is a word or two.
_CONFIRMED_STRAYS = 3
# Ended on two words of theirs, the words must be less than this share hers.
_HALF = 0.5
# More than this many acknowledgements is saying something.
_MOST_ACKS = 3
# Spoken as two words; "uh huh" is how a recognizer may spell "uh-huh".
_PHRASES = re.compile(r"(?<!\S)(?:got it|i see|uh huh)(?!\S)")
# Fillers: someone thinking aloud over her is not stopping her.
_FILLERS = frozenset("um umm uh uhm er erm hm hmm ah".split())
_ACKS = (
    frozenset(
        "yeah yes yep mm-hm mm-hmm mmhmm mhm mm uh-huh right okay ok sure exactly totally "
        "true".split()
    )
    | _FILLERS
)
# Ended on this many words not hers, the recognizer's turn is the user's (see `confirm`).
_ENDED_STRAYS = 2
# Her words reached the microphone and the recognizer in any of these spellings.
_SYMBOLS = {"$": " dollars ", "\u00a3": " pounds ", "\u20ac": " euros ", "%": " percent "}
_BOUNDARY = re.compile(r"(?<=\d)(?=[^\W\d_])|(?<=[^\W\d_])(?=\d)")
_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
# Any script and digits count as words. Hyphens and apostrophes stay inside a word,
# so "mm-hm", "uh-huh" and "don't" are one word each.
_WORD = re.compile(r"[^\W_]+(?:['-][^\W_]+)*")
# Words that mean "stop". They are never taken for her echo.
_STOPS = frozenset("stop wait no nope hold hang pause cancel enough quiet shh hey".split())


def _spelled(n: int) -> list[str]:
    """A number as it is said: 30 is "thirty", 305 is "three hundred five"."""
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        return [_ONES[n // 100], "hundred"] + (_spelled(n % 100) if n % 100 else [])
    return [*_spelled(n // 1000), "thousand"] + (_spelled(n % 1000) if n % 1000 else [])


_NUMBER_WORDS = frozenset([*_ONES, *_TENS[2:], "hundred", "thousand"])


def _year(n: int) -> list[str]:
    """A year as it is said: 2026 is "twenty twenty six", 1905 "nineteen oh five"."""
    if 2000 <= n <= 2009:
        return ["two", "thousand"] + ([_ONES[n % 10]] if n % 10 else [])
    rest = n % 100
    if rest == 0:
        return [*_spelled(n // 100), "hundred"]
    return [*_spelled(n // 100), *(["oh", _ONES[rest]] if rest < 10 else _spelled(rest))]


def _number(w: str) -> list[str]:
    n = int(w)
    return _year(n) if len(w) == 4 and 1100 <= n <= 2099 else _spelled(n)


def words_of(text: str) -> list[str]:
    """Words, lower case, with numbers and money spelled out: she writes "$5" and
    "10:30", says "five dollars" and "ten thirty", and either may come back."""
    text = text.lower().replace("\u2019", "'")
    for symbol, word in _SYMBOLS.items():
        text = text.replace(symbol, word)
    out: list[str] = []
    for w in _WORD.findall(_BOUNDARY.sub(" ", text)):
        if w.isdigit() and len(w) <= 6:
            out.extend(_number(w))
        elif "-" in w and all(p in _NUMBER_WORDS for p in w.split("-")):
            out.extend(w.split("-"))  # "twenty-six" as "twenty six"
        else:
            out.append(w)
    return out


_words = words_of


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


def _hers(playing: Sequence[str]) -> set[str]:
    return {w for sentence in playing for w in _words(sentence)}


# A stop word is hers when she says it in another form ("Stopped." comes back as
# "Stop."): a word of hers that starts with it, this many letters at least. "no" only
# as itself.
_STEM = 3


def is_stop(w: str) -> bool:
    """Whether a word (as `words_of` gives it) means "stop"."""
    return w in _STOPS


def is_filler(w: str) -> bool:
    """Whether a word (as `words_of` gives it) is someone thinking aloud ("um")."""
    return w in _FILLERS


def _mine(w: str, hers: set[str]) -> bool:
    """Whether a heard word is one of hers: the same word, or a stop word she said in
    another form (stop and stopped, wait and waiting)."""
    return w in hers or (w in _STOPS and matches(w, hers, stop=True))


# A longer word is another form of a shorter one when it is at least this long ("send"
# and "sends"), or when what follows it is one of these endings ("ask" and "asked").
_LONG_STEM = 4
_ENDINGS = frozenset("s es d ed ing er ers ly 's".split())


def _inflects(short: str, long: str) -> bool:
    """Whether `long` is `short` with an ending: "ask" and "asked", "leave" and
    "leaving" (its "e" dropped), but not "the" and "then" or "for" and "forget"."""
    if len(short) < _STEM or len(long) <= len(short):
        return False
    if long.startswith(short):
        return len(short) >= _LONG_STEM or long[len(short) :] in _ENDINGS
    stem = short[:-1]
    return short.endswith("e") and long.startswith(stem) and long[len(stem) :] in _ENDINGS


def matches(w: str, hers: Collection[str], *, stop: bool = False) -> bool:
    """Whether a word (as `words_of` gives it) is one of hers. Loosely: the same word,
    or another form of it either way ("ask" and "asked"). With `stop`, strictly, as a
    stop word is: her word itself, or one of hers starting with it ("stop" by her
    "stopped"), never a shorter word of hers ("cancel" by her "can")."""
    if w in hers:
        return True
    if stop:
        return len(w) >= _STEM and any(h.startswith(w) for h in hers)
    return any(_inflects(w, h) or _inflects(h, w) for h in hers)


def confirm(
    candidate: str,
    after: str,
    *,
    done: bool,
    elapsed_s: float,
    playing: Sequence[str],
    turn: str | None = None,
) -> Decision:
    """Words that were not hers ducked her (the candidate); `after` is what has been
    heard since. Two words not hers can be her echo misheard ("Alright, properly this
    time" heard as "or writes properly this"), so they wait. Three across both stop
    her, and so do two that the recognizer has ended a turn on, in words less than
    half hers: a misheard fragment of her is half her words. A stop word that is not
    hers stops her at once. Acknowledgements and fillers carry on as `decide` lets
    them; nothing more by `CONFIRM_S` carries on.

    Three words of theirs count only in words at least half theirs: her echo can carry
    several misheard words. Two in an ended turn count only when the recognizer's
    whole `turn` (her words it let go included) is less than half hers, since it ends
    turns of her echo at her pauses too."""
    hers = _hers(playing)
    heard = _words(candidate) + _words(after)
    if not heard:
        return "carry_on" if elapsed_s >= CONFIRM_S else "wait"
    theirs = [w for w in heard if not _mine(w, hers)]
    if any(w in _STOPS for w in theirs):
        return "interrupt"
    acks = _acks(theirs)
    if acks is not None and theirs:
        if acks > _MOST_ACKS:
            return "interrupt"
        return "carry_on" if done or elapsed_s >= ACK_AFTER_S else "wait"
    said = [w for w in theirs if w not in _ACKS]
    half_theirs = len(theirs) / len(heard) >= _HALF
    if len(said) >= _CONFIRMED_STRAYS and half_theirs:
        return "interrupt"
    whole = _words(turn) if turn is not None else heard
    whole_theirs = sum(not _mine(w, hers) for w in whole) / len(whole) if whole else 0.0
    if done and len(said) >= _ENDED_STRAYS and whole_theirs > 1 - _HALF:
        return "interrupt"
    return "carry_on" if elapsed_s >= CONFIRM_S else "wait"
