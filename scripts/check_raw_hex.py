#!/usr/bin/env python3
"""Fail the build if a colour is hardcoded outside tokens.css (GAL-11).

One file holds every UI colour so the light theme is real and a rebrand is
one diff. Shader colours are linear-RGB floats in states.ts, not hex.

    python3 scripts/check_raw_hex.py [--selftest]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEX = re.compile(r"(?<![&\w])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
SUFFIXES = {".ts", ".tsx", ".css", ".html", ".glsl"}
SKIP = {"node_modules", "dist", ".vite", "target"}  # target: the desktop app's Rust build output
ALLOWED_NAME = "tokens.css"


def hits(text: str) -> list[int]:
    return [text.count("\n", 0, m.start()) + 1 for m in HEX.finditer(text)]


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for top in ("apps", "packages"):
        base = root / top
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if (
                not path.is_file()
                or path.suffix not in SUFFIXES
                or path.name == ALLOWED_NAME
                or SKIP & set(path.parts)
            ):
                continue
            rel = path.relative_to(root)
            text = path.read_text(encoding="utf-8")
            findings.extend(f"  {rel}:{n}  raw hex colour; use a token" for n in hits(text))
    return findings


def selftest() -> int:
    cases = {
        "color: #2DD4A8;": 1,
        "color: #fff;": 1,
        "background: #0E0F13cc;": 1,
        "&#123; entity": 0,
        "color: var(--accent);": 0,
        "#main-heading { }": 0,
    }
    failures = [f"  {src!r}: expected {n}" for src, n in cases.items() if len(hits(src)) != n]
    if failures:
        print("Raw-hex self-test FAILED:\n" + "\n".join(failures))
        return 1
    print("Raw-hex self-test passed.")
    return 0


def main() -> int:
    findings = scan(ROOT)
    if findings:
        print("Raw-hex check FAILED (GAL-11):\n" + "\n".join(findings))
        return 1
    print("Raw-hex check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
