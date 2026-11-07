#!/usr/bin/env python3
"""Trillion's version, in every place it is written, and what a release tag means.

Versions are CalVer: a tag is `vYYYY.M.PATCH` (stable) or `vYYYY.M.PATCH-beta.N`
(beta). Old `v0.X.Y` tags are still read, so history stays valid. Each tag is
written three ways:

    tag                  Python (PEP 440)   npm and the desktop app (SemVer)
    v2026.11.0           2026.11.0          2026.11.0
    v2026.11.0-beta.1    2026.11.0b1        2026.11.0-beta.1
    (a dev build, #57)   2026.11.1.dev57    2026.11.1-dev.57

A dev build is a build of main, never tagged and never on PyPI. Its version sorts
after the version on main and before the next one.

    python3 scripts/version.py                     print the version (PEP 440)
    python3 scripts/version.py --semver            ...as SemVer, for Tauri and npm
    python3 scripts/version.py set 2026.11.0-beta.1    set it everywhere (a tag works too)
    python3 scripts/version.py dev N               set it to dev build N of this version
    python3 scripts/version.py check               every place agrees
    python3 scripts/version.py check-tag v2026.11.0    the tag matches; prints its channel
    python3 scripts/version.py --selftest

Standard library only, so CI runs it with any Python 3.11 or later.
"""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Where the version is written. The desktop app's own files (tauri.conf.json,
# Cargo.toml) are not here: a release build stamps the version in with
# `tauri build --config`, so theirs is only a placeholder.
PYPROJECT = "pyproject.toml"
INIT = "src/trillion/__init__.py"
LOCK = "uv.lock"
PACKAGES = ("packages/galaxy/package.json", "apps/web/package.json", "apps/desktop/package.json")

_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")
_PEP440 = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:b(\d+))?(?:\.dev(\d+))?$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(beta|dev)\.(\d+))?$")


class VersionError(Exception):
    pass


@total_ordering
@dataclass(frozen=True)
class Version:
    year: int
    month: int
    patch: int
    beta: int | None = None
    dev: int | None = None

    def _key(self) -> tuple[float, ...]:
        # A beta sorts before its stable version; a dev build before what it leads to.
        beta = math.inf if self.beta is None else self.beta
        dev = math.inf if self.dev is None else self.dev
        return (self.year, self.month, self.patch, beta, dev)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    @property
    def channel(self) -> str:
        if self.dev is not None:
            return "dev"
        return "beta" if self.beta is not None else "stable"

    def pep440(self) -> str:
        out = f"{self.year}.{self.month}.{self.patch}"
        if self.beta is not None:
            out += f"b{self.beta}"
        if self.dev is not None:
            out += f".dev{self.dev}"
        return out

    def semver(self) -> str:
        out = f"{self.year}.{self.month}.{self.patch}"
        if self.dev is not None:
            return f"{out}-dev.{self.dev}"
        return f"{out}-beta.{self.beta}" if self.beta is not None else out

    def tag(self) -> str:
        if self.dev is not None:
            raise VersionError(f"{self.pep440()} is a dev build, which is never tagged")
        return "v" + self.semver()

    def dev_build(self, n: int) -> Version:
        """Dev build `n` of main at this version: after it, before the next one."""
        if self.dev is not None:
            raise VersionError(f"{self.pep440()} is already a dev build")
        if self.beta is not None:
            return Version(self.year, self.month, self.patch, self.beta + 1, n)
        return Version(self.year, self.month, self.patch + 1, None, n)


def _checked(v: Version, text: str) -> Version:
    # Legacy 0.X.Y, or CalVer with a real month. A year is four digits.
    if v.year != 0 and not (1000 <= v.year <= 9999 and 1 <= v.month <= 12):
        raise VersionError(f"{text}: not CalVer (YYYY.M.PATCH, month 1 to 12) or 0.X.Y")
    if text.lstrip("v").split(".")[1].startswith("0") and v.month != 0:
        raise VersionError(f"{text}: write the month without a leading zero")
    return v


def parse(text: str) -> Version:
    """A tag, a PEP 440 version or a SemVer version, in any of the forms above."""
    text = text.strip()
    if m := _TAG.match(text):
        y, mo, p, b = m.groups()
        return _checked(Version(int(y), int(mo), int(p), int(b) if b else None), text)
    if m := _PEP440.match(text):
        y, mo, p, b, d = m.groups()
        v = Version(int(y), int(mo), int(p), int(b) if b else None, int(d) if d else None)
        return _checked(v, text)
    if m := _SEMVER.match(text):
        y, mo, p, kind, n = m.groups()
        beta = int(n) if kind == "beta" else None
        dev = int(n) if kind == "dev" else None
        return _checked(Version(int(y), int(mo), int(p), beta, dev), text)
    raise VersionError(
        f"{text!r} is not a version: use YYYY.M.PATCH or YYYY.M.PATCH-beta.N "
        "(such as 2026.11.0 or 2026.11.0-beta.1)"
    )


