"""The folder browser's listing (the shared folders spec, D5, D6, D8).

It shows folder names and nothing else: no files, sizes or dates, no hidden folders,
nothing the deny floor protects and never Trillion's own folder, because the names go
to the model provider so she can follow "open Clients". It opens folders only inside
a fence: the home folder, a mounted drive, or a folder already shared, never the
system's folders. D4's rule (`rules.judge`) decides what is refused, so the browser
and the share rule never disagree; only the reasons D4 gives for a folder being too
big to share (the home folder itself, a whole drive) are let through, since opening
one is how the user finds a folder inside it.

The places list (Home, Desktop, Documents, Downloads, the shares, the drives) has one
function per system taking its system calls as parameters, so every system is tested
on every runner, and an entry point chooses by `sys.platform`. A listing reads one
folder only and gives up after a couple of seconds, so a network drive gone to sleep
can't hang her turn.
"""

from __future__ import annotations

import ctypes
import functools
import getpass
import logging
import os
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath, PurePosixPath
from typing import Literal

from trillion import paths
from trillion.attention.probes import Run, within
from trillion.files import rules
from trillion.spine import deny_floor

logger = logging.getLogger(__name__)

Kind = Literal["home", "folder", "shared", "drive"]
DEADLINE_S = 2.0
LIMIT = 200
OUTSIDE = "the folder browser opens only folders in your home folder, on a drive, or shared"
_SLOW = "that folder took too long to open"
_GONE = "that folder could not be found"

# Windows known folders: where the system says they are, so a Documents folder moved
# to OneDrive or another drive is still found.
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"
# GetDriveType: no such drive, then the kinds that may hold no disk right now.
_DRIVE_UNKNOWN, _DRIVE_NO_ROOT, _DRIVE_REMOVABLE, _DRIVE_CDROM = 0, 1, 2, 5


@dataclass(frozen=True)
class Place:
    label: str
    path: Path
    kind: Kind

    def row(self) -> dict[str, str]:
        return {"label": self.label, "path": str(self.path), "kind": self.kind}


@dataclass(frozen=True)
class Listing:
    """One folder's folder names. `problem` says in plain words why there are none."""

    path: Path
    folders: tuple[str, ...] = ()
    more: int = 0
    problem: str | None = None


@dataclass(frozen=True)
class Step:
    """Where the browser goes: a folder, or None for the places list. When `problem`
    is set it stays where it was."""

    to: Path | None
    problem: str | None = None


def _never(_p: PurePath) -> bool:
    return False


@functools.cache
def _too_big() -> frozenset[str]:
    """D4's reasons for a folder too big to share (a home folder, a whole drive, one
    holding them), asked of `judge` itself on a made-up layout so the two never drift.
    The browser may open such a folder; it is still never shared."""
    home, data, drive = (PurePosixPath(p) for p in ("/h/me", "/d/t", "/m/x"))
    cases = ("/", "/h", "/h/me", "/h/you", "/d", "/m", "/m/x")
    found = (
        rules.judge(PurePosixPath(c), home=home, data=data, mounts=[drive], hidden=_never)
        for c in cases
    )
    return frozenset(r for r in found if r is not None)


@dataclass(frozen=True)
class Fence:
    """Where the browser may go (D6). Paths are resolved; `drives` are spelled with
    only the folder above each resolved, as D4 spells them."""

    home: PurePath
    data: PurePath
    drives: tuple[PurePath, ...] = ()
    shared: tuple[PurePath, ...] = ()
    system: tuple[PurePath, ...] = ()
    protected: tuple[Path, ...] = ()
    hidden: Callable[[PurePath], bool] = field(default=rules.marked_hidden)
    case_blind: bool = deny_floor.CASE_BLIND

    @classmethod
    def here(
        cls, shared: Sequence[Path] = (), *, home: Path | None = None, data_dir: Path | None = None
    ) -> Fence:
        """This computer's fence: its home, drives, system folders and protected ones."""
        drives = _drive_roots() if sys.platform == "win32" else list(rules.mount_roots())
        return cls(
            home=rules.resolved(home if home is not None else Path.home()),
            data=rules.resolved(data_dir if data_dir is not None else paths.home()),
            drives=tuple(rules.resolved(d.parent) / d.name for d in drives),
            shared=tuple(rules.resolved(s) for s in shared),
            system=rules.system_folders(),
            protected=deny_floor.DenyPolicy.default().protected_roots,
        )

    def _within(self, p: PurePath, root: PurePath) -> bool:
        if self.case_blind:
            return deny_floor.is_under(p, root)
        return p.parts[: len(root.parts)] == root.parts

    def roots(self) -> tuple[PurePath, ...]:
        return (self.home, *self.drives, *self.shared)

    def outermost(self, p: PurePath) -> PurePath | None:
        """The widest place holding `p`, where its breadcrumb starts."""
        holding = [r for r in self.roots() if self._within(p, r)]
        return min(holding, key=lambda r: len(r.parts)) if holding else None

    def reason(self, p: PurePath) -> str | None:
        """Why the browser may not open a resolved path, or None. Touches no disk but
        through `hidden` and the protected folders' spellings."""
        if self.outermost(p) is None:
            return OUTSIDE
        reason = rules.judge(
            p,
            home=self.home,
            data=self.data,
            protected=self.protected,
            mounts=self.drives,
            system=self.system,
            hidden=self.hidden,
        )
        return None if reason is None or reason in _too_big() else reason


