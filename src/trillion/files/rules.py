"""The folders that can never be shared (the shared folders spec, D4).

A home folder, a whole drive, Trillion's own folder or one holding it, anything the
deny floor protects, and hidden folders. A link is judged as the folder it leads to,
so a harmless-looking name can't carry a share into `~/.ssh`. Names are compared
folded, as the deny floor compares them: refusing too much here is safe.

The same rule runs in Settings, when she proposes a share, and again when the user
approves it, so a folder swapped for a link in between is still caught.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Sequence
from pathlib import Path

from trillion import paths
from trillion.spine import deny_floor

# Where drives other than the system's own are mounted, on macOS and Linux. Windows
# drives are roots of their own (`D:\`), caught as roots.
_MOUNT_PATTERNS = ("Volumes/*", "media/*/*", "run/media/*/*", "mnt/*")
# Windows marks some folders hidden or system without a leading dot.
_WINDOWS_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2) | getattr(
    stat, "FILE_ATTRIBUTE_SYSTEM", 4
)


def resolved(path: Path) -> Path:
    """Where a folder really is: links followed, spelled as it is on disk."""
    return deny_floor.canonical(path.expanduser().resolve(strict=False))


def mount_roots(root: Path = Path("/")) -> tuple[Path, ...]:
    """The mounted drives on this computer, each a folder of its own."""
    found: list[Path] = []
    for pattern in _MOUNT_PATTERNS:
        try:
            found += [p for p in root.glob(pattern) if p.is_dir()]
        except OSError:
            continue
    return tuple(found)


def marked_hidden(path: Path) -> bool:
    """Hidden by the system rather than by name: `UF_HIDDEN` on macOS (`~/Library`),
    the hidden or system attribute on Windows."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    flags = getattr(st, "st_flags", 0) & getattr(stat, "UF_HIDDEN", 0)
    attributes = getattr(st, "st_file_attributes", 0) & _WINDOWS_HIDDEN
    return bool(flags or attributes)


def _same(a: Path, b: Path) -> bool:
    return len(a.parts) == len(b.parts) and deny_floor.is_under(a, b)


def _holds(outer: Path, inner: Path) -> bool:
    """`outer` is `inner` or one of the folders above it."""
    return deny_floor.is_under(inner, outer)


def _why_not_one(
    p: Path,
    home: Path,
    data: Path,
    protected: tuple[Path, ...],
    mounts: Sequence[Path],
    hidden: Callable[[Path], bool],
) -> str | None:
    if deny_floor.is_under(p, data):
        return "that is Trillion's own folder, which is never shared"
    reason = deny_floor.protected_reason(p, protected)
    if reason is not None:
        return reason
    names = p.parts[1:] if p.anchor else p.parts
    if any(name.startswith(".") for name in names) or hidden(p):
        return "hidden folders are never shared"
    if p.parent == p or any(_same(p, m) for m in mounts):
        return "a whole drive is never shared"
    if _same(p, home):
        return "a home folder is never shared: it holds everything in it"
    if _holds(p, home):
        return "that folder holds a home folder, which holds everything in it"
    if _holds(p, data):
        return "that folder holds Trillion's own folder, which is never shared"
    if any(_holds(p, m) for m in mounts):
        return "that folder holds whole drives"
    return None


def why_not(
    path: Path,
    *,
    home: Path | None = None,
    data_dir: Path | None = None,
    protected_roots: Sequence[Path] | None = None,
    mounts: Sequence[Path] | None = None,
    hidden: Callable[[Path], bool] = marked_hidden,
) -> str | None:
    """Why `path` can never be shared, in plain words, or None when it may be.

    The home folder, data folder, protected folders and drives default to this
    computer's own; tests pass their own."""
    if "\x00" in str(path):
        return "that folder could not be found"
    try:
        p = resolved(path)
        home_ = resolved(home if home is not None else Path.home())
        data = resolved(data_dir if data_dir is not None else paths.home())
    except (OSError, RuntimeError, ValueError):
        return "that folder could not be found"
    protected = tuple(
        protected_roots
        if protected_roots is not None
        else deny_floor.DenyPolicy.default().protected_roots
    )
    drives = tuple(resolved(m) for m in (mounts if mounts is not None else mount_roots()))
    return _why_not_one(p, home_, data, protected, drives, hidden)
