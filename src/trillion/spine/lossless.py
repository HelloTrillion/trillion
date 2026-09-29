"""A lossless view of untrusted text: safe to read, exactly reversible (issue #14).

The neutralizer (SPN-6) makes hostile text safe by *deleting* what poses as structure.
That is right for an email body and wrong for a file the model may write back, where
the deletion becomes permanent: a read → overwrite round trip replaced every code fence
and stripped the joiners out of emoji and Persian and Indic text.

`encode` keeps the same guarantees by *escaping* instead. Every character it touches
becomes a visible token, `⟦U+XXXX⟧`, and `decode` turns each token back into its
character. It escapes:

- the token opener `⟦` itself, so a file that already contains tokens survives;
- every invisible and control character the neutralizer would delete (except tab,
  newline and carriage return): nothing hidden reaches the model;
- the `<` of anything shaped like the wrapper's own tag: the text cannot close it;
- on any line that reads as a role marker or chat-template token once invisible
  characters are dropped and compatibility forms folded, the punctuation that forms
  it (`: < > | [ ]` and their full-width forms): the line no longer reads as one.

Code fences, full-width letters and ordinary text pass through untouched.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from trillion.spine.neutralize import CONTROL, INVISIBLE, ROLE

OPEN, CLOSE = "⟦", "⟧"  # ⟦ ⟧
_TOKEN = re.compile(f"{OPEN}U\\+([0-9A-F]{{4,6}}){CLOSE}")
_WRAPPER_TAG = re.compile(r"<(?=\s*/?\s*untrusted_tool_result)", re.IGNORECASE)
_STRUCTURAL = re.compile("[:<>|\\[\\]\uff1a\uff1c\uff1e\uff5c\uff3b\uff3d]")


@dataclass(frozen=True)
class Encoded:
    text: str
    escaped: int


def _token(c: str) -> str:
    return f"{OPEN}U+{ord(c):04X}{CLOSE}"


def _escape(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda m: "".join(_token(c) for c in m.group(0)), text)


_WRAPPER_IN_FOLD = re.compile(r"<\s*/?\s*untrusted_tool_result", re.IGNORECASE)


def _poses_as_structure(line: str) -> bool:
    """Judged on what a reader that ignores invisible characters and folds compatibility
    forms would see — the same view the neutralizer checks — so a marker split by a soft
    hyphen or written in full-width letters is still caught."""
    folded = unicodedata.normalize("NFKC", INVISIBLE.sub("", line))
    return ROLE.search(folded) is not None or _WRAPPER_IN_FOLD.search(folded) is not None


def encode(text: str) -> Encoded:
    original_lines = text.split("\n")
    out = text.replace(OPEN, _token(OPEN))
    out = _escape(INVISIBLE, out)
    out = _escape(CONTROL, out)
    out = _WRAPPER_TAG.sub(lambda _: _token("<"), out)
    lines = out.split("\n")  # nothing above adds or removes a newline
    for i, original in enumerate(original_lines):
        if _poses_as_structure(original):
            lines[i] = _escape(_STRUCTURAL, lines[i])
    result = "\n".join(lines)
    return Encoded(text=result, escaped=len(_TOKEN.findall(result)))


def decode(text: str) -> str:
    """Turn every valid token back into its character; leave anything else alone."""

    def back(m: re.Match[str]) -> str:
        point = int(m.group(1), 16)
        if point > 0x10FFFF or 0xD800 <= point <= 0xDFFF:  # not a character UTF-8 can hold
            return m.group(0)
        return chr(point)

    return _TOKEN.sub(back, text)