def _bounded[T](work: Callable[[], T], seconds: float) -> tuple[bool, T | None]:
    """Run `work` in a daemon thread for at most `seconds`: (finished, its answer). A
    stale network drive can hang a system call; the thread is left behind, never her
    turn."""
    box: list[T] = []

    def run() -> None:
        try:
            box.append(work())
        except Exception as e:
            logger.info("folder browser can't tell: %s", type(e).__name__)

    t = threading.Thread(target=run, name="folder-browse", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return False, None
    return True, box[0] if box else None


def _resolve(path: Path) -> Path | None:
    if "\x00" in str(path):
        return None
    try:
        return rules.resolved(path)
    except (OSError, RuntimeError, ValueError):
        return None


def _allowed(path: Path, fence: Fence) -> tuple[Path | None, str | None]:
    p = _resolve(path)
    if p is None:
        return None, _GONE
    return p, fence.reason(p)


def allowed(
    path: Path, *, fence: Fence | None = None, deadline_s: float = DEADLINE_S
) -> str | None:
    """Why the browser may not open `path`, in plain words, or None when it may. A
    link is judged as the folder it leads to."""
    f = fence if fence is not None else Fence.here()
    done, answer = _bounded(lambda: _allowed(path, f)[1], deadline_s)
    if not done:
        return _SLOW
    return answer


def _shown(entry: os.DirEntry[str], folder: Path, fence: Fence, quiet: Fence) -> bool:
    """Whether one entry is a folder the browser lists. The folder holding it already
    passed the fence, so only the entry itself is asked whether the system hides it."""
    if entry.name.startswith("."):
        return False
    if entry.is_symlink() or entry.is_junction():
        # Never followed into a refused place: the folder it leads to must pass too.
        target = _resolve(Path(entry.path))
        return target is not None and target.is_dir() and fence.reason(target) is None
    if not entry.is_dir(follow_symlinks=False):
        return False
    child = folder / entry.name
    return not fence.hidden(child) and quiet.reason(child) is None


def _scan(path: Path, fence: Fence) -> tuple[Path, list[str], str | None]:
    """Every folder name the browser shows in one folder, sorted, or why there are none."""
    p, reason = _allowed(path, fence)
    if p is None or reason is not None:
        return (p or path), [], reason
    try:
        with os.scandir(p) as it:
            entries = list(it)
    except FileNotFoundError:
        return p, [], _GONE
    except NotADirectoryError:
        return p, [], "that is not a folder"
    except PermissionError:
        return p, [], "Trillion isn't allowed to read that folder"
    except OSError:
        return p, [], "that folder could not be read"
    quiet = replace(fence, hidden=_never)
    names: list[str] = []
    for entry in entries:
        try:
            if _shown(entry, p, fence, quiet):
                names.append(entry.name)
        except OSError:
            continue  # gone or unreadable since the folder was read: not shown
    names.sort(key=lambda n: (deny_floor.fold(n), n))
    return p, names, None


def listing(
    path: Path,
    *,
    fence: Fence | None = None,
    limit: int = LIMIT,
    deadline_s: float = DEADLINE_S,
) -> Listing:
    """The folder names in one folder (D5), at most `limit` with a count of the rest.
    Never raises: a folder past the fence, gone, unreadable or too slow comes back
    with no names and the problem in plain words."""
    f = fence if fence is not None else Fence.here()
    done, answer = _bounded(lambda: _scan(path, f), deadline_s)
    if not done:
        return Listing(path, problem=_SLOW)
    if answer is None:
        return Listing(path, problem="that folder could not be read")
    p, names, problem = answer
    return Listing(p, tuple(names[:limit]), max(0, len(names) - limit), problem)


def _top(p: PurePath, fence: Fence) -> bool:
    """`p` is a place's own root: the home folder, a drive, or a share outside both."""
    return fence.outermost(p) == p


def _match(name: str, names: Sequence[str]) -> tuple[str | None, str | None]:
    """The one name meant, exactly or by any case: (name, None) or (None, why not)."""
    if name in names:
        return name, None
    folded = [n for n in names if deny_floor.fold(n) == deny_floor.fold(name)]
    if len(folded) == 1:
        return folded[0], None
    if folded:
        return None, f"more than one folder here is called {name}: say which by its full path"
    return None, f"there is no folder called {name} here"


def step(
    current: Path | None,
    target: str,
    *,
    fence: Fence | None = None,
    places_now: Sequence[Place] = (),
    deadline_s: float = DEADLINE_S,
) -> Step:
    """Where `target` takes the browser from `current` (None: the places list).

    `target` is `places`, `up`, a full path, or a name from what the browser shows
    now: a folder in `current`, or a place's label in `places_now`."""
    f = fence if fence is not None else Fence.here()
    want = target.strip()
    if want.lower() == "places":
        return Step(None)
    if want.lower() == "up":
        if current is None:
            return Step(None)
        here = _resolve(current)
        if here is None or _top(here, f) or here.parent == here:
            return Step(None)
        # Above a place's root is past the fence: back to the places list.
        if allowed(here.parent, fence=f, deadline_s=deadline_s) is not None:
            return Step(None)
        return Step(here.parent)
    if want.startswith("~") or Path(want).is_absolute():
        p = Path(want).expanduser()
        reason = allowed(p, fence=f, deadline_s=deadline_s)
        if reason is not None:
            return Step(current, reason)
        return Step(_resolve(p))
    if not want or "/" in want or "\\" in want:
        return Step(current, "say one folder name at a time, or the folder's full path")
    if current is None:
        labels = [p.label for p in places_now]
        label, why = _match(want, labels)
        if label is None:
            return Step(None, why)
        return Step(next(p.path for p in places_now if p.label == label))
    shown = listing(current, fence=f, limit=sys.maxsize, deadline_s=deadline_s)
    if shown.problem is not None:
        return Step(current, shown.problem)
    name, why = _match(want, shown.folders)
    if name is None:
        return Step(current, why)
    return Step(shown.path / name)


def crumbs(path: PurePath, *, fence: Fence | None = None) -> list[tuple[str, PurePath]]:
    """The breadcrumb for a folder, from the place holding it down: Home, Documents..."""
    f = fence if fence is not None else Fence.here()
    root = f.outermost(path)
    if root is None:
        return [(path.name or str(path), path)]
    if root == f.home:
        label = "Home"
    else:
        label = root.name or root.drive or str(root)
    trail: list[tuple[str, PurePath]] = [(label, root)]
    here = root
    for name in path.parts[len(root.parts) :]:
        here = here / name
        trail.append((name, here))
    return trail


# --- places (D8) -------------------------------------------------------------

Found = tuple[list[Place], list[Place]]
_ENGLISH = (("Desktop", "Desktop"), ("Documents", "Documents"), ("Downloads", "Downloads"))


def _folder(label: str, path: Path | None) -> list[Place]:
    return [Place(label, path, "folder")] if path is not None and path.is_dir() else []


def _drives_in(
    folder: Path, is_mount: Callable[[Path], bool], skip: Callable[[os.DirEntry[str]], bool]
) -> list[Place]:
    """The drives mounted in one folder, in name order. Folders left behind there by a
    drive no longer attached are not drives."""
    try:
        with os.scandir(folder) as it:
            entries = sorted(it, key=lambda e: deny_floor.fold(e.name))
    except OSError:
        return []
    found: list[Place] = []
    for e in entries:
        try:
            if e.name.startswith(".") or skip(e) or not e.is_dir():
                continue
            if is_mount(Path(e.path)):
                found.append(Place(e.name, Path(e.path), "drive"))
        except OSError:
            continue
    return found


def _boot_volume(entry: os.DirEntry[str]) -> bool:
    """macOS lists the startup disk in /Volumes as a link to `/`: read the link only,
    so no other drive is touched."""
    if not entry.is_symlink():
        return False
    try:
        target = os.readlink(entry.path)
    except OSError:
        return False
    return Path(target).anchor == target or Path(entry.path).resolve() == Path("/").resolve()


def _macos(
    home: Path,
    *,
    volumes: Path = Path("/Volumes"),
    is_mount: Callable[[Path], bool] = rules.mount_point,
) -> Found:
    folders = [p for label, name in _ENGLISH for p in _folder(label, home / name)]
    return folders, _drives_in(volumes, is_mount, _boot_volume)


def _windows(
    home: Path,
    *,
    known: Callable[[str], str | None],
    drive_mask: Callable[[], int],
    drive_type: Callable[[str], int],
    has_volume: Callable[[str], bool],
) -> Found:
    folders: list[Place] = []
    for (label, name), guid in zip(
        _ENGLISH, (FOLDERID_DESKTOP, FOLDERID_DOCUMENTS, FOLDERID_DOWNLOADS), strict=True
    ):
        where = known(guid)
        folders += _folder(label, Path(where) if where else home / name)
    drives: list[Place] = []
    mask = drive_mask()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(ord('A') + i)}:\\"
        kind = drive_type(root)
        if kind in (_DRIVE_UNKNOWN, _DRIVE_NO_ROOT):
            continue
        # A card reader or a CD drive is listed with nothing in it: ask for its disk.
        if kind in (_DRIVE_REMOVABLE, _DRIVE_CDROM) and not has_volume(root):
            continue
        drives.append(Place(root[:2], Path(root), "drive"))
    return folders, drives


