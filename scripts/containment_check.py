#!/usr/bin/env python3
"""Fail the build if anything private made it into the public repository.

Trillion V2 was extracted from a private single-user codebase. This check is
the guard on that boundary: a maintainer porting a file must not bring along
the owner's identity, their businesses, their machine paths, or a credential.

Run it locally the same way CI does:

    python3 scripts/containment_check.py

Exit code 0 means clean; 1 means something needs to come out.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Identifiers from the private codebase. A hit is a bug, not a style problem.
FORBIDDEN = {
    # Matched case-insensitively like every other category: "KevinFremon" and
    # "/Users/KEVIN/..." slipped through while this block was case-sensitive.
    "owner identity": [
        r"kevin\s*fremon",
        r"/Users/[A-Za-z0-9._-]+/code/Hello-Trillion",
        r"\bkevin\b",
    ],
    "businesses": [
        r"Lightbulb Moment Labs(?!, Inc\.)",  # the legal name in LICENSE/NOTICE is fine
        r"SuperSchema",
        r"Clean\s?Pro",
        r"Future Proof",
        r"Creating Space",
        r"Locked In",
        r"Whitefish",
    ],
    "private infrastructure": [
        r"trillion-cloud",
        # Actual usage, not the word: naming Doppler in a "we do not depend on
        # this" list is legitimate and must not fail the build.
        r"doppler\s+(run|secrets|setup|login)",
        r"DOPPLER_(TOKEN|PROJECT|CONFIG)",
        r"hellotrillion\.ai/collab",
    ],
    "credentials": [
        r"sk-ant-[A-Za-z0-9-]{10,}",
        r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
        r"AIza[0-9A-Za-z_-]{30,}",
        r"sk_live_[0-9a-zA-Z]{10,}",
        r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----",
        r"ghp_[A-Za-z0-9]{20,}",
    ],
}

# Maintainer names belong in these files and nowhere else.
NAME_ALLOWED = {"LICENSE", "NOTICE", "CODEOWNERS", "TRADEMARKS.md"}
NAME_ALLOWED_DIRS = {"docs"}

SKIP_DIRS = {".git", "node_modules", "dist", ".venv", "__pycache__", "models"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".wav", ".onnx", ".ico", ".woff", ".woff2"}


def tracked_files() -> list[Path]:
    """Every file git knows about, so untracked scratch work is ignored."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        paths = [ROOT / p for p in out.split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        paths = [p for p in ROOT.rglob("*") if p.is_file()]

    return [
        p for p in paths
        if p.is_file()
        and not SKIP_DIRS & set(p.relative_to(ROOT).parts)
        and p.suffix.lower() not in SKIP_SUFFIXES
        and p.name != Path(__file__).name
    ]


def exempt(rel: Path, category: str) -> bool:
    """Maintainer names are allowed in legal files and in docs/."""
    if category != "owner identity":
        return False
    return rel.name in NAME_ALLOWED or (rel.parts and rel.parts[0] in NAME_ALLOWED_DIRS)


# Fixtures that must be caught, and text that must not be. A reviewer found
# real misses here (case-sensitive owner patterns), so the check now checks
# itself in CI.
SELFTEST_CAUGHT = [
    "kevinfremon",
    "KevinFremon",
    "Kevin Fremon",
    "/Users/KEVIN/code/Hello-Trillion",
    "/Users/kevinfremon/code/Hello-Trillion",
    "superschema",
    "SuperSchema",
    "doppler run -- uv run python",
    "DOPPLER_TOKEN=abc",
    "sk-ant-api03-AAAAAAAAAAAA",
    "xoxb-1234567890-abcdefghij",
    "ghp_AAAAAAAAAAAAAAAAAAAAAAAA",
]
SELFTEST_ALLOWED = [
    "Trillion is a voice-first agent.",
    "We do not depend on a secrets manager such as Doppler or Vault.",
    "The orb turns gold when the microphone is hot.",
]


def selftest() -> int:
    """Prove the patterns catch what they claim to catch."""
    failures: list[str] = []

    for sample in SELFTEST_CAUGHT:
        if not any(
            re.search(p, sample, re.IGNORECASE)
            for patterns in FORBIDDEN.values()
            for p in patterns
        ):
            failures.append(f"  MISSED: {sample!r} should be caught but is not")

    for sample in SELFTEST_ALLOWED:
        for category, patterns in FORBIDDEN.items():
            for p in patterns:
                if re.search(p, sample, re.IGNORECASE):
                    failures.append(
                        f"  FALSE POSITIVE: {sample!r} matched {p!r} [{category}]"
                    )

    if failures:
        print("Containment self-test FAILED:\n" + "\n".join(failures))
        return 1

    print(
        f"Containment self-test passed: {len(SELFTEST_CAUGHT)} caught, "
        f"{len(SELFTEST_ALLOWED)} allowed."
    )
    return 0


def main() -> int:
    findings: list[str] = []

    for path in tracked_files():
        rel = path.relative_to(ROOT)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for category, patterns in FORBIDDEN.items():
            if exempt(rel, category):
                continue
            for pattern in patterns:
                for m in re.finditer(pattern, text, re.IGNORECASE):
                    line = text.count("\n", 0, m.start()) + 1
                    findings.append(f"  {rel}:{line}  [{category}]  matched {pattern!r}")

    if findings:
        print("Containment check FAILED. Private material must not ship:\n")
        print("\n".join(findings))
        print(
            "\nRemove it, or — if this is a legitimate false positive — narrow the"
            "\npattern in scripts/containment_check.py in the same pull request."
        )
        return 1

    print("Containment check passed: no private identifiers or credentials found.")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
