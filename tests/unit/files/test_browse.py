"""The folder browser's listing (the shared folders spec, D5, D6, D8): folder names only,
inside a fence, with fake homes, drives and system calls for every system."""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from typing import Any

import pytest

from tests.links import junction, symlink_or_skip
from trillion.files import browse, rules


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    """A fake home with some folders, a file, hidden things and Trillion's data, a drive,
    and a folder outside both."""
    tmp = tmp_path.resolve()
    home = tmp / "Users" / "sam"
    for name in ("Desktop", "Documents/Clients/Website", "Music", ".hidden", ".trillion"):
        (home / name).mkdir(parents=True)
    (home / "notes.txt").write_text("not a folder")
    drive = tmp / "Volumes" / "Backup"
    (drive / "Photos").mkdir(parents=True)
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    return {"tmp": tmp, "home": home, "data": home / ".trillion", "drive": drive, "out": elsewhere}


def fence(world: dict[str, Path], **kw: Any) -> browse.Fence:
    base: dict[str, Any] = {
        "home": world["home"],
        "data": world["data"],
        "drives": (world["drive"],),
        "hidden": lambda _p: False,
    }
    base.update(kw)
    return browse.Fence(**base)


def link(at: Path, to: Path) -> None:
    # A junction needs no privilege on Windows, so the link tests run there too.
    if sys.platform == "win32":
        junction(at, to)
    else:
        symlink_or_skip(at, to)


# --- the listing (D5) -------------------------------------------------------


def test_folder_names_only(world: dict[str, Path]) -> None:
    got = browse.listing(world["home"], fence=fence(world))
    assert got.problem is None
    assert got.path == world["home"]
    assert got.folders == ("Desktop", "Documents", "Music")
    assert got.more == 0
    assert all(isinstance(n, str) for n in got.folders)


def test_sorted_without_regard_to_case(world: dict[str, Path]) -> None:
    for name in ("cherry", "Banana", "apple"):
        (world["out"] / name).mkdir()
    f = fence(world, shared=(world["out"],))
    assert browse.listing(world["out"], fence=f).folders == ("apple", "Banana", "cherry")


def test_at_most_200_names_and_a_count_of_the_rest(world: dict[str, Path]) -> None:
    for i in range(250):
        (world["out"] / f"f{i:03}").mkdir()
    for i in range(30):
        (world["out"] / f"file{i}.txt").write_text("")
    got = browse.listing(world["out"], fence=fence(world, shared=(world["out"],)))
    assert len(got.folders) == 200
    assert got.folders[0] == "f000"
    assert got.more == 50


class _FlaggedOs:
    """The os module, but `stat` of some names says the system marks them hidden."""

    def __init__(self, names: set[str], **marks: int) -> None:
        self._names = names
        self._marks = marks

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def stat(self, path: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path).name in self._names:
            return SimpleNamespace(**{"st_flags": 0, "st_file_attributes": 0, **self._marks})
        return os.stat(path, *args, **kwargs)


def _marked(world: dict[str, Path]) -> Any:
    """The system's own hidden check, asked only inside the test's folder: the real
    folders above it (`/private` on macOS) are hidden themselves."""
    return lambda p: p.is_relative_to(world["tmp"]) and rules.marked_hidden(p)


