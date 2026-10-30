"""The folder browser's listing (the shared folders spec, D5, D6, D8).

It shows folder names and nothing else: no files, sizes or dates, no hidden folders,
nothing the deny floor protects and never Trillion's own folder, because the names go
to the model provider so she can follow "open Clients". It opens folders only inside
a fence: the home folder, a mounted drive, a folder already shared or one of the
places it lists, never the system's folders and never anyone else's home folder.
D4's rule (`rules.judge`) decides what is refused, so the browser and the share rule
never disagree; only the reasons D4 gives for a folder being too big to share (the
home folder itself, a whole drive) are let through, since opening one is how the user
finds a folder inside it.

The places list (Home, Desktop, Documents, Downloads, the shares, the drives) has one
function per system taking its system calls as parameters, so every system is tested
on every runner, and an entry point chooses by `sys.platform`. Every call reads one
folder only and gives up after a couple of seconds, fence included, so a network
drive gone to sleep can't hang her turn.
"""

from __future__ import annotations

import ctypes
import functools
import getpass
import logging
import os
import stat
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, Literal

from trillion import paths
from trillion.attention.probes import Run, within
from trillion.files import rules
from trillion.spine import deny_floor

logger = logging.getLogger(__name__)

Kind = Literal["home", "folder", "shared", "drive"]
Outcome = Literal["done", "slow", "raised"]
DEADLINE_S = 2.0
LIMIT = 200
OUTSIDE = "the folder browser opens only folders in your home folder, on a drive, or shared"
OTHER_HOME = "that is someone else's home folder, which the folder browser never opens"
_SLOW = "that folder took too long to open"
_GONE = "that folder could not be found"
_FAILED = "that folder could not be read"
_UNCHECKED = "that folder could not be checked"
# Calls still running at once. A hung network drive keeps its thread; past this many,
# a new call answers "too long" at once instead of piling up threads.
_SLOTS = threading.BoundedSemaphore(4)

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
    """One folder's folder names and its breadcrumb. `problem` says in plain words why
    there are none."""

    path: Path
    folders: tuple[str, ...] = ()
    more: int = 0
    problem: str | None = None
    crumbs: tuple[tuple[str, PurePath], ...] = ()


@dataclass(frozen=True)
class Step:
    """Where the browser goes: a folder, or None for the places list. When `problem`
    is set it stays where it was."""

    to: Path | None
    problem: str | None = None


Found = tuple[list[Place], list[Place]]


def _never(_p: PurePath) -> bool:
    return False


def marked(st: Any) -> bool:
    """A stat result the system marks hidden: `UF_HIDDEN` on macOS, the hidden or
    system attribute on Windows (the old `My Documents` junctions carry both)."""
    flags = getattr(st, "st_flags", 0) & stat.UF_HIDDEN
    return bool(flags or getattr(st, "st_file_attributes", 0) & rules._WINDOWS_HIDDEN)


def _entry_marked(entry: os.DirEntry[str]) -> bool:
    # The entry's own stat, not where a link leads: free on Windows, one lstat elsewhere.
    return marked(entry.stat(follow_symlinks=False))


@functools.cache
def _too_big() -> frozenset[str]:
    """D4's reasons for a folder too big to share (a home folder, a whole drive, one
    holding them), asked of `judge` itself on a made-up layout so the two never drift.
    The browser may open such a folder; it is still never shared. Someone else's home
    is not among them: the browser never opens one."""
    home, data, drive = (PurePosixPath(p) for p in ("/h/me", "/d/t", "/m/x"))
    cases = ("/", "/h", "/h/me", "/d", "/m", "/m/x")
    found = (
        rules.judge(PurePosixPath(c), home=home, data=data, mounts=[drive], hidden=_never)
        for c in cases
    )
    return frozenset(r for r in found if r is not None)


def _key(p: PurePath, case_blind: bool) -> str:
    """One folder however it is spelled, compared the way this system's disks do."""
    return deny_floor.fold(str(p)) if case_blind else str(p)