def _linux(
    home: Path,
    *,
    run: Run,
    user: str,
    root: Path = Path("/"),
    is_mount: Callable[[Path], bool] = rules.mount_point,
) -> Found:
    folders: list[Place] = []
    for (label, name), key in zip(_ENGLISH, ("DESKTOP", "DOCUMENTS", "DOWNLOAD"), strict=True):
        out = run(["xdg-user-dir", key])
        if out is None:
            folders += _folder(label, home / name)  # no xdg-user-dir: the usual names
            continue
        where = Path(out.strip()) if out.strip() else None
        # Unset, xdg-user-dir answers with the home folder, already a place of its own.
        if where is not None and where != home:
            folders += _folder(label, where)
    drives: list[Place] = []
    for place in (root / "media" / user, root / "run" / "media" / user, root / "mnt"):
        drives += _drives_in(place, is_mount, lambda _e: False)
    return folders, drives


def _user() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return os.environ.get("USER", "")


if sys.platform == "win32":
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def _known_folder(guid: str) -> str | None:
        import uuid

        g = _GUID.from_buffer_copy(uuid.UUID(guid).bytes_le)
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return out.value
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)

    def _has_volume(root: str) -> bool:
        kernel32 = ctypes.windll.kernel32
        # No "insert a disk" box for an empty drive: just a failed call.
        old = wintypes.DWORD()
        kernel32.SetThreadErrorMode(0x0001 | 0x8000, ctypes.byref(old))
        try:
            return bool(kernel32.GetVolumeInformationW(root, None, 0, None, None, None, None, 0))
        finally:
            kernel32.SetThreadErrorMode(old.value, None)

    def _windows_here(home: Path) -> Found:
        kernel32 = ctypes.windll.kernel32
        return _windows(
            home,
            known=_known_folder,
            drive_mask=kernel32.GetLogicalDrives,
            drive_type=kernel32.GetDriveTypeW,
            has_volume=_has_volume,
        )


def _drive_roots() -> list[Path]:
    """The Windows drives with a disk, for the fence."""
    if sys.platform == "win32":
        return [p.path for p in _windows_here(Path.home())[1]]
    return []


def _finder() -> Callable[[Path], Found]:
    if sys.platform == "darwin":
        return _macos
    if sys.platform == "win32":
        return _windows_here
    return lambda home: _linux(home, run=within(DEADLINE_S), user=_user())


def places(
    shared: Sequence[tuple[str, Path]] = (),
    *,
    home: Path | None = None,
    finder: Callable[[Path], Found] | None = None,
    deadline_s: float = DEADLINE_S,
) -> list[Place]:
    """The places list (D8): Home, Desktop, Documents, Downloads, the folders already
    shared as (name, path), and the drives, each only if it is there. Within the
    deadline: a drive that hangs leaves what was already found."""
    h = home if home is not None else Path.home()
    find = finder if finder is not None else _finder()
    found: list[Place] = [Place("Home", h, "home")]

    def gather() -> None:
        folders, drives = find(h)
        found.extend(p for p in folders if p.path != h)
        for name, path in shared:
            if path.is_dir():
                found.append(Place(name, path, "shared"))
        found.extend(drives)

    _bounded(gather, deadline_s)
    return list(found)
