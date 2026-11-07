#!/usr/bin/env python3
"""Fail the build if a stylesheet has an unclosed or stray brace.

A browser doesn't report a missing `}`: every rule after it is quietly swallowed into
the one left open, and whole features lose their styles. That happened on 2026-11-07
when a merge kept both sides of a conflict and dropped the brace they shared. The
web app uses no CSS nesting, so a rule opened inside another rule (not an @-rule)
is always a missing brace.

    python3 scripts/check_css.py [--selftest]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"node_modules", "dist", ".vite", "target"}
COMMENT = re.compile(r"/\*.*?\*/", re.S)
STRING = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")


def problems(text: str) -> list[str]:
    """Each brace problem, as 'line N: what'. Comments and strings are ignored."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731 - keeps line numbers
    text = STRING.sub(blank, COMMENT.sub(blank, text))
    found: list[str] = []
    stack: list[tuple[int, str]] = []
    for n, line in enumerate(text.split("\n"), 1):
        start = 0
        for i, ch in enumerate(line):
            if ch == "{":
                head = line[start:i].strip() or (stack[-1][1] if stack else "")
                if stack and not stack[-1][1].startswith("@"):
                    found.append(
                        f"line {n}: {head!r} opens inside {stack[-1][1]!r}"
                        f" (line {stack[-1][0]}), which never closed"
                    )
                stack.append((n, head))
                start = i + 1
            elif ch == "}":
                if not stack:
                    found.append(f"line {n}: a '}}' with nothing open")
                else:
                    stack.pop()
                start = i + 1
            elif ch == ";":
                start = i + 1
    found.extend(f"line {n}: {head!r} never closes" for n, head in stack)
    return found


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for top in ("apps", "packages"):
        base = root / top
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.css")):
            if SKIP & set(path.parts):
                continue
            rel = path.relative_to(root)
            findings.extend(f"  {rel} {p}" for p in problems(path.read_text(encoding="utf-8")))
    return findings


def selftest() -> int:
    cases = {
        ".a { color: red; }\n.b { color: blue; }": 0,
        "@media (max-width: 9px) {\n  .a { color: red; }\n}": 0,
        "/* { */ .a { content: '}'; }": 0,
        ".a {\n  color: red;\n.b { color: blue; }": 2,  # .b inside .a, and .a never closes
        ".a { color: red; }\n}": 1,
    }
    failures = [
        f"  {src!r}: expected {n}, got {problems(src)}"
        for src, n in cases.items()
        if len(problems(src)) != n
    ]
    if failures:
        print("CSS check self-test FAILED:\n" + "\n".join(failures))
        return 1
    print("CSS check self-test passed.")
    return 0


def main() -> int:
    findings = scan(ROOT)
    if findings:
        print(
            "CSS check FAILED: a brace is missing or extra, so the rules after it are lost.\n"
            + "\n".join(findings)
        )
        return 1
    print("CSS check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