@dataclass(frozen=True)
class Fence:
    """Where the browser may go (D6). Paths are resolved; `drives` are spelled with
    only the folder above each resolved, as D4 spells them. `shared` are the shares'
    folders, `folders` the places list's own (a Documents folder moved elsewhere)."""

    home: PurePath
    data: PurePath
    drives: tuple[PurePath, ...] = ()
    shared: tuple[PurePath, ...] = ()
    folders: tuple[PurePath, ...] = ()
    system: tuple[PurePath, ...] = ()
    protected: tuple[Path, ...] = ()
    hidden: Callable[[PurePath], bool] = field(default=rules.marked_hidden)
    entry_hidden: Callable[[os.DirEntry[str]], bool] = field(default=_entry_marked)
    case_blind: bool = deny_floor.CASE_BLIND

    @classmethod
    def here(
        cls,
        shared: Sequence[Path] = (),
        *,
        home: Path | None = None,
        data_dir: Path | None = None,
        mounts: Sequence[Path] | None = None,
        finder: Callable[[Path], Found] | None = None,
        protected: Sequence[Path] | None = None,
        system: Sequence[PurePath] | None = None,
        hidden: Callable[[PurePath], bool] = rules.marked_hidden,
    ) -> Fence:
        """This computer's fence: its home, drives, places, shares, system folders and
        protected ones. Touches the disk: call it within a deadline."""
        h = rules.resolved(home if home is not None else Path.home())
        folders, found_drives = (finder if finder is not None else _finder())(h)
        if mounts is None:
            # Windows drives are the drive letters; elsewhere D4's own list of drives.
            on_win = sys.platform == "win32"
            mounts = [p.path for p in found_drives] if on_win else list(rules.mount_roots())
        base = cls(
            home=h,
            data=rules.resolved(data_dir if data_dir is not None else paths.home()),
            drives=tuple(rules.resolved(d.parent) / d.name for d in mounts),
            system=tuple(system if system is not None else rules.system_folders()),
            protected=tuple(
                protected
                if protected is not None
                else deny_floor.DenyPolicy.default().protected_roots
            ),
            hidden=hidden,
        )
        return replace(
            base,
            shared=tuple(r for s in shared if (r := base._share_root(s)) is not None),
            folders=tuple(
                r for p in folders if (r := _resolve(p.path)) is not None and not base._judged(r)
            ),
        )

    def _share_root(self, raw: Path) -> PurePath | None:
        """A share's folder as a root of the fence, or None. A share whose folder was
        swapped for a link reaches nothing, as in the shares store, and a share must
        pass D4 in full: no exception for being too big."""
        r = _resolve(raw)
        if r is None or _key(r, self.case_blind) != _key(raw, self.case_blind):
            return None
        reason = rules.judge(
            r,
            home=self.home,
            data=self.data,
            protected=self.protected,
            mounts=self.drives,
            system=self.system,
            hidden=self.hidden,
        )
        return r if reason is None else None

    def _within(self, p: PurePath, root: PurePath) -> bool:
        if self.case_blind:
            return deny_floor.is_under(p, root)
        return p.parts[: len(root.parts)] == root.parts

    def roots(self) -> tuple[PurePath, ...]:
        return (self.home, *self.drives, *self.shared, *self.folders)

    def outermost(self, p: PurePath) -> PurePath | None:
        """The widest place holding `p`, where its breadcrumb starts."""
        holding = [r for r in self.roots() if self._within(p, r)]
        return min(holding, key=lambda r: len(r.parts)) if holding else None

    def _other_home(self, p: PurePath) -> bool:
        """Inside the folder of home folders (`/Users`, `C:\\Users`, `/home`) but not in
        the user's own: someone else's, or `Public`. The folder of homes itself may be
        opened on the way to the user's own, which is all it then shows."""
        homes = self.home.parent
        if homes == self.home or homes.parent == homes:
            return False  # a home at the top of a drive: no folder of homes
        if len(p.parts) <= len(homes.parts) or not self._within(p, homes):
            return False
        if self._within(p, self.home):
            return False
        # Except a folder the user shared from there (`/Users/Shared/Project`).
        return not any(self._within(p, s) for s in self.shared)

    def _judged(self, p: PurePath) -> str | None:
        if self._other_home(p):
            return OTHER_HOME
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

    def reason(self, p: PurePath) -> str | None:
        """Why the browser may not open a resolved path, or None. Touches no disk but
        through `hidden` and the protected folders' spellings."""
        p = rules._plain(p)  # `\\?\C:\...` is judged as `C:\...`
        if self.outermost(p) is None:
            return OUTSIDE
        return self._judged(p)


