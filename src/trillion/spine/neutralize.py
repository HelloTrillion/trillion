"""SPN-6: strip what hostile text uses to pose as structure, and say what was found."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    kind: str
    sample: str


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_ROLE = re.compile(
    r"(?im)^[ \t]*(?:system|assistant|user|developer)[ \t]*:"
    r"|<\|?\s*(?:im_start|im_end|system|assistant|user)\s*\|?>"
    r"|\[/?INST\]"
)
_FENCE = re.compile(r"`{3,}|~{3,}")


def _sub(pattern: re.Pattern[str], kind: str, repl: str, text: str, out: list[Finding]) -> str:
    def replace(m: re.Match[str]) -> str:
        out.append(Finding(kind=kind, sample=m.group(0)[:40]))
        return repl

    return pattern.sub(replace, text)


def neutralize(text: str) -> tuple[str, tuple[Finding, ...]]:
    findings: list[Finding] = []
    text = _sub(_CONTROL, "control", "", text, findings)
    text = _sub(_INVISIBLE, "invisible", "", text, findings)
    text = _sub(_ROLE, "role_marker", "[role marker removed]", text, findings)
    text = _sub(_FENCE, "fence", "[fence removed]", text, findings)
    return text, tuple(findings)
