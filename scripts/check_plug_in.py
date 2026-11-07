#!/usr/bin/env python3
"""Fail the build if a new feature appends to a shared file instead of plugging in.

Features built in parallel all used to add one line to the same few lists, so nearly
every pull request conflicted with the next (docs/specs/2026-11-07-plug-in-features.md).
Each hook in a shared file is counted here, and a count may only go down. When you
move an old hook out, lower its number below in the same pull request.

    python3 scripts/check_plug_in.py [--selftest]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Hook:
    path: str
    what: str
    pattern: str | None  # None: count lines
    most: int
    instead: str


HOOKS = (
    Hook(
        "apps/web/src/main.ts",
        "Settings sections added by hand (panel.add)",
        r"panel\.add\(",
        15,
        "add a file to apps/web/src/features/ that exports `feature` and calls panel.add"
        " with `after:` (see features/memories.ts)",
    ),
    Hook(
        "apps/web/src/main.ts",
        "imports",
        r"(?m)^import ",
        51,
        "import your module from a file in apps/web/src/features/, which loads itself",
    ),
    Hook(
        "apps/web/src/main.ts",
        "message handlers called by hand (.handle(msg))",
        r"\.handle\(msg",
        3,
        "in your feature's setup, call ctx.on((msg) => …)",
    ),
    Hook(
        "apps/web/src/app.css",
        "lines",
        None,
        8479,
        "put your styles in a CSS file beside your feature and import it from there"
        " (see features/memories.css)",
    ),
    Hook(
        "apps/web/src/icons.ts",
        "icons",
        r"(?m)^  [A-Za-z_-]+: \[",
        31,
        "call addIcon(name, paths) from your feature's file",
    ),
    Hook(
        "src/trillion/api/app.py",
        "routers mounted by hand (include_router)",
        r"include_router\(",
        17,
        "end your router's module with `ROUTES = (create_my_router,)` (trillion/api/routes.py)",
    ),
    Hook(
        "src/trillion/api/runtime.py",
        "tools bound by hand (.bind)",
        r"\.bind\(",
        13,
        "give your tool `async def attach(self, rt)` (see tools/roast.py)",
    ),
)

# Migrations with a three-digit number stop here. New ones are named by the time
# they were written (YYYYMMDDHHMM_name.sql), so two branches can't take the same one.
LAST_NUMBERED = 22
MIGRATIONS = "src/trillion/storage/migrations"
_NUMBERED = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def count(text: str, pattern: str | None) -> int:
    if pattern is None:
        return text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    return len(re.findall(pattern, text))


def late_numbered(filenames: list[str]) -> list[str]:
    """Three-digit migrations newer than the last one allowed."""
    return sorted(
        n for n in filenames if (m := _NUMBERED.match(n)) and int(m.group(1)) > LAST_NUMBERED
    )


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for hook in HOOKS:
        path = root / hook.path
        if not path.exists():
            continue
        n = count(path.read_text(encoding="utf-8"), hook.pattern)
        if n > hook.most:
            findings.append(
                f"  {hook.path}: {n} {hook.what}, more than {hook.most}."
                f"\n    Instead: {hook.instead}."
            )
    folder = root / MIGRATIONS
    if folder.exists():
        for name in late_numbered([p.name for p in folder.iterdir()]):
            findings.append(
                f"  {MIGRATIONS}/{name}: a new migration takes the time it was written,"
                " not the next number.\n    Instead: rename it YYYYMMDDHHMM_name.sql."
            )
    return findings


def selftest() -> int:
    failures: list[str] = []
    if count("a\nb\n", None) != 2 or count("a\nb", None) != 2:
        failures.append("  line count")
    if count("panel.add(x); panel.add(y)", r"panel\.add\(") != 2:
        failures.append("  pattern count")
    if count("  memories: ['M1'],\n  day: [\n", r"(?m)^  [A-Za-z_-]+: \[") != 2:
        failures.append("  icon count")
    got = late_numbered(["022_memory_notes.sql", "023_next.sql", "202611071200_later.sql"])
    if got != ["023_next.sql"]:
        failures.append(f"  late numbered migration: {got}")
    if failures:
        print("Plug-in check self-test FAILED:\n" + "\n".join(failures))
        return 1
    print("Plug-in check self-test passed.")
    return 0


def main() -> int:
    findings = scan(ROOT)
    if findings:
        print(
            "Plug-in check FAILED: a feature was added to a shared file. That makes every"
            " other open pull request conflict.\n" + "\n".join(findings)
        )
        return 1
    print("Plug-in check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