def _bounded[T](work: Callable[[], T], seconds: float) -> tuple[Outcome, T | None]:
    """Run `work` in a daemon thread for at most `seconds`: whether it finished, was
    too slow or raised, and its answer. A stale network drive can hang a system call;
    the thread is left behind, never her turn, and holds one of a few slots."""
    if not _SLOTS.acquire(blocking=False):
        return "slow", None
    box: list[T] = []
    failed: list[BaseException] = []

    def run() -> None:
        try:
            box.append(work())
        except Exception as e:
            failed.append(e)
            logger.info("folder browser can't tell: %s", type(e).__name__)
        finally:
            _SLOTS.release()

    t = threading.Thread(target=run, name="folder-browse", daemon=True)
    try:
        t.start()
    except RuntimeError:
        _SLOTS.release()
        return "raised", None
    t.join(seconds)
    if t.is_alive():
        return "slow", None
    if failed or not box:
        return "raised", None
    return "done", box[0]


def _resolve(path: PurePath) -> Path | None:
    if "\x00" in str(path):
        return None
    try:
        return rules.resolved(Path(path))
    except (OSError, RuntimeError, ValueError):
        return None


def _allowed(path: Path, fence: Fence) -> tuple[Path | None, str | None]:
    """The folder `path` resolves to, and why the browser may not open it."""
    p = _resolve(path)
    if p is None:
        return None, _GONE
    return p, fence.reason(p)


def _fence(fence: Fence | None, shared: Sequence[Path]) -> Fence:
    return fence if fence is not None else Fence.here(shared)


def allowed(
    path: Path,
    *,
    shared: Sequence[Path] = (),
    fence: Fence | None = None,
    deadline_s: float = DEADLINE_S,
) -> str | None:
    """Why the browser may not open `path`, in plain words, or None when it may. A
    link is judged as the folder it leads to. Anything that goes wrong refuses."""
    outcome, answer = _bounded(lambda: _allowed(path, _fence(fence, shared))[1], deadline_s)
    if outcome == "slow":
        return _SLOW
    if outcome == "raised":
        return _UNCHECKED
    return answer


def _shown(entry: os.DirEntry[str], folder: Path, fence: Fence, quiet: Fence) -> bool:
    """Whether one entry is a folder the browser lists. The folder holding it already
    passed the fence, so only the entry itself is asked whether the system hides it."""
    if entry.name.startswith(".") or fence.entry_hidden(entry):
        return False
    if entry.is_symlink() or entry.is_junction():
        # Never followed into a refused place: the folder it leads to must pass too.
        target = _resolve(Path(entry.path))
        return target is not None and target.is_dir() and fence.reason(target) is None
    if not entry.is_dir(follow_symlinks=False):
        return False
    return quiet.reason(folder / entry.name) is None


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
        return p, [], _FAILED
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


def _listing(path: Path, fence: Fence, limit: int) -> Listing:
    p, names, problem = _scan(path, fence)
    trail = tuple(crumbs(p, fence=fence)) if problem is None else ()
    return Listing(p, tuple(names[:limit]), max(0, len(names) - limit), problem, trail)


