"""SPN-2f: what counts as a plain no.

The host cancels a waiting card on a plain refusal without asking the model, so the
rule is narrow on purpose. The whole turn must be refusal words, give or take filler
and politeness. Anything else ("no, wait, make it say hello") is the model's to read.
A yes is never read here: approving stays the model's call, under the gate's rules.
"""

from __future__ import annotations

import re

# Words that add nothing to a refusal. Not "oh": "oh no" is dismay, not an answer.
_FILLER = frozenset("um umm uh uhh er erm hmm hm mm ah well actually please sorry".split())

# Each a whole refusal, apostrophes dropped ("don't" is "dont").
_PHRASES: tuple[tuple[str, ...], ...] = tuple(
    sorted(
        (
            tuple(p.split())
            for p in (
                "no",
                "nope",
                "nah",
                "no thanks",
                "no thank you",
                "dont",
                "dont do that",
                "dont do it",
                "do not",
                "do not do that",
                "do not do it",
                "cancel",
                "cancel that",
                "cancel it",
                "never mind",
                "nevermind",
                "forget it",
                "forget that",
                "forget about it",
            )
        ),
        key=len,
        reverse=True,
    )
)

_APOSTROPHES = re.compile("['`\N{LEFT SINGLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}]")
# Only plain ASCII punctuation separates words. Anything else stays in a word and
# so is not a refusal word: digits, other scripts, emoji, accents.
_BETWEEN = re.compile(r"[\s.,!;:\"()\[\]-]+|\N{HORIZONTAL ELLIPSIS}")
_QUESTION = ("?", "\N{FULLWIDTH QUESTION MARK}", "\N{INVERTED QUESTION MARK}")
_WORD = re.compile(r"[a-z]+")


def _words(text: str) -> list[str] | None:
    """The words that matter, or None when any piece is not a plain English word."""
    flat = _APOSTROPHES.sub("", text.lower())
    words = [w for w in _BETWEEN.split(flat) if w]
    if not all(_WORD.fullmatch(w) for w in words):
        return None
    return [w for w in words if w not in _FILLER]


def plain_no(text: str) -> bool:
    """True when the whole of `text` is a plain refusal, such as "no" or "never mind".

    "Stop" is not one: said over her it silences her, and that never cancels work
    (VOI-15c)."""
    if any(q in text for q in _QUESTION):
        # "No?" asks; it does not answer.
        return False
    words = _words(text)
    if not words:
        return False
    i = 0
    while i < len(words):
        for phrase in _PHRASES:
            if tuple(words[i : i + len(phrase)]) == phrase:
                i += len(phrase)
                break
        else:
            return False
    return True
