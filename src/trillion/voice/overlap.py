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


def _words(text: str) -> list[str]:
    """Words, lower case, with numbers and money spelled out: she writes "$5" and
    "10:30", says "five dollars" and "ten thirty", and either may come back."""
    text = text.lower().replace("\u2019", "'")
    for symbol, word in _SYMBOLS.items():
        text = text.replace(symbol, word)
    out: list[str] = []
    for w in _WORD.findall(_BOUNDARY.sub(" ", text)):
        out.extend(_spelled(int(w)) if w.isdigit() and len(w) <= 6 else [w])
    return out


def _acks(words: list[str]) -> int | None:
    """How many acknowledgements the words are, or None if any word is something else."""
    text, count = _PHRASES.subn(" ", " ".join(words))
    rest = text.split()
    if any(w not in _ACKS for w in rest):
        return None
    return count + len(rest)


def decide(
    words: str,
    *,
    done: bool,
    elapsed_s: float,
    playing: Sequence[str],
    drop_hers: bool = False,
) -> Decision:
    """With `drop_hers`, her words among theirs are left out first (stop words never
    are): over her, what comes back of her is mixed into what they say."""
    heard = _words(words)
    hers = {w for sentence in playing for w in _words(sentence)}
    if drop_hers:
        heard = [w for w in heard if w not in hers or w in _STOPS]
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
    mine = sum(w in hers for w in heard)
    if mine / len(heard) >= ECHO_SHARE:
        return "echo"
    strays = [w for w in heard if w not in hers and w not in _FILLERS]
    if len(strays) > _MOST_STRAYS or any(w in _STOPS for w in strays):
        return "candidate"
    return "echo" if mine else "nothing"


def share_hers(words: str, playing: Sequence[str]) -> float:
    """How much of the words are hers, 0 to 1."""
    heard = _words(words)
    hers = _hers(playing)
    return sum(w in hers for w in heard) / len(heard) if heard else 0.0


def _is_hers(token: str, hers: set[str]) -> bool:
    return all(w in hers for w in _words(token))


def echo_through(words: str, playing: Sequence[str]) -> int:
    """How many of the words, as the recognizer spaced them, run up to and including
    the last one of hers. Words of theirs after it are not yet judged."""
    hers = _hers(playing)
    tokens = words.split()
    return max((i + 1 for i, t in enumerate(tokens) if _is_hers(t, hers)), default=0)


def strip_hers(words: str, playing: Sequence[str]) -> str:
    """Her words in front of theirs, come back just as she stopped, taken off. All of
    them if every word is hers; a single word of hers in front is left to the user."""
    hers = _hers(playing)
    tokens = words.split()
    lead = 0
    while lead < len(tokens) and _is_hers(tokens[lead], hers):
        lead += 1
    if lead == len(tokens):
        return ""
    return " ".join(tokens[lead:]) if lead >= 2 else words.strip()