def listing(
    path: Path,
    *,
    shared: Sequence[Path] = (),
    fence: Fence | None = None,
    limit: int = LIMIT,
    deadline_s: float = DEADLINE_S,
) -> Listing:
    """The folder names in one folder (D5), at most `limit` with a count of the rest,
    and its breadcrumb. `shared` are the shares' folders in scope. Never raises: a
    folder past the fence, gone, unreadable or too slow comes back with no names and
    the problem in plain words."""
    outcome, answer = _bounded(lambda: _listing(path, _fence(fence, shared), limit), deadline_s)
    if outcome == "slow":
        return Listing(path, problem=_SLOW)
    if answer is None:
        return Listing(path, problem=_FAILED)
    return answer


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


def _step(current: Path | None, want: str, f: Fence, places_now: Sequence[Place]) -> Step:
    if want.lower() == "places":
        return Step(None)
    if want.lower() == "up":
        if current is None:
            return Step(None)
        here = _resolve(current)
        if here is None or _top(here, f) or here.parent == here:
            return Step(None)
        # Above a place's root is past the fence: back to the places list.
        return Step(None) if f.reason(here.parent) is not None else Step(here.parent)
    if want.startswith("~") or Path(want).is_absolute():
        try:
            p = Path(want).expanduser()
        except (RuntimeError, KeyError, OSError):
            return Step(current, _GONE)
        if not p.is_absolute():
            return Step(current, _GONE)  # `~nosuchuser`, left as it was
        resolved, reason = _allowed(p, f)
        if reason is not None:
            return Step(current, reason)
        return Step(resolved)
    if not want or "/" in want or "\\" in want:
        return Step(current, "say one folder name at a time, or the folder's full path")
    if current is None:
        label, why = _match(want, [p.label for p in places_now])
        if label is None:
            return Step(None, why)
        return Step(next(p.path for p in places_now if p.label == label))
    folder, names, problem = _scan(current, f)
    if problem is not None:
        return Step(current, problem)
    name, why = _match(want, names)
    if name is None:
        return Step(current, why)
    return Step(folder / name)


def step(
    current: Path | None,
    target: str,
    *,
    shared: Sequence[Path] = (),
    fence: Fence | None = None,
    places_now: Sequence[Place] = (),
    deadline_s: float = DEADLINE_S,
) -> Step:
    """Where `target` takes the browser from `current` (None: the places list).

    `target` is `places`, `up`, a full path, or a name from what the browser shows
    now: a folder in `current`, or a place's label in `places_now`. Never raises."""
    want = target.strip()
    outcome, answer = _bounded(
        lambda: _step(current, want, _fence(fence, shared), places_now), deadline_s
    )
    if outcome == "slow":
        return Step(current, _SLOW)
    if answer is None:
        return Step(current, _FAILED)
    return answer


def crumbs(path: PurePath, *, fence: Fence) -> list[tuple[str, PurePath]]:
    """The breadcrumb for a folder, from the place holding it down: Home, Documents..."""
    root = fence.outermost(path)
    if root is None:
        return [(path.name or str(path), path)]
    if root == fence.home:
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
    so no drive is touched."""
    if not entry.is_symlink():
        return False
    try:
        target = os.readlink(entry.path)
    except OSError:
        return False
    return target.rstrip("/") == ""


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
        try:
            found = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(g), 0, None, ctypes.byref(out)
            )
            return out.value if found == 0 else None
        finally:
            # Freed whatever the answer: the system may hand back memory on failure too.
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
    deadline: what hangs leaves what was already found. The drives are looked for
    before the shares, so one hung share can't take them away."""
    h = home if home is not None else Path.home()
    find = finder if finder is not None else _finder()
    got: dict[str, list[Place]] = {"folders": [], "shared": [], "drives": []}

    def gather() -> None:
        folders, drives = find(h)
        got["folders"] = [p for p in folders if p.path != h]
        got["drives"] = list(drives)
        for name, path in shared:
            if path.is_dir():
                got["shared"] = [*got["shared"], Place(name, path, "shared")]

    _bounded(gather, deadline_s)
    return [Place("Home", h, "home"), *got["folders"], *got["shared"], *got["drives"]]
