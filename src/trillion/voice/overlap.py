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
from collections.abc import Collection, Sequence
from typing import Literal

Decision = Literal["wait", "carry_on", "interrupt"]
Watch = Literal["nothing", "echo", "candidate"]

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
# Words heard while she speaks are her echo when at least this share of them is in
# what she is saying: a recognizer mishears some of her, and a stray word of the user's
# among hers is not yet them talking.
ECHO_SHARE = 0.6
# Fewer words than this that are not hers are not yet the user: a recognizer mishears
# one of hers ("noon" as "new") as each report adds a word. A stop word is enough alone.
_MOST_STRAYS = 1
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


def _hers(playing: Sequence[str]) -> set[str]:
    return {w for sentence in playing for w in _words(sentence)}


# A stop word is hers when she says it in another form ("Stopped." comes back as
# "Stop."): a word of hers that starts with it, this many letters at least. "no" only
# as itself.
_STEM = 3


def _mine(w: str, hers: set[str]) -> bool:
    """Whether a heard word is one of hers: the same word, or a stop word she said in
    another form (stop and stopped, wait and waiting)."""
    if w in hers:
        return True
    if w not in _STOPS or len(w) < _STEM:
        return False
    # Only her inflected form of the stop word: she said "stopped" and "stop" came
    # back. Not the other way: her "can" does not make the user's "cancel" hers.
    return any(h.startswith(w) for h in hers)


def is_stop(w: str) -> bool:
    """Whether a word (as `words_of` gives it) means "stop"."""
    return w in _STOPS


def matches(w: str, hers: Collection[str], *, stop: bool = False) -> bool:
    """Whether a word (as `words_of` gives it) is one of hers. Loosely: the same word,
    or one starts the other, the shorter at least 3 letters ("leave" and "leaving").
    With `stop`, strictly, as a stop word is: her word itself, or her inflection of it
    ("stop" by her "stopped"), never a shorter word of hers ("cancel" by her "can")."""
    if w in hers:
        return True
    if len(w) < _STEM:
        return False
    if stop:
        return any(h.startswith(w) for h in hers)
    return any(h.startswith(w) or (len(h) >= _STEM and w.startswith(h)) for h in hers)


def _stop(w: str, hers: set[str]) -> bool:
    """A stop word that is the user's, not her own come back."""
    return w in _STOPS and not _mine(w, hers)


def judge_watch(words: str, playing: Sequence[str]) -> Watch:
    """Words heard while she speaks: nothing, her own voice coming back, or perhaps the
    user. `playing` is what she is saying now: the sentence being heard, the one before
    and the one after, since her audio runs ahead of its reports.

    Mostly hers is her echo; her own "No, the train..." comes back as hers. Otherwise
    it takes two words that are not hers (fillers aside), or one that is a stop word,
    to be a candidate: one misheard word of hers is not the user. Once words are a
    candidate, `decide` weighs them, and there a stop word stops her."""
    heard = _words(words)
    if not heard:
        return "nothing"
    hers = _hers(playing)
    mine = sum(_mine(w, hers) for w in heard)
    if mine / len(heard) >= ECHO_SHARE:
        return "echo"
    strays = [w for w in heard if not _mine(w, hers) and w not in _FILLERS]
    if len(strays) > _MOST_STRAYS or any(w in _STOPS for w in strays):
        return "candidate"
    return "echo" if mine else "nothing"


def share_hers(words: str, playing: Sequence[str]) -> float:
    """How much of the words are hers, 0 to 1."""
    heard = _words(words)
    hers = _hers(playing)
    return sum(w in hers for w in heard) / len(heard) if heard else 0.0


def _is_hers(token: str, hers: set[str]) -> bool:
    return all(_mine(w, hers) for w in _words(token))


def echo_through(words: str, playing: Sequence[str]) -> int:
    """How many of the words, as the recognizer spaced them, are her echo: up to her
    last word, taking in a word not hers only when two of hers follow it (a misheard
    word inside her sentence). One of hers after it ("skip" then "that") is not enough:
    that word may be the user's, and stays to be judged."""
    hers = _hers(playing)
    mark, run, stray = 0, 0, False
    for i, token in enumerate(words.split()):
        if _is_hers(token, hers):
            run += 1
            if not stray or run >= 2:
                mark, stray = i + 1, False
        else:
            run, stray = 0, True
    return mark


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
    if done and len(said) > _MOST_STRAYS and whole_theirs > 1 - _HALF:
        return "interrupt"
    return "carry_on" if elapsed_s >= CONFIRM_S else "wait"


def _within(run: list[str], seq: list[str]) -> bool:
    """Whether the words appear in this order, one after another, in the sequence."""
    n = len(run)
    return any(seq[i : i + n] == run for i in range(len(seq) - n + 1))


def echo_of(words: str, playing: Sequence[str], *, early: bool) -> str:
    """A turn heard just as she stopped, less her own last words come back: "" when it
    is only her. Only on evidence: three or more of her words in her order (in front,
    taken off; mostly her, all of it), or for one or two words, exactly her final ones
    heard at once (`early`). A reply that reuses her words ("Six?" after "...leave at
    six?") is theirs, and anything with a stop word in it always is."""
    heard = _words(words)
    mine = _hers(playing)
    if not heard or any(_stop(w, mine) for w in heard):
        return words.strip()
    # Her own stop word at the end of theirs ("Skip that. No." over her "No, the
    # train…") is her, come back as they spoke: taken off.
    tokens = words.split()
    while len(tokens) > 1 and all(w in _STOPS and _mine(w, mine) for w in _words(tokens[-1])):
        tokens.pop()
    if len(tokens) < len(words.split()):
        words = " ".join(tokens)
        heard = _words(words)
    hers = [w for sentence in playing for w in _words(sentence)]
    if len(heard) <= 2:
        return "" if early and hers[-len(heard) :] == heard else words.strip()
    lead = max((k for k in range(1, len(heard) + 1) if _within(heard[:k], hers)), default=0)
    if lead == len(heard):
        return ""
    if lead >= 3:
        # Off with the tokens that carried those words; the rest is theirs.
        tokens, taken = words.split(), 0
        while tokens and taken + len(_words(tokens[0])) <= lead:
            taken += len(_words(tokens.pop(0)))
        return " ".join(tokens)
    runs = any(_within(heard[i : i + 3], hers) for i in range(len(heard) - 2))
    if runs and share_hers(words, playing) >= ECHO_SHARE:
        return ""
    return words.strip()


def is_said(words: str) -> bool:
    """Whether there is a word in it that is not a filler."""
    return any(w not in _FILLERS for w in _words(words))


def late_echo(words: str, start: int, stop: int, playing: Sequence[str]) -> str:
    """Her words come back after theirs as she was stopped: from word `start` (what had
    been heard at the cut-in), the run of hers is taken out, up to the first word that
    is not hers, and only among words heard by `stop`."""
    hers = _hers(playing)
    tokens = words.split()
    end = start
    while end < min(stop, len(tokens)) and _is_hers(tokens[end], hers):
        end += 1
    return " ".join(tokens[:start] + tokens[end:]) if end > start else words.strip()
