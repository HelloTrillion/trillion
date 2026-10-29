"""The folders that can never be shared (the shared folders spec, D4).

A home folder (the user's or anyone's), a whole drive or anything mounted, system
folders, Trillion's own folder or one holding it, the apps' own folders under home
(`~/Library`, `~\\AppData`; but iCloud Drive and the cloud storage folders kept in
`~/Library` may be shared), anything the deny floor protects, and hidden folders or
folders inside one. A link is judged as the folder it leads to, so a harmless-looking
name can't carry a share into `~/.ssh`. Names are compared folded, as the deny floor
compares them: refusing too much here is safe.

The same rule runs in Settings, when she proposes a share, and again when the user
approves it, so a folder swapped for a link in between is still caught.

`judge` is the rule on a path already resolved, with no disk access, so it can be
tested with any system's spellings. `why_not` resolves the path, adds the checks
that need the disk (mount points, the same folder by another name) and calls it.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from trillion import paths
from trillion.spine import deny_floor

# Where drives other than the system's own are mounted on macOS and Linux, as
# (folder, levels down). Windows drives are roots of their own (`D:\`).
_MOUNT_PLACES = (("Volumes", 1), ("media", 2), ("run/media", 2), ("mnt", 1))
# A stale network drive can hang a listing: look again at most this often.
_MOUNTS_FOR_S = 5.0
_mounts_cache: dict[str, tuple[float, tuple[Path, ...]]] = {}
# Windows marks some folders hidden or system without a leading dot.
_WINDOWS_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2) | getattr(
    stat, "FILE_ATTRIBUTE_SYSTEM", 4
)
# The system's own folders (D6): nothing in them is a project.
POSIX_SYSTEM: tuple[PurePath, ...] = tuple(
    PurePosixPath(p)
    for p in (
        "/System",
        "/Library",
        "/Applications",
        "/usr",
        "/etc",
        "/bin",
        "/sbin",
        "/var",
        "/private",
        "/proc",
        "/sys",
        "/dev",
        "/opt",
        "/boot",
        "/root",
    )
)
WINDOWS_SYSTEM: tuple[PurePath, ...] = tuple(
    PureWindowsPath(p)
    for p in (
        "C:/Windows",
        "C:/Program Files",
        "C:/Program Files (x86)",
        "C:/ProgramData",
    )
)
# Home folders that are not under the folder holding the others.
_OTHER_HOMES: tuple[PurePath, ...] = (PurePosixPath("/root"), PurePosixPath("/var/root"))
# Where apps keep their own files under a home folder. Hidden by the system, but the
# flag can be taken off, so the names are refused too.
_APP_FOLDERS = ("Library", "AppData")
# Except where macOS keeps the user's own cloud files inside ~/Library: iCloud Drive,
# and one folder per provider account under CloudStorage (Dropbox, Google Drive...).
_ICLOUD_DRIVE = ("Library", "Mobile Documents", "com~apple~CloudDocs")
# An app's iCloud folder the user sees (an Obsidian vault): <container>/Documents.
_MOBILE_DOCUMENTS = ("Library", "Mobile Documents")
_APP_ICLOUD = "Documents"
_CLOUD_STORAGE = ("Library", "CloudStorage")


def resolved(path: Path) -> Path:
    """Where a folder really is: links followed, spelled as it is on disk."""
    return deny_floor.canonical(path.expanduser().resolve(strict=False))


def _windows_system() -> tuple[PurePath, ...]:
    """The fixed list, plus where this computer says its system folders are."""
    found = [
        PureWindowsPath(v)
        for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
        if (v := os.environ.get(k))
    ]
    return WINDOWS_SYSTEM + tuple(found)


def system_folders() -> tuple[PurePath, ...]:
    return _windows_system() if sys.platform == "win32" else POSIX_SYSTEM


def _folders_in(folder: Path) -> list[Path]:
    """The folders directly in `folder`, from the listing alone: no stat of each, so a
    hung network drive among them is named without being touched."""
    try:
        with os.scandir(folder) as entries:
            return [Path(e.path) for e in entries if e.is_dir()]
    except OSError:
        return []


def mount_roots(root: Path = Path("/")) -> tuple[Path, ...]:
    """The mounted drives on this computer, each a folder of its own. Looked up at most
    every few seconds, since every share check asks."""
    now = time.monotonic()
    cached = _mounts_cache.get(str(root))
    if cached is not None and now - cached[0] < _MOUNTS_FOR_S:
        return cached[1]
    found: list[Path] = []
    for place, levels in _MOUNT_PLACES:
        here = [root / place]
        for _ in range(levels):
            here = [f for folder in here for f in _folders_in(folder)]
        found += here
    _mounts_cache[str(root)] = (now, tuple(found))
    return tuple(found)


def marked_hidden(path: PurePath) -> bool:
    """Hidden by the system rather than by name: `UF_HIDDEN` on macOS (`~/Library`),
    the hidden or system attribute on Windows."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    flags = getattr(st, "st_flags", 0) & getattr(stat, "UF_HIDDEN", 0)
    attributes = getattr(st, "st_file_attributes", 0) & _WINDOWS_HIDDEN
    return bool(flags or attributes)


def mount_point(path: Path) -> bool:
    """Another drive starts here. Compares devices with the folder above, which also
    finds what `os.path.ismount` misses (`/System/Volumes/Data` on macOS). So a folder
    that is a mount of its own (a btrfs subvolume, a ZFS dataset, its own partition)
    can't be shared itself, though the folders inside it can."""
    if path.parent == path:
        return True
    try:
        return os.stat(path).st_dev != os.stat(path.parent).st_dev
    except OSError:
        return False