def test_hidden_on_macos_by_the_hidden_flag(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    (world["home"] / "Library").mkdir()
    (world["home"] / "Stuff").mkdir()
    monkeypatch.setattr(rules, "os", _FlaggedOs({"Stuff", "Library"}, st_flags=stat.UF_HIDDEN))
    f = fence(world, hidden=_marked(world))
    got = browse.listing(world["home"], fence=f)
    assert got.problem is None
    assert got.folders == ("Desktop", "Documents", "Music")


@pytest.mark.parametrize("attribute", ["FILE_ATTRIBUTE_HIDDEN", "FILE_ATTRIBUTE_SYSTEM"])
def test_hidden_on_windows_by_attribute(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch, attribute: str
) -> None:
    (world["home"] / "Stuff").mkdir()
    marks = {"st_file_attributes": getattr(stat, attribute)}
    monkeypatch.setattr(rules, "os", _FlaggedOs({"Stuff"}, **marks))
    f = fence(world, hidden=_marked(world))
    assert browse.listing(world["home"], fence=f).folders == ("Desktop", "Documents", "Music")


def test_hidden_on_linux_by_a_leading_dot_only(world: dict[str, Path]) -> None:
    (world["home"] / ".config").mkdir()
    (world["home"] / "Plain").mkdir()
    got = browse.listing(world["home"], fence=fence(world)).folders
    assert "Plain" in got
    assert not [n for n in got if n.startswith(".")]


def test_protected_and_trillions_own_folders_left_out(world: dict[str, Path]) -> None:
    docs = world["home"] / "Documents"
    for name in ("Vault", "keys.env", "TrillionData", "Fine"):
        (docs / name).mkdir()
    f = fence(world, data=docs / "TrillionData", protected=(docs / "Vault",))
    assert browse.listing(docs, fence=f).folders == ("Clients", "Fine")


def test_a_link_is_listed_only_when_it_leads_inside_the_fence(world: dict[str, Path]) -> None:
    home = world["home"]
    (home / ".ssh").mkdir()
    link(home / "Pictures", world["drive"] / "Photos")
    link(home / "Keys", home / ".ssh")
    link(home / "Away", world["out"])
    link(home / "Own", world["data"])
    got = browse.listing(home, fence=fence(world)).folders
    assert "Pictures" in got
    assert not {"Keys", "Away", "Own"} & set(got)


def test_a_link_to_a_file_is_not_listed(world: dict[str, Path]) -> None:
    if sys.platform == "win32":
        pytest.skip("a junction leads only to a folder")
    symlink_or_skip(world["home"] / "Note", world["home"] / "notes.txt")
    assert "Note" not in browse.listing(world["home"], fence=fence(world)).folders


def test_a_folder_that_vanished_is_a_problem_not_an_error(world: dict[str, Path]) -> None:
    got = browse.listing(world["home"] / "Gone", fence=fence(world))
    assert got.folders == ()
    assert got.problem == "that folder could not be found"


def test_a_file_is_not_a_folder_to_open(world: dict[str, Path]) -> None:
    got = browse.listing(world["home"] / "notes.txt", fence=fence(world))
    assert got.folders == ()
    assert got.problem == "that is not a folder"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no mode bits to take away")
def test_a_folder_that_cannot_be_read_is_a_problem(world: dict[str, Path]) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads every folder")
    locked = world["home"] / "Music"
    locked.chmod(0)
    try:
        got = browse.listing(locked, fence=fence(world))
    finally:
        locked.chmod(0o755)
    assert got.folders == ()
    assert got.problem == "Trillion isn't allowed to read that folder"


def test_past_the_fence_is_refused_with_the_reason(world: dict[str, Path]) -> None:
    got = browse.listing(world["out"], fence=fence(world))
    assert got.folders == ()
    assert got.problem == browse.OUTSIDE
    assert browse.listing(world["data"], fence=fence(world)).problem is not None


def test_a_slow_folder_gives_up_at_the_deadline(world: dict[str, Path]) -> None:
    gate = threading.Event()

    def stuck(_p: PurePath) -> bool:
        gate.wait(5)
        return False

    start = time.monotonic()
    got = browse.listing(world["home"], fence=fence(world, hidden=stuck), deadline_s=0.2)
    gate.set()
    assert time.monotonic() - start < 1.5
    assert got.folders == ()
    assert got.problem == "that folder took too long to open"


# --- the fence (D6), per system, on paths alone --------------------------------


def _mac() -> browse.Fence:
    return browse.Fence(
        home=PurePosixPath("/Users/sam"),
        data=PurePosixPath("/Users/sam/.trillion"),
        drives=(PurePosixPath("/Volumes/Backup"),),
        system=rules.POSIX_SYSTEM,
        hidden=lambda _p: False,
        case_blind=True,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/Users/sam",
        "/Users/sam/Documents",
        "/Users/sam/Library/CloudStorage/Dropbox",
        "/Users/sam/Library/CloudStorage/Dropbox/Clients",
        "/Users/sam/Library/Mobile Documents/com~apple~CloudDocs",
        "/Users/sam/Library/Mobile Documents/iCloud~md~obsidian/Documents",
        "/Volumes/Backup",
        "/Volumes/Backup/Photos",
        "/users/SAM/documents",
    ],
)
def test_fence_on_macos_lets_in(path: str) -> None:
    assert _mac().reason(PurePosixPath(path)) is None


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/Users",
        "/Users/Guest",
        "/Volumes",
        "/System",
        "/Library",
        "/usr/local",
        "/private/var",
        "/Applications",
        "/Users/sam/Library",
        "/Users/sam/Library/Mobile Documents",
        "/Users/sam/.trillion",
        "/Users/sam/.ssh",
        "/Users/sam/.config",
        "/Users/sam/Documents/.git",
    ],
)
def test_fence_on_macos_keeps_out(path: str) -> None:
    assert _mac().reason(PurePosixPath(path)) is not None


