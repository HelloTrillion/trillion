#!/usr/bin/env python3
"""Fail the build if an agent-facing string hardcodes the brand name (PER-1a).

The brand is Trillion; the agent's name belongs to the user and reaches every
prompt as {agent_name}. A prompt that says "Trillion" makes a user's "Nova"
introduce herself by the wrong name.

    python3 scripts/check_agent_name.py            # scan the repository
    python3 scripts/check_agent_name.py --selftest # prove the rule works
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = re.compile(r"\bTrillion\b")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
TEXT_SUFFIXES = {".md", ".txt"}


def agent_facing_files(root: Path) -> list[Path]:
    files = [p for p in (root / "persona").rglob("*") if p.is_file()]
    for prompts in (root / "src" / "trillion").rglob("prompts"):
        if prompts.is_dir():
            files.extend(p for p in prompts.rglob("*") if p.is_file())
    return sorted(files)


def text_hits(text: str) -> list[int]:
    stripped = HTML_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return [stripped.count("\n", 0, m.start()) + 1 for m in BRAND.finditer(stripped)]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def python_hits(source: str) -> list[int]:
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and BRAND.search(node.value)
    )


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in agent_facing_files(root):
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            lines = python_hits(text)
        elif path.suffix in TEXT_SUFFIXES:
            lines = text_hits(text)
        else:
            continue
        findings.extend(f"  {rel}:{n}  hardcodes the brand; use {{agent_name}}" for n in lines)
    return findings


def selftest() -> int:
    failures: list[str] = []
    if text_hits("You are Trillion.") != [1]:
        failures.append("  MISSED: plain markdown mention")
    if text_hits("<!-- default is Trillion -->\nYou are {agent_name}.") != []:
        failures.append("  FALSE POSITIVE: mention inside an HTML comment")
    if text_hits("A trillion stars.") != []:
        failures.append("  FALSE POSITIVE: lowercase common noun")
    if python_hits('"""Prompts for Trillion."""\nX = "hi"\n') != []:
        failures.append("  FALSE POSITIVE: module docstring")
    if python_hits('X = f"I am Trillion, {y}"\n') != [1]:
        failures.append("  MISSED: f-string literal part")
    if python_hits('X = "I am {agent_name}"\n') != []:
        failures.append("  FALSE POSITIVE: placeholder string")
    if failures:
        print("Agent-name self-test FAILED:\n" + "\n".join(failures))
        return 1
    print("Agent-name self-test passed.")
    return 0


def main() -> int:
    findings = scan(ROOT)
    if findings:
        print("Agent-name check FAILED (PER-1a):\n" + "\n".join(findings))
        return 1
    print("Agent-name check passed: no agent-facing string hardcodes the brand.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
