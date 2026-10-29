"""SPN-4: a pure floor under every path a tool touches, on the normal AND the confirmed path.

Names are compared folded, not lowercased: macOS volumes are case- AND
normalization-insensitive, so `.\u017fsh` (long s), `.\u00dfh` and `.SSH` all open `.ssh`.
Symlinks are resolved before any comparison, the on-disk spelling of the path is
checked as well as the one asked for, and anything that is not a usable path
string fails closed.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath

from trillion import paths
from trillion.spine.contract import JSON, Risk, ToolDefinition

try:
    import fcntl
except ImportError:  # Windows: resolve() already returns the on-disk spelling there.
    _F_GETPATH: int | None = None
else:
    # macOS only: ask the kernel for the path of an open descriptor, as stored on disk.
    _F_GETPATH = getattr(fcntl, "F_GETPATH", None)

# What a write in a folder shared `read` is told (the shared folders spec, D11).
READ_ONLY = (
    "that folder is shared read only; change it to read and change in Settings to let her "
    "write there"
)

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


def _folded_parts(p: PurePath) -> tuple[str, ...]:
    return tuple(fold(part) for part in p.parts)


def is_under(p: PurePath, root: PurePath) -> bool:
    """Under `root` by any spelling: for what is protected, refusing too much is safe."""
    rp, rr = _folded_parts(p), _folded_parts(root)
    return rp[: len(rr)] == rr


# Whether one folder answers to names that differ only in case. On Linux `Docs` and
# `docs` are two folders: a grant of one must not reach into the other, or the card
# would show one file while another is changed.
CASE_BLIND = sys.platform in ("darwin", "win32")


def inside(p: Path, root: Path) -> bool:
    """Under a granted folder, comparing names the way this system's disks do."""
    if CASE_BLIND:
        return is_under(p, root)
    rp, rr = p.parts, root.parts
    return rp[: len(rr)] == rr


def _has_stream(p: PurePath) -> bool:
    """A colon in a name after the drive. On Windows `.env::$DATA` is `.env` itself, and
    `.git::$INDEX_ALLOCATION` the folder `.git`, so no name rule could see them."""
    names = p.parts[1:] if p.anchor else p.parts
    return any(":" in name for name in names)


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


def spellings(p: Path) -> tuple[Path, ...]:
    """The path as given and, when it differs, as it is spelled on disk."""
    c = canonical(p)
    return (p,) if c == p else (p, c)


def protected_reason(p: PurePath, protected_roots: tuple[Path, ...]) -> str | None:
    """Why one spelling of a resolved path is protected wherever it is, or None. Shared
    with the rule for folders that can never be shared, so the two never drift."""
    if _has_stream(p):
        return "a name with a colon in it is not allowed"
    folded = _folded_parts(p)
    hit = _FOLDED_SEGMENTS.intersection(folded)
    if hit:
        return f"the path touches a protected directory ({sorted(hit)[0]})"
    if folded and _is_env_file(folded[-1]):
        return "environment files are protected"
    for root in protected_roots:
        if any(is_under(p, r) for r in spellings(root)):
            return "Trillion's own files are protected"
    return None


def _check_one(
    name: str, p: Path, policy: DenyPolicy, granted_roots: tuple[Path, ...]
) -> Denial | None:
    reason = protected_reason(p, policy.protected_roots)
    if reason is not None:
        return Denial(name, reason)
    grants = [s for g in granted_roots for s in spellings(g.expanduser().resolve())]
    if not any(inside(p, g) for g in grants):
        return Denial(name, "the path is outside the folders the user granted")
    return None


def refused(
    p: Path, granted_roots: tuple[Path, ...], *, read_only_roots: tuple[Path, ...] = ()
) -> str | None:
    """Why a path already resolved is refused, or None. For the file tools, on the very
    path they are about to walk to, after any folder on the way may have changed. A
    write passes `read_only_roots`, so a link swapped in to a read only share is caught
    here too (D11)."""
    for form in spellings(p):
        denial = _check_one("path", form, DenyPolicy.default(), granted_roots)
        if denial is not None:
            return denial.reason
        if read_only_roots and read_only(form, granted_roots, read_only_roots):
            return READ_ONLY
    return None


def read_only(p: Path, granted_roots: tuple[Path, ...], read_only_roots: tuple[Path, ...]) -> bool:
    """Whether the innermost share holding `p` is read only. Where shares nest, the
    innermost decides; two of the same folder, one of them read only, are read only."""
    deepest, locked = -1, False
    shares = [(g, False) for g in granted_roots] + [(r, True) for r in read_only_roots]
    for root, read_only in shares:
        for form in spellings(root.expanduser().resolve()):
            if not inside(p, form):
                continue
            depth = len(form.parts)
            if depth > deepest:
                deepest, locked = depth, read_only
            elif depth == deepest:
                locked = locked or read_only
    return locked


def _local(raw: str) -> bool:
    """A full path on this computer. A relative one given to a connected server names
    a place in its own world, not one under the user's shares."""
    s = raw.strip()
    return s.startswith(("/", "~", "\\\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", s))


def reads_as_path(raw: str) -> bool:
    """Whether a value names a place on a disk, rather than an id or an address."""
    s = raw.strip()
    if "://" in s:
        return False
    return (
        s.startswith(("/", "~", "./", "../", "\\\\"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", s))
        or "/" in s
        or "\\" in s
    )


def check(
    defn: ToolDefinition,
    args: JSON,
    policy: DenyPolicy,
    granted_roots: tuple[Path, ...],
    *,
    read_only_roots: tuple[Path, ...] = (),
) -> Denial | None:
    """`read_only_roots` are the shares that are `read` only (D11). Any tool that is
    not a read counts as a write there, so a connected server or a skill naming a path
    is held the same as the file tools."""
    writes = defn.risk is not Risk.READ and bool(read_only_roots)
    for name in defn.outside_path_args:
        raw = args.get(name)
        if not isinstance(raw, str) or not reads_as_path(raw):
            continue
        if "\x00" in raw:
            return Denial(name, "the path could not be resolved")
        try:
            forms = spellings(_resolve(raw))
        except (OSError, RuntimeError, ValueError):
            return Denial(name, "the path could not be resolved")
        # Protected wherever it is, granted folder or not (SEC-10): every form is
        # "granted" its own drive, so only the protected rules can refuse it.
        everywhere = tuple({Path(f.anchor) for f in forms if f.anchor})
        for form in forms:
            denial = _check_one(name, form, policy, everywhere)
            if denial is not None:
                return denial
            if writes and _local(raw) and read_only(form, granted_roots, read_only_roots):
                return Denial(name, READ_ONLY)
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
            forms = spellings(p)
        except (OSError, RuntimeError, ValueError):
            return Denial(name, "the path could not be resolved")
        # Deny if EITHER the spelling asked for or the one on disk hits a rule.
        for form in forms:
            denial = _check_one(name, form, policy, granted_roots)
            if denial is not None:
                return denial
            if writes and read_only(form, granted_roots, read_only_roots):
                return Denial(name, READ_ONLY)
    return None
