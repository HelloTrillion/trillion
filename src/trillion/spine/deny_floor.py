"""SPN-4: a pure floor under every path a tool touches, on the normal AND the confirmed path.

Names are compared folded, not lowercased: macOS volumes are case- AND
normalization-insensitive, so `.\u017fsh` (long s), `.\u00dfh` and `.SSH` all open `.ssh`.
Symlinks are resolved before any comparison, the on-disk spelling of the path is
checked as well as the one asked for, and anything that is not a usable path
string fails closed.
"""

from __future__ import annotations

import os
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from trillion import paths
from trillion.spine.contract import JSON, ToolDefinition

try:
    import fcntl
except ImportError:  # Windows: resolve() already returns the on-disk spelling there.
    _F_GETPATH: int | None = None
else:
    # macOS only: ask the kernel for the path of an open descriptor, as stored on disk.
    _F_GETPATH = getattr(fcntl, "F_GETPATH", None)

PROTECTED_SEGMENTS: frozenset[str] = frozenset({".git", ".ssh", ".aws", ".gnupg"})

_MAXPATHLEN = 1024


@dataclass(frozen=True)
class Denial:
    arg: str
    reason: str


@dataclass(frozen=True)
class DenyPolicy:
    protected_roots: tuple[Path, ...]

    @classmethod
    def default(cls) -> DenyPolicy:
        return cls(
            protected_roots=(
                paths.home().expanduser().resolve(),
                paths.package_root(),
                (Path.home() / "Library" / "Keychains").resolve(),
            )
        )


def fold(s: str) -> str:
    """NFKD, case-fold, NFKD again: long s (U+017F) -> s, sharp s -> ss, full-width -> ASCII."""
    return unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", s).casefold())


_FOLDED_SEGMENTS: frozenset[str] = frozenset(fold(s) for s in PROTECTED_SEGMENTS)


def _folded_parts(p: Path) -> tuple[str, ...]:
    return tuple(fold(part) for part in p.parts)


def _is_under(p: Path, root: Path) -> bool:
    rp, rr = _folded_parts(p), _folded_parts(root)
    return rp[: len(rr)] == rr


def _is_env_file(leaf: str) -> bool:
    return leaf in (".env", ".envrc") or leaf.startswith(".env.") or leaf.endswith(".env")


def _resolve(raw: str) -> Path:
    return Path(raw).expanduser().resolve(strict=False)


def _on_disk(existing: Path) -> Path:
    # The platform check lets type checkers on Windows skip the macOS-only branch.
    if sys.platform == "win32" or _F_GETPATH is None:
        return existing
    try:
        # O_NONBLOCK: a FIFO must not hang the check.
        fd = os.open(existing, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return existing
    try:
        buf = fcntl.fcntl(fd, _F_GETPATH, bytes(_MAXPATHLEN))
    except OSError:
        return existing
    finally:
        os.close(fd)
    raw = buf.split(b"\0", 1)[0]
    return Path(os.fsdecode(raw)) if raw else existing


def canonical(p: Path) -> Path:
    """The on-disk spelling of the longest existing prefix of `p`, plus the rest as given."""
    prefix, tail = p, []
    while not prefix.exists() and prefix != prefix.parent:
        tail.append(prefix.name)
        prefix = prefix.parent
    return _on_disk(prefix).joinpath(*reversed(tail))


def _spellings(p: Path) -> tuple[Path, ...]:
    c = canonical(p)
    return (p,) if c == p else (p, c)


def _check_one(
    name: str, p: Path, policy: DenyPolicy, granted_roots: tuple[Path, ...]
) -> Denial | None:
    folded = _folded_parts(p)
    hit = _FOLDED_SEGMENTS.intersection(folded)
    if hit:
        return Denial(name, f"the path touches a protected directory ({sorted(hit)[0]})")
    if folded and _is_env_file(folded[-1]):
        return Denial(name, "environment files are protected")
    for root in policy.protected_roots:
        if any(_is_under(p, r) for r in _spellings(root)):
            return Denial(name, "Trillion's own files are protected")
    grants = [s for g in granted_roots for s in _spellings(g.expanduser().resolve())]
    if not any(_is_under(p, g) for g in grants):
        return Denial(name, "the path is outside the folders the user granted")
    return None


def check(
    defn: ToolDefinition, args: JSON, policy: DenyPolicy, granted_roots: tuple[Path, ...]
) -> Denial | None:
    for name in defn.path_args:
        if name not in args:
            continue
        raw = args[name]
        if not isinstance(raw, str) or not raw.strip():
            return Denial(name, "a path must be a non-empty string")
        # Windows resolves a NUL without complaint; fail closed everywhere instead.
        if "\x00" in raw:
            return Denial(name, "the path could not be resolved")
        try:
            p = _resolve(raw)
            forms = _spellings(p)
        except (OSError, RuntimeError, ValueError):
            return Denial(name, "the path could not be resolved")
        # Deny if EITHER the spelling asked for or the one on disk hits a rule.
        for form in forms:
            denial = _check_one(name, form, policy, granted_roots)
            if denial is not None:
                return denial
    return None
