"""Turn written text into something that can be said aloud (VOI-6).

A voice that reads "asterisk asterisk done asterisk asterisk" or spells out a
URL is worse than silence. This strips what only makes sense on a screen and
keeps the words. It never adds any: what she says is what she wrote.
"""

from __future__ import annotations

import re
import unicodedata

_FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_BULLET = re.compile(r"^\s{0,3}(?:[-*•+]|\d{1,3}[.)])\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
_LEFTOVER = re.compile(r"[*_`~#|]+")
_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")
# Symbols, pictographs and the invisible characters that join or modify them.
_NOT_SPOKEN = {"So", "Sk", "Cs", "Co", "Cn"}
_JOINERS = {0x200D, 0xFE0E, 0xFE0F, 0x20E3}


def _is_emoji(ch: str) -> bool:
    code = ord(ch)
    return (
        unicodedata.category(ch) in _NOT_SPOKEN
        or code in _JOINERS
        or 0x1F3FB <= code <= 0x1F3FF  # skin tones
        or 0xE0020 <= code <= 0xE007F  # tag characters
    )


def speakable(text: str) -> str:
    """The same words, without markup. A paragraph break survives as a pause."""
    text = _FENCE.sub(" ", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _TABLE_RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _BULLET.sub("", text)
    for _ in range(3):  # nested emphasis: ***bold italic***
        text = _EMPHASIS.sub(r"\2", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LEFTOVER.sub(" ", text)
    text = "".join(ch for ch in text if not _is_emoji(ch))
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()