def _windows() -> browse.Fence:
    return browse.Fence(
        home=PureWindowsPath("C:/Users/sam"),
        data=PureWindowsPath("C:/Users/sam/.trillion"),
        drives=(PureWindowsPath("C:/"), PureWindowsPath("D:/")),
        system=rules.WINDOWS_SYSTEM,
        hidden=lambda _p: False,
        case_blind=True,
    )


@pytest.mark.parametrize(
    "path",
    ["C:/Users/sam", "C:/Users/sam/Documents", "D:/", "D:/Photos", "C:/", "c:/users/sam/music"],
)
def test_fence_on_windows_lets_in(path: str) -> None:
    assert _windows().reason(PureWindowsPath(path)) is None


@pytest.mark.parametrize(
    "path",
    [
        "C:/Windows",
        "C:/Windows/System32",
        "C:/Program Files",
        "C:/Program Files (x86)/App",
        "C:/ProgramData",
        "C:/Users/sam/AppData/Roaming",
        "C:/Users/sam/.trillion",
        "E:/",
    ],
)
def test_fence_on_windows_keeps_out(path: str) -> None:
    assert _windows().reason(PureWindowsPath(path)) is not None


def _linux() -> browse.Fence:
    return browse.Fence(
        home=PurePosixPath("/home/sam"),
        data=PurePosixPath("/home/sam/.trillion"),
        drives=(PurePosixPath("/media/sam/USB"), PurePosixPath("/mnt/data")),
        system=rules.POSIX_SYSTEM,
        hidden=lambda _p: False,
        case_blind=False,
    )


@pytest.mark.parametrize(
    "path", ["/home/sam", "/home/sam/Projects", "/media/sam/USB/x", "/mnt/data"]
)
def test_fence_on_linux_lets_in(path: str) -> None:
    assert _linux().reason(PurePosixPath(path)) is None


@pytest.mark.parametrize(
    "path", ["/home", "/home/other", "/home/Sam/Projects", "/proc", "/etc", "/sys/x", "/mnt"]
)
def test_fence_on_linux_keeps_out(path: str) -> None:
    # Linux names are case-sensitive: /home/Sam is someone else's folder.
    assert _linux().reason(PurePosixPath(path)) is not None


def test_allowed_on_the_disk(world: dict[str, Path]) -> None:
    f = fence(world)
    assert browse.allowed(world["home"], fence=f) is None
    assert browse.allowed(world["drive"] / "Photos", fence=f) is None
    assert browse.allowed(world["out"], fence=f) == browse.OUTSIDE
    assert browse.allowed(world["data"], fence=f) is not None
    assert browse.allowed(Path("bad\x00name"), fence=f) is not None


def test_a_link_is_judged_as_where_it_leads(world: dict[str, Path]) -> None:
    link(world["home"] / "Away", world["out"])
    assert browse.allowed(world["home"] / "Away", fence=fence(world)) == browse.OUTSIDE


# --- moving: step and up ---------------------------------------------------------


def test_up_moves_to_the_folder_above(world: dict[str, Path]) -> None:
    docs = world["home"] / "Documents"
    assert browse.step(docs / "Clients", "up", fence=fence(world)).to == docs
    assert browse.step(docs, "up", fence=fence(world)).to == world["home"]


@pytest.mark.parametrize("root", ["home", "drive"])
def test_up_from_a_place_root_is_the_places_list(world: dict[str, Path], root: str) -> None:
    got = browse.step(world[root], "up", fence=fence(world))
    assert got.to is None
    assert got.problem is None