def parse_tag(tag: str) -> Version:
    if not tag.startswith("v") or not _TAG.match(tag):
        raise VersionError(
            f"tag {tag!r} is not a release tag: use vYYYY.M.PATCH or vYYYY.M.PATCH-beta.N "
            "(such as v2026.11.0 or v2026.11.0-beta.1)"
        )
    return parse(tag)


# Reading and writing each place. Each pattern has the version as group 2.
_PY_PROJECT = re.compile(r'(?m)^(version = ")([^"]*)(")')
_PY_INIT = re.compile(r'(?m)^(__version__ = ")([^"]*)(")')
_PY_LOCK = re.compile(r'(?m)(^name = "trillion"\nversion = ")([^"]*)(")')
_JSON = re.compile(r'(?m)^(  "version": ")([^"]*)(")')


def _places(root: Path) -> list[tuple[str, re.Pattern[str], bool]]:
    """Each file, its pattern, and whether it takes the npm (SemVer) form."""
    out: list[tuple[str, re.Pattern[str], bool]] = [
        (PYPROJECT, _PY_PROJECT, False),
        (INIT, _PY_INIT, False),
        (LOCK, _PY_LOCK, False),
    ]
    out += [(p, _JSON, True) for p in PACKAGES]
    return [(p, pat, npm) for p, pat, npm in out if (root / p).is_file()]


def read(root: Path = ROOT) -> dict[str, str]:
    found: dict[str, str] = {}
    for rel, pattern, _ in _places(root):
        m = pattern.search((root / rel).read_text(encoding="utf-8"))
        if not m:
            raise VersionError(f"{rel}: no version found")
        found[rel] = m.group(2)
    return found


def current(root: Path = ROOT) -> Version:
    """The version in pyproject.toml, which every other place follows."""
    return parse(read(root)[PYPROJECT])


def write(v: Version, root: Path = ROOT) -> list[str]:
    changed: list[str] = []
    for rel, pattern, npm in _places(root):
        path = root / rel
        text = path.read_text(encoding="utf-8")
        new = v.semver() if npm else v.pep440()
        out, n = pattern.subn(lambda m, new=new: m.group(1) + new + m.group(3), text, count=1)
        if n != 1:
            raise VersionError(f"{rel}: no version found")
        if out != text:
            path.write_text(out, encoding="utf-8", newline="\n")
            changed.append(rel)
    return changed


def check(root: Path = ROOT) -> list[str]:
    """What disagrees with pyproject.toml. Empty when every place agrees."""
    found = read(root)
    v = parse(found[PYPROJECT])
    wrong = []
    for rel, _, npm in _places(root):
        want = v.semver() if npm else v.pep440()
        if found[rel] != want:
            wrong.append(f"{rel} says {found[rel]}, not {want}")
    return wrong


def check_tag(tag: str, root: Path = ROOT) -> Version:
    """The version a tag names, if pyproject.toml and the package say the same."""
    v = parse_tag(tag)
    found = read(root)
    for rel in (PYPROJECT, INIT):
        if found[rel] != v.pep440():
            raise VersionError(
                f"tag {tag} needs version {v.pep440()} in {rel}, which says {found[rel]}. "
                f"Run: python3 scripts/version.py set {tag}, merge, then tag again"
            )
    return v


def _fixture(root: Path, version: str = "0.0.2", npm: str = "0.0.1") -> None:
    (root / "src/trillion").mkdir(parents=True)
    (root / PYPROJECT).write_text(f'[project]\nname = "trillion"\nversion = "{version}"\n')
    (root / INIT).write_text(f'"""Trillion."""\n\n__version__ = "{version}"\n')
    (root / LOCK).write_text(
        f'[[package]]\nname = "tqdm"\nversion = "4.0"\n\n'
        f'[[package]]\nname = "trillion"\nversion = "{version}"\nsource = {{ editable = "." }}\n'
    )
    for rel in PACKAGES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        nested = '  "deps": {\n    "version": "9"\n  }\n'
        (root / rel).write_text(f'{{\n  "name": "x",\n  "version": "{npm}",\n{nested}}}\n')


