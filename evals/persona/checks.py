"""The persona eval's code checks (EVL-1): what a reply must never do. No judge needed,
so these also run in CI against stored replies (tests/evals/test_persona_checks.py)."""

from __future__ import annotations

import re
from collections.abc import Sequence

# The "Never sound like this" lines in persona/SOUL.md, and their near forms. Matched
# case-insensitively in the first sentence; one that spans two sentences, at the start.
BANNED_OPENERS = (
    "great question",
    "i'd be happy to",
    "i would be happy to",
    "i understand. let me",
    "i understand, let me",
    "based on the information",
    "certainly!",
    "absolutely!",
)
# "Here are three things", "here are a few things", "here are 3 things".
_HERE_ARE_THINGS = re.compile(r"^here are (?:\w+ ){0,2}things\b")
_AS_AN_AI = re.compile(
    r"\bas an ai\b|\blanguage model\b"
    r"|\bi(?:'m| am) (?:just )?an? (?:ai|artificial intelligence)\b"
    # Naming herself as one: "I'm Nova, an AI assistant".
    r"|\bi(?:'m| am) \w+, an ai\b"
    r"|\bi don't have feelings\b|\bi do not have feelings\b"
)
# Words in double quotes, straight or curly: quoting the phrase is not saying it.
_QUOTED = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d')
_BULLET_LINE = re.compile(r"(?m)^\s*[-\u2022]\s")
# A numbered item: one or two digits, so a year that starts a line is never one. A
# list needs two of them; "1914. An archduke..." or a lone "3. That's it" is speech.
_NUMBERED_LINE = re.compile(r"(?m)^\s*\d{1,2}[.)]\s")
_DASHES = "\u2013\u2014"
# A sentence ends at a run of stops (an ellipsis or dots, spaced or not count as one),
# with any closing quote or bracket, before a space.
_SENTENCE_END = re.compile(r"[.!?\u2026]+(?:\s+\.+)*[\"')\]\u201d\u2019]*(?=\s)")
# Whose full stop never ends a sentence: a title comes before a name.
_NEVER_ENDS = frozenset({"dr.", "mr.", "mrs.", "ms.", "e.g.", "i.e.", "vs."})
# Whose full stop ends one only when a capital follows: "at 3 p.m. on Friday" is one
# sentence, "at 3 p.m. Bring soup" two.
_ENDS_BEFORE_A_CAPITAL = frozenset({"a.m.", "p.m.", "etc."})
_WORD = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")
MAX_SENTENCES, MAX_WORDS = 3, 45


def _plain(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'").strip().lower()


def sentences(text: str) -> list[str]:
    text = text.strip()
    found: list[str] = []
    start = 0
    for stop in _SENTENCE_END.finditer(text):
        head = text[start : stop.end()]
        last = head.split()[-1].lstrip("\"'(\u201c\u2018").lower() if head.split() else ""
        after = text[stop.end() :].lstrip()[:1]
        if last in _NEVER_ENDS or (last in _ENDS_BEFORE_A_CAPITAL and not after.isupper()):
            continue
        found.append(head.strip())
        start = stop.end()
    found.append(text[start:].strip())
    return [s for s in found if s]


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def banned_opener(reply: str) -> str | None:
    """The banned opener this reply starts with, or None."""
    text = " ".join(_plain(reply).lstrip("\"'*#_ ").split())
    first = (sentences(text) or [""])[0]
    for opener in BANNED_OPENERS:
        spans_two = ". " in opener or ", let" in opener
        if text.startswith(opener) if spans_two else opener in first:
            return opener
    found = _HERE_ARE_THINGS.search(first)
    return found.group(0) if found else None


def as_an_ai(reply: str) -> str | None:
    found = _AS_AN_AI.search(_QUOTED.sub(" ", _plain(reply)))
    return found.group(0) if found else None


def markdown(reply: str) -> str | None:
    """What a spoken reply has that voice.md forbids, or None."""
    for mark, what in (("*", "an asterisk"), ("#", "a hash"), ("`", "a backtick")):
        if mark in reply:
            return what
    if _BULLET_LINE.search(reply) or len(_NUMBERED_LINE.findall(reply)) >= 2:
        return "a list line"
    for i, ch in enumerate(reply):
        if ch in _DASHES:
            between_digits = (
                0 < i < len(reply) - 1 and reply[i - 1].isdigit() and reply[i + 1].isdigit()
            )
            if not between_digits:
                return "an em or en dash"
    return None


def too_long(reply: str, *, long_ok: bool = False) -> str | None:
    """Over three sentences or 45 words when spoken. A prompt that asks for something
    long (`long_ok`) may instead be refused in one line, or offered on the board."""
    n_sentences, n_words = len(sentences(reply)), len(words(reply))
    if n_sentences <= MAX_SENTENCES and n_words <= MAX_WORDS:
        return None
    if long_ok and (n_sentences == 1 or re.search(r"\bboard\b", reply, re.IGNORECASE)):
        return None
    return f"{n_sentences} sentences, {n_words} words"


def repeated_openers(replies: Sequence[str], window: int = 20) -> list[int]:
    """The replies whose first three words match a reply at most `window` before them."""
    last: dict[tuple[str, ...], int] = {}
    flagged: list[int] = []
    for i, r in enumerate(replies):
        key = tuple(w.lower() for w in words(_plain(r))[:3])
        if len(key) < 3:
            continue
        if key in last and i - last[key] <= window:
            flagged.append(i)
        last[key] = i
    return flagged