def test_up_from_a_share_outside_home_is_the_places_list(world: dict[str, Path]) -> None:
    f = fence(world, shared=(world["out"],))
    assert browse.step(world["out"], "up", fence=f).to is None


def test_places_and_up_at_the_places_list(world: dict[str, Path]) -> None:
    assert browse.step(world["home"], "places", fence=fence(world)).to is None
    assert browse.step(None, "up", fence=fence(world)).to is None


def test_a_name_from_the_listing_any_case(world: dict[str, Path]) -> None:
    docs = world["home"] / "Documents"
    got = browse.step(docs, "  clients ", fence=fence(world))
    assert got.problem is None
    assert got.to == docs / "Clients"


def test_an_unknown_name_stays_put_and_says_why(world: dict[str, Path]) -> None:
    got = browse.step(world["home"], "Taxes", fence=fence(world))
    assert got.to == world["home"]
    assert got.problem == "there is no folder called Taxes here"
    # A hidden or refused folder is not there to open by name either.
    assert browse.step(world["home"], ".trillion", fence=fence(world)).problem is not None


def test_a_name_from_the_places_list(world: dict[str, Path]) -> None:
    shown = [browse.Place("Documents", world["home"] / "Documents", "folder")]
    got = browse.step(None, "documents", fence=fence(world), places_now=shown)
    assert got.to == world["home"] / "Documents"
    assert browse.step(None, "Nowhere", fence=fence(world), places_now=shown).to is None


def test_a_full_path_is_fenced(world: dict[str, Path]) -> None:
    f = fence(world)
    assert (
        browse.step(None, str(world["drive"] / "Photos"), fence=f).to == world["drive"] / "Photos"
    )
    refused = browse.step(world["home"], str(world["out"]), fence=f)
    assert refused.to == world["home"]
    assert refused.problem == browse.OUTSIDE


def test_crumbs_start_at_the_place(world: dict[str, Path]) -> None:
    f = fence(world)
    website = world["home"] / "Documents" / "Clients" / "Website"
    assert [label for label, _p in browse.crumbs(website, fence=f)] == [
        "Home",
        "Documents",
        "Clients",
        "Website",
    ]
    assert browse.crumbs(world["drive"] / "Photos", fence=f)[0] == ("Backup", world["drive"])


# --- places (D8), per system -----------------------------------------------------


def test_macos_places(world: dict[str, Path], tmp_path: Path) -> None:
    home = world["home"]
    volumes = world["tmp"] / "Volumes"
    (volumes / "Data").mkdir()  # left over, not a drive
    (volumes / ".timemachine").mkdir()
    link(volumes / "Macintosh HD", Path(volumes.anchor))
    folders, drives = browse._macos(home, volumes=volumes, is_mount=lambda p: p.name != "Data")
    assert [(p.label, p.path, p.kind) for p in folders] == [
        ("Desktop", home / "Desktop", "folder"),
        ("Documents", home / "Documents", "folder"),
    ]
    assert [(p.label, p.path, p.kind) for p in drives] == [("Backup", volumes / "Backup", "drive")]


DESKTOP, DOCUMENTS, DOWNLOADS = (
    browse.FOLDERID_DESKTOP,
    browse.FOLDERID_DOCUMENTS,
    browse.FOLDERID_DOWNLOADS,
)


def test_windows_places_find_a_moved_documents_folder(world: dict[str, Path]) -> None:
    home = world["home"]
    moved = world["tmp"] / "OneDrive" / "Documents"
    moved.mkdir(parents=True)
    (home / "Downloads").mkdir()
    known = {DESKTOP: str(home / "Desktop"), DOCUMENTS: str(moved)}
    kinds = {"A:\\": 2, "C:\\": 3, "D:\\": 5, "E:\\": 2, "Z:\\": 4}
    with_media = {"E:\\"}
    folders, drives = browse._windows(
        home,
        known=known.get,
        drive_mask=lambda: sum(1 << (ord(d) - ord("A")) for d in "ACDEZ"),
        drive_type=lambda root: kinds.get(root, 1),
        has_volume=lambda root: root in with_media,
    )
    assert [(p.label, p.path) for p in folders] == [
        ("Desktop", home / "Desktop"),
        ("Documents", moved),
        ("Downloads", home / "Downloads"),  # not found by the system: the usual place
    ]
    # Empty removable and CD drives are left out; a fixed, a stick with a disk, a
    # network drive stay.
    assert [(p.label, str(p.path), p.kind) for p in drives] == [
        ("C:", "C:\\", "drive"),
        ("E:", "E:\\", "drive"),
        ("Z:", "Z:\\", "drive"),
    ]