def selftest() -> int:
    failures: list[str] = []

    def expect(what: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"  {what}: got {got!r}, want {want!r}")

    def refused(text: str) -> None:
        try:
            parse(text)
        except VersionError:
            return
        failures.append(f"  {text!r} should be refused")

    stable, beta = parse("v2026.11.0"), parse("v2026.11.0-beta.1")
    expect("stable pep440", stable.pep440(), "2026.11.0")
    expect("beta pep440", beta.pep440(), "2026.11.0b1")
    expect("beta semver", beta.semver(), "2026.11.0-beta.1")
    expect("beta tag", beta.tag(), "v2026.11.0-beta.1")
    expect("channels", (stable.channel, beta.channel), ("stable", "beta"))
    expect("pep440 round trip", parse("2026.11.0b1"), beta)
    expect("semver round trip", parse("2026.11.0-beta.1"), beta)
    expect("legacy", parse("v0.0.2").pep440(), "0.0.2")
    dev = stable.dev_build(57)
    expect("dev pep440", dev.pep440(), "2026.11.1.dev57")
    expect("dev semver", dev.semver(), "2026.11.1-dev.57")
    expect("dev of a beta", beta.dev_build(3).pep440(), "2026.11.0b2.dev3")
    expect("dev round trip", parse("2026.11.1.dev57"), dev)
    expect("dev channel", dev.channel, "dev")
    # Order: a beta before its stable; a dev build after the version it came from.
    expect("beta < stable", beta < stable, True)
    expect("stable < dev < next", stable < dev < parse("2026.11.1"), True)
    expect("beta < dev of beta < next beta", beta < beta.dev_build(1) < parse("2026.11.0b2"), True)
    expect("legacy < calver", parse("0.0.2") < beta, True)
    for bad in ("2026.13.0", "2026.011.0", "26.11.0", "2026.11", "v2026.11.0-rc.1", "latest"):
        refused(bad)
    try:
        parse_tag("2026.11.0")
        failures.append("  a tag without v should be refused")
    except VersionError:
        pass

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _fixture(root)
        expect("disagreeing npm found", len(check(root)), 3)
        changed = write(parse("v2026.11.0-beta.1"), root)
        expect("all written", len(changed), 6)
        expect("all agree", check(root), [])
        expect("pyproject", read(root)[PYPROJECT], "2026.11.0b1")
        expect("lock", read(root)[LOCK], "2026.11.0b1")
        expect("npm", read(root)[PACKAGES[0]], "2026.11.0-beta.1")
        lock = (root / LOCK).read_text()
        other = 'name = "tqdm"\nversion = "4.0"'
        expect("another package's version untouched", other in lock, True)
        pkg = json.loads((root / PACKAGES[0]).read_text())
        expect("nested version untouched", pkg["deps"]["version"], "9")
        expect("tag matches", check_tag("v2026.11.0-beta.1", root), beta)
        for wrong in ("v2026.11.0", "v2026.11.0-beta.2"):
            try:
                check_tag(wrong, root)
                failures.append(f"  {wrong} should not match 2026.11.0b1")
            except VersionError as e:
                expect("names the fix", "scripts/version.py set" in str(e), True)
        expect("unchanged rewrite", write(parse("2026.11.0b1"), root), [])

    if failures:
        print("Version self-test FAILED:\n" + "\n".join(failures))
        return 1
    print("Version self-test passed.")
    return 0


def main(argv: list[str]) -> int:
    try:
        if argv == ["--selftest"]:
            return selftest()
        if not argv:
            print(current().pep440())
        elif argv == ["--semver"]:
            print(current().semver())
        elif len(argv) == 2 and argv[0] == "set":
            v = parse(argv[1])
            changed = write(v)
            print(f"version {v.pep440()} (npm {v.semver()})")
            for rel in changed:
                print(f"  wrote {rel}")
        elif len(argv) == 2 and argv[0] == "dev" and argv[1].isdigit():
            v = current().dev_build(int(argv[1]))
            write(v)
            print(v.pep440())
        elif argv == ["check"]:
            wrong = check()
            if wrong:
                print("The version disagrees. Run: python3 scripts/version.py set <version>")
                print("\n".join(f"  {line}" for line in wrong))
                return 1
            print(f"version {current().pep440()} everywhere")
        elif len(argv) == 2 and argv[0] == "check-tag":
            print(check_tag(argv[1]).channel)
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except VersionError as e:
        print(f"version: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