def _samefile(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _plain(p: PurePath) -> PurePath:
    r"""A Windows path without its `\\?\` prefix, so it is compared as usually spelled."""
    if not isinstance(p, PureWindowsPath):
        return p
    s = str(p)
    if s.startswith("\\\\?\\UNC\\"):
        return PureWindowsPath("\\\\" + s[8:])
    if s.startswith("\\\\?\\"):
        return PureWindowsPath(s[4:])
    return p


def _same(a: PurePath, b: PurePath) -> bool:
    return len(a.parts) == len(b.parts) and deny_floor.is_under(a, b)


def _holds(outer: PurePath, inner: PurePath) -> bool:
    """`outer` is `inner` or one of the folders above it."""
    return deny_floor.is_under(inner, outer)


def _cloud_top(p: PurePath, home: PurePath) -> PurePath | None:
    """The cloud folder `p` is in (iCloud Drive, an app's iCloud Documents folder, or
    one provider's account folder), or None. The folders holding them, and the rest of
    an app's iCloud container, stay the apps' own."""
    icloud = home.joinpath(*_ICLOUD_DRIVE)
    if deny_floor.is_under(p, icloud):
        return type(p)(*p.parts[: len(icloud.parts)])
    mobile = home.joinpath(*_MOBILE_DOCUMENTS)
    depth = len(mobile.parts) + 2
    if (
        deny_floor.is_under(p, mobile)
        and len(p.parts) >= depth
        and deny_floor.fold(p.parts[depth - 1]) == deny_floor.fold(_APP_ICLOUD)
    ):
        return type(p)(*p.parts[:depth])
    storage = home.joinpath(*_CLOUD_STORAGE)
    if deny_floor.is_under(p, storage) and len(p.parts) > len(storage.parts):
        return type(p)(*p.parts[: len(storage.parts) + 1])
    return None


def judge(
    p: PurePath,
    *,
    home: PurePath,
    data: PurePath,
    protected: tuple[Path, ...] = (),
    mounts: Iterable[PurePath] = (),
    system: Iterable[PurePath] = (),
    hidden: Callable[[PurePath], bool] = lambda _p: False,
) -> str | None:
    """Why a resolved path can never be shared, or None. Touches no disk but through
    `hidden`, which is asked about the folder and every folder above it."""
    p, home, data = _plain(p), _plain(home), _plain(data)
    mounts = [_plain(m) for m in mounts]
    if deny_floor.is_under(p, data):
        return "that is Trillion's own folder, which is never shared"
    reason = deny_floor.protected_reason(p, protected)
    if reason is not None:
        return reason
    # A system folder that holds the home folder (`/var/home` on Fedora Silverblue) or
    # Trillion's data is where the user's own files are kept, not the system's.
    system = [s for s in system if not _holds(s, home) and not _holds(s, data)]
    if any(deny_floor.is_under(p, s) for s in system):
        return "system folders are never shared"
    names = p.parts[1:] if p.anchor else p.parts
    above = [q for q in (p, *p.parents) if q.parent != q]
    cloud = _cloud_top(p, home)
    # Folders the system hides above a cloud folder (~/Library) or a drive (/Volumes
    # on macOS) say nothing about what is inside: ask only from there down.
    stop = cloud or next((m for m in mounts if deny_floor.is_under(p, m)), None)
    if stop is not None:
        above = [q for q in above if deny_floor.is_under(q, stop)]
    if any(name.startswith(".") for name in names) or any(hidden(q) for q in above):
        return "hidden folders, and folders inside them, are never shared"
    if cloud is None and any(deny_floor.is_under(p, home / name) for name in _APP_FOLDERS):
        return "that folder holds apps' own files, which are never shared"
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
    if _same(p.parent, home.parent) or any(_same(p, h) for h in _OTHER_HOMES):
        return "that is someone's home folder, which holds everything in it"
    return None


def why_not(
    path: Path,
    *,
    home: Path | None = None,
    data_dir: Path | None = None,
    protected_roots: Sequence[Path] | None = None,
    mounts: Sequence[Path] | None = None,
    system: Sequence[PurePath] | None = None,
    hidden: Callable[[PurePath], bool] = marked_hidden,
    is_mount: Callable[[Path], bool] = mount_point,
) -> str | None:
    """Why `path` can never be shared, in plain words, or None when it may be.

    The home folder, data folder, protected folders, drives and system folders
    default to this computer's own; tests pass their own."""
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
    # Only the folder above each drive is resolved: a hung drive is never touched.
    drives = [
        resolved(m.parent) / m.name for m in (mounts if mounts is not None else mount_roots())
    ]
    reason = judge(
        p,
        home=home_,
        data=data,
        protected=protected,
        mounts=drives,
        system=system if system is not None else system_folders(),
        hidden=hidden,
    )
    if reason is not None:
        return reason
    if is_mount(p):
        return "a whole drive is never shared"
    # The same folder by another name, as a firmlink or a mount elsewhere gives it.
    if _samefile(p, home_):
        return "a home folder is never shared: it holds everything in it"
    if any(_samefile(p, q) for q in home_.parents):
        return "that folder holds a home folder, which holds everything in it"
    if any(_samefile(p, q) for q in (data, *data.parents)):
        return "that folder holds Trillion's own folder, which is never shared"
    return None
