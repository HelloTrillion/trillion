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

import hashlib
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
        r"\bkevin\b",
    ],
    # A path on a maintainer's machine is private in every file, docs/ included: it
    # names their account. Example paths use a made-up user.
    "machine paths": [
        r"/Users/(?!someone/|you/|me/|<)[A-Za-z0-9._-]+/code/Hello-Trillion",
        r"-Users-(?!someone-)[A-Za-z0-9._]+-code-Hello-Trillion",
    ],
    "businesses": [
        r"Lightbulb Moment Labs(?!, Inc\.)",  # the legal name in LICENSE/NOTICE is fine
        # The owner's other businesses are matched by fingerprint (PRIVATE_NAMES below),
        # so this public file doesn't publish the names it protects.
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

# Private names, kept as fingerprints: the first 20 hex characters of
# sha256(SALT + phrase), where the phrase is the name's words in lower case, joined
# by one space and again with none ("example corp" and "examplecorp"). Text is matched the
# same way, one to three words at a time, whatever the case or punctuation between
# them. Add a name with `python3 scripts/containment_check.py --hash "Some Name"`.
# A fingerprint hides a name from a reader; someone who already guesses a name can
# still test it. Names already in the git history stay there.
SALT = "trillion-containment:"
PRIVATE_NAMES = frozenset(
    {
        "0deb44aa04b3ae0b94e9",
        "0e472f5a23c204f9c41d",
        "12507f7a8c0d7b5f3e0c",
        "3d1e5f68be3460fbeb46",
        "3de1e9fa9f09f1aaa040",
        "5894e4efa93d490ee8b8",
        "6d2d5905b09e24fab109",
        "bb633edb6a2ca50fb230",
        "c7c601acf2cc5b1e3c62",
        "d5acd2208990f7c5a30e",
    }
)
_WORD = re.compile(r"[a-z0-9]+")
_MAX_WORDS = 3


def fingerprint(phrase: str) -> str:
    return hashlib.sha256((SALT + phrase).encode("utf-8")).hexdigest()[:20]


def fingerprints(name: str) -> set[str]:
    """The fingerprints a name is stored as: its words spaced, and run together."""
    words = _WORD.findall(name.lower())
    return {fingerprint(" ".join(words)), fingerprint("".join(words))}


def private_name_hits(text: str, names: frozenset[str] = PRIVATE_NAMES) -> list[int]:
    """Offsets in `text` where a run of one to three words is a private name."""
    found = [(m.start(), m.group()) for m in _WORD.finditer(text.lower())]
    hits: list[int] = []
    for i in range(len(found)):
        for n in range(1, _MAX_WORDS + 1):
            run = [w for _, w in found[i : i + n]]
            if len(run) < n:
                break
            if fingerprint(" ".join(run)) in names or fingerprint("".join(run)) in names:
                hits.append(found[i][0])
                break
    return hits


# Maintainer names belong in these files and nowhere else.
NAME_ALLOWED = {"LICENSE", "NOTICE", "CODEOWNERS", "TRADEMARKS.md"}
NAME_ALLOWED_DIRS = {"docs"}

SKIP_DIRS = {".git", "node_modules", "dist", ".venv", "__pycache__", "models"}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".mp3",
    ".wav",
    ".onnx",
    ".ico",
    ".woff",
    ".woff2",
}


def tracked_files() -> list[Path]:
    """Every file git knows about, so untracked scratch work is ignored."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        paths = [ROOT / p for p in out.split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        paths = [p for p in ROOT.rglob("*") if p.is_file()]

    return [
        p
        for p in paths
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
    "/private/tmp/claude-501/-Users-maintainer-code-Hello-Trillion-Public/x",
    "doppler run -- uv run python",
    "DOPPLER_TOKEN=abc",
    "sk-ant-api03-AAAAAAAAAAAA",
    "xoxb-1234567890-abcdefghij",
    "ghp_AAAAAAAAAAAAAAAAAAAAAAAA",
]
SELFTEST_ALLOWED = [
    "Clone it to /Users/someone/code/Hello-Trillion and run it.",
    "Trillion is a voice-first agent.",
    "We do not depend on a secrets manager such as Doppler or Vault.",
    "The orb turns gold when the microphone is hot.",
]


def selftest() -> int:
    """Prove the patterns catch what they claim to catch."""
    failures: list[str] = []

    for sample in SELFTEST_CAUGHT:
        if not any(
            re.search(p, sample, re.IGNORECASE) for patterns in FORBIDDEN.values() for p in patterns
        ):
            failures.append(f"  MISSED: {sample!r} should be caught but is not")

    for sample in SELFTEST_ALLOWED:
        for category, patterns in FORBIDDEN.items():
            for p in patterns:
                if re.search(p, sample, re.IGNORECASE):
                    failures.append(f"  FALSE POSITIVE: {sample!r} matched {p!r} [{category}]")

    # Private names: a made-up name stands in, so this file never holds a real one.
    fake = frozenset(fingerprints("Example Corp"))
    for sample in ("Example Corp", "examplecorp", "EXAMPLE-CORP", "at ExampleCorp's office"):
        if not private_name_hits(sample, fake):
            failures.append(f"  MISSED: {sample!r} should match a fingerprinted name")
    for sample in ("an example corporation", "examples corp", "example of corp"):
        if private_name_hits(sample, fake):
            failures.append(f"  FALSE POSITIVE: {sample!r} matched a fingerprinted name")
    if len(PRIVATE_NAMES) == 0 or any(not re.fullmatch(r"[0-9a-f]{20}", h) for h in PRIVATE_NAMES):
        failures.append("  PRIVATE_NAMES must be 20-hex-character fingerprints")
    for sample in SELFTEST_ALLOWED:
        if private_name_hits(sample):
            failures.append(f"  FALSE POSITIVE: {sample!r} matched a private name")

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

        for offset in private_name_hits(text):
            line = text.count("\n", 0, offset) + 1
            findings.append(f"  {rel}:{line}  [businesses]  matched a private name")

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
    if len(sys.argv) == 3 and sys.argv[1] == "--hash":
        print("\n".join(sorted(fingerprints(sys.argv[2]))))
        sys.exit(0)
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
