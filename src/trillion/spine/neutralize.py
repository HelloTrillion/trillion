"""SPN-6: strip what hostile text uses to pose as structure, and say what was found.

Order matters. Invisible characters go first, so a soft hyphen or a combining joiner
cannot split a marker the later rules would otherwise see. Then NFKC folds
compatibility forms (full-width `system` and colon become ASCII). Only then do the
control, role-marker and fence rules run, on the text a model would effectively read.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    kind: str
    sample: str


# Format characters live only in these planes (a unit test re-checks the whole range).
_CF_PLANES = (range(0x0000, 0x20000), range(0xE0000, 0xF0000))


def _invisible_class() -> str:
    """Every Unicode format (Cf) character, plus combining grapheme joiner and variation
    selectors: soft hyphen, zero-width and bidi controls, word joiners, BOM, and the tag
    characters (U+E0000-E007F) used to smuggle hidden ASCII."""
    points = [c for r in _CF_PLANES for c in r if unicodedata.category(chr(c)) == "Cf"]
    # The whole tag block, unassigned code points included: E0002-E001F are not Cf.
    points += [0x034F, *range(0xFE00, 0xFE10), *range(0xE0000, 0xE0080), *range(0xE0100, 0xE01F0)]
    ranges: list[tuple[int, int]] = []
    for c in sorted(set(points)):
        if ranges and ranges[-1][1] == c - 1:
            ranges[-1] = (ranges[-1][0], c)
        else:
            ranges.append((c, c))
    return "".join(
        re.escape(chr(a)) if a == b else f"{re.escape(chr(a))}-{re.escape(chr(b))}"
        for a, b in ranges
    )


_INVISIBLE = re.compile(f"[{_invisible_class()}]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ROLE = re.compile(
    r"(?im)^[ \t]*(?:human|system|assistant|user|developer)[ \t]*:"
    r"|<\|?\s*(?:im_start|im_end|system|assistant|user)\s*\|?>"
    r"|\[/?INST\]"
)
_FENCE = re.compile(r"`{3,}|~{3,}")

# Shared with the lossless view (lossless.py), which escapes what this module deletes.
INVISIBLE, CONTROL, ROLE = _INVISIBLE, _CONTROL, _ROLE


def _sub(pattern: re.Pattern[str], kind: str, repl: str, text: str, out: list[Finding]) -> str:
    def replace(m: re.Match[str]) -> str:
        out.append(Finding(kind=kind, sample=m.group(0)[:40]))
        return repl

    return pattern.sub(replace, text)


def neutralize(text: str) -> tuple[str, tuple[Finding, ...]]:
    findings: list[Finding] = []
    text = _sub(_INVISIBLE, "invisible", "", text, findings)
    text = unicodedata.normalize("NFKC", text)
    text = _sub(_CONTROL, "control", "", text, findings)
    text = _sub(_ROLE, "role_marker", "[role marker removed]", text, findings)
    text = _sub(_FENCE, "fence", "[fence removed]", text, findings)
    return text, tuple(findings)
