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
    "here are three things",
    "here are a few things",
    "certainly!",
    "absolutely!",
)
_AS_AN_AI = re.compile(
    r"\bas an ai\b|\blanguage model\b|\bi'm (?:just )?an ai\b|\bi am (?:just )?an ai\b"
    r"|\bi don't have feelings\b|\bi do not have feelings\b"
)
_LIST_LINE = re.compile(r"(?m)^\s*(?:[-\u2022]|\d+[.)])\s")
_DASHES = "\u2013\u2014"
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+")
_WORD = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")
MAX_SENTENCES, MAX_WORDS = 3, 45


def _plain(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'").strip().lower()


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def banned_opener(reply: str) -> str | None:
    """The banned opener this reply starts with, or None."""
    text = _plain(reply).lstrip("\"'*#_ ")
    first = (sentences(text) or [""])[0]
    for opener in BANNED_OPENERS:
        spans_two = ". " in opener or ", let" in opener
        if text.startswith(opener) if spans_two else opener in first:
            return opener
    return None


def as_an_ai(reply: str) -> str | None:
    found = _AS_AN_AI.search(_plain(reply))
    return found.group(0) if found else None


def markdown(reply: str) -> str | None:
    """What a spoken reply has that voice.md forbids, or None."""
    for mark, what in (("*", "an asterisk"), ("#", "a hash"), ("`", "a backtick")):
        if mark in reply:
            return what
    if _LIST_LINE.search(reply):
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
    if long_ok and (n_sentences == 1 or "board" in reply.lower()):
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
