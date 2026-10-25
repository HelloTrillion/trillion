"""Words she writes, made fit for a card: emoji and pictographs taken out.

Pure, no I/O. Every widget and block goes through `scrub` before it is checked, so a
cap counts the characters the page will show.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Emoji and pictographs. Text symbols that carry data ($ € £ % ° → ✓ ★ ♥ ⚠ ▲) stay.
# Astral: cards, enclosed symbols, flags, pictographs, skin tones, flag tag characters.
_ASTRAL = r"\U0001f000-\U0001faff\U000e0020-\U000e007f"
# Below U+FFFF only the characters that are emoji by default (Emoji_Presentation).
_BMP_EMOJI = (
    r"\u231a\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd\u25fe\u2614\u2615\u2648-\u2653"
    r"\u267f\u2693\u26a1\u26aa\u26ab\u26bd\u26be\u26c4\u26c5\u26ce\u26d4\u26ea"
    r"\u26f2\u26f3\u26f5\u26fa\u26fd\u2705\u270a\u270b\u2728\u274c\u274e"
    r"\u2753-\u2755\u2757\u2795-\u2797\u27b0\u27bf\u2b1b\u2b1c\u2b50\u2b55"
)
# Any other symbol is an emoji only when written with the emoji selector (❤️, ⚠️).
_ONE = rf"(?:[{_ASTRAL}{_BMP_EMOJI}]|[\u00a9\u00ae\u2000-\u32ff]\ufe0f)\ufe0f?"
# One emoji, or several joined (family, profession, flag-with-text); the joiners and
# selectors go only with an emoji, so scripts that use a joiner keep theirs.
_EMOJI = re.compile(rf"{_ONE}(?:\u200d{_ONE})*")
# 1️⃣: the digit stays, the selector and keycap ring go.
_KEYCAP = re.compile(r"(?<=[0-9#*])\ufe0f?\u20e3")
_GAPS = re.compile(r"[ \t]{2,}")


def strip_emoji(text: str) -> str:
    """The text without emoji or pictographs (and the joiners and selectors inside them)."""
    clean = _EMOJI.sub("", _KEYCAP.sub("", text))
    return text if clean == text else _GAPS.sub(" ", clean)


def scrub(value: Any) -> Any:
    """Every string in a widget or block, however deep, without emoji."""
    if isinstance(value, str):
        return strip_emoji(value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, Mapping):
        return {k: scrub(v) for k, v in value.items()}
    return value