def _xdg(answers: dict[str, str | None]) -> browse.Run:
    def run(args: Sequence[str]) -> str | None:
        assert args[0] == "xdg-user-dir"
        return answers.get(args[1])

    return run


def test_linux_places_from_xdg_user_dir(world: dict[str, Path]) -> None:
    home = world["home"]
    (home / "Schreibtisch").mkdir()
    (home / "Downloads").mkdir()
    root = world["tmp"] / "root"
    for d in ("media/sam/USB", "run/media/sam/Card", "mnt/data", "media/other/Theirs"):
        (root / d).mkdir(parents=True)
    run = _xdg(
        {
            "DESKTOP": f"{home / 'Schreibtisch'}\n",
            "DOCUMENTS": f"{home}\n",  # not set: xdg-user-dir answers with home itself
            "DOWNLOAD": None,  # failed
        }
    )
    folders, drives = browse._linux(home, run=run, user="sam", root=root, is_mount=lambda _p: True)
    # A question it could not answer falls back to the usual name.
    assert [(p.label, p.path) for p in folders] == [
        ("Desktop", home / "Schreibtisch"),
        ("Downloads", home / "Downloads"),
    ]
    assert [(p.label, p.path) for p in drives] == [
        ("USB", root / "media" / "sam" / "USB"),
        ("Card", root / "run" / "media" / "sam" / "Card"),
        ("data", root / "mnt" / "data"),
    ]


def test_linux_places_fall_back_to_english_names(world: dict[str, Path]) -> None:
    home = world["home"]
    (home / "Downloads").mkdir()
    folders, drives = browse._linux(
        home, run=lambda _a: None, user="sam", root=world["tmp"] / "none", is_mount=lambda _p: True
    )
    assert [p.label for p in folders] == ["Desktop", "Documents", "Downloads"]
    assert drives == []


def test_linux_folders_that_are_not_drives_are_left_out(world: dict[str, Path]) -> None:
    root = world["tmp"] / "root"
    (root / "mnt" / "empty").mkdir(parents=True)
    _f, drives = browse._linux(
        world["home"], run=lambda _a: None, user="sam", root=root, is_mount=lambda _p: False
    )
    assert drives == []


def test_places_in_order_with_the_shares(world: dict[str, Path]) -> None:
    home = world["home"]
    website = home / "Documents" / "Clients" / "Website"

    def finder(h: Path) -> tuple[list[browse.Place], list[browse.Place]]:
        return (
            [browse.Place("Documents", h / "Documents", "folder")],
            [browse.Place("Backup", world["drive"], "drive")],
        )

    got = browse.places(
        [("Website project", website), ("Missing", home / "Gone")], home=home, finder=finder
    )
    assert [(p.label, p.kind) for p in got] == [
        ("Home", "home"),
        ("Documents", "folder"),
        ("Website project", "shared"),
        ("Backup", "drive"),
    ]
    assert got[2].row() == {"label": "Website project", "path": str(website), "kind": "shared"}


def test_places_keep_what_was_found_when_a_drive_hangs(world: dict[str, Path]) -> None:
    gate = threading.Event()

    def finder(h: Path) -> tuple[list[browse.Place], list[browse.Place]]:
        gate.wait(5)
        return [], []

    start = time.monotonic()
    got = browse.places(home=world["home"], finder=finder, deadline_s=0.2)
    gate.set()
    assert time.monotonic() - start < 1.5
    assert [p.kind for p in got] == ["home"]


# --- the real system ---------------------------------------------------------------


# On this computer, as the user would see it: in time, and nothing but folders.


def test_real_places_and_home_listing_answer_in_time() -> None:
    start = time.monotonic()
    found = browse.places()
    assert time.monotonic() - start < 2.5
    assert found[0].kind == "home"
    start = time.monotonic()
    got = browse.listing(Path.home())
    assert time.monotonic() - start < 2.5
    assert got.problem is None or got.folders == ()
    for name in got.folders:
        assert (got.path / name).is_dir(), name
