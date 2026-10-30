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


def _by_name(*names: str) -> Any:
    """An entry check that says the system marks these names hidden."""
    return lambda entry: entry.name in names


@pytest.mark.parametrize(
    ("marks", "hidden"),
    [
        ({"st_flags": stat.UF_HIDDEN}, True),  # macOS
        ({"st_file_attributes": stat.FILE_ATTRIBUTE_HIDDEN}, True),  # Windows
        ({"st_file_attributes": stat.FILE_ATTRIBUTE_SYSTEM}, True),
        ({"st_file_attributes": stat.FILE_ATTRIBUTE_DIRECTORY}, False),
        ({}, False),  # Linux: no marks, only a leading dot
    ],
)
def test_what_the_system_marks_hidden(marks: dict[str, int], hidden: bool) -> None:
    assert browse.marked(SimpleNamespace(**marks)) is hidden


def test_folders_the_system_marks_hidden_are_left_out(world: dict[str, Path]) -> None:
    (world["home"] / "Stuff").mkdir()
    f = fence(world, entry_hidden=_by_name("Stuff"))
    got = browse.listing(world["home"], fence=f)
    assert got.problem is None
    assert got.folders == ("Desktop", "Documents", "Music")


@pytest.mark.skipif(sys.platform != "darwin", reason="the hidden flag is macOS's")
def test_hidden_flag_on_this_mac(world: dict[str, Path]) -> None:
    (world["home"] / "Stuff").mkdir()
    os.chflags(world["home"] / "Stuff", stat.UF_HIDDEN)
    assert "Stuff" not in browse.listing(world["home"], fence=fence(world)).folders
    assert browse.listing(world["home"], fence=fence(world)).folders == (
        "Desktop",
        "Documents",
        "Music",
    )


@pytest.mark.skipif(sys.platform != "win32", reason="the hidden attribute is Windows's")
def test_hidden_attribute_on_this_windows(world: dict[str, Path]) -> None:
    import ctypes

    (world["home"] / "Stuff").mkdir()
    set_attributes = ctypes.windll.kernel32.SetFileAttributesW  # type: ignore[attr-defined]
    assert set_attributes(str(world["home"] / "Stuff"), stat.FILE_ATTRIBUTE_HIDDEN)
    assert browse.listing(world["home"], fence=fence(world)).folders == (
        "Desktop",
        "Documents",
        "Music",
    )


def test_a_link_the_system_marks_hidden_is_left_out(world: dict[str, Path]) -> None:
    # Windows keeps `My Documents` as a hidden, system junction to Documents.
    link(world["home"] / "My Documents", world["home"] / "Documents")
    f = fence(world, entry_hidden=_by_name("My Documents"))
    assert "My Documents" not in browse.listing(world["home"], fence=f).folders
    assert "My Documents" in browse.listing(world["home"], fence=fence(world)).folders


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
        "/Users/Shared",
        "/Users/bob/Documents",
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
    [
        "C:/Users/sam",
        "C:/Users/sam/Documents",
        "D:/",
        "D:/Photos",
        "C:/",
        "c:/users/sam/music",
        # The folder of homes, on the way to the user's own (which is all it shows).
        "C:/Users",
        "//?/C:/Users/sam/Documents",
    ],
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
        # Someone else's home, and anything in one.
        "C:/Users/bob",
        "C:/Users/bob/Documents",
        "C:/Users/Public",
        "C:/Users/Public/Documents",
        "C:/Users/Default",
        "c:/users/BOB",
        # The long and network spellings are judged as usually spelled.
        "//?/C:/Windows",
        "//?/C:/Users/bob",
        "//server/share/Projects",
        "//?/UNC/server/share",
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
    "path",
    [
        "/home",
        "/home/other",
        "/home/other/x",
        "/home/Sam/Projects",
        "/proc",
        "/etc",
        "/sys/x",
        "/mnt",
    ],
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
        [("Website project", website), ("Missing", home / "Gone")],
        home=home,
        finder=finder,
        fence=fence(world),
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


def test_places_keep_the_drives_when_a_share_hangs(world: dict[str, Path]) -> None:
    gate = threading.Event()

    class Stuck:
        def is_dir(self) -> bool:
            gate.wait(5)
            return True

    def finder(h: Path) -> tuple[list[browse.Place], list[browse.Place]]:
        return [], [browse.Place("Backup", world["drive"], "drive")]

    got = browse.places([("Hung", Stuck())], home=world["home"], finder=finder, deadline_s=0.2)  # type: ignore[list-item]
    gate.set()
    assert [p.kind for p in got] == ["home", "drive"]


# --- other homes, shares and places in the fence -----------------------------------


@pytest.fixture
def homes(tmp_path: Path) -> dict[str, Path]:
    """A drive holding a folder of homes: the user's, someone else's, and Public."""
    drive = tmp_path.resolve() / "C"
    users = drive / "Users"
    for name in ("sam/Documents", "bob/Documents", "Public/Documents"):
        (users / name).mkdir(parents=True)
    return {"drive": drive, "users": users, "home": users / "sam"}


def _homes_fence(homes: dict[str, Path], **kw: Any) -> browse.Fence:
    base: dict[str, Any] = {
        "home": homes["home"],
        "data": homes["home"] / ".trillion",
        "drives": (homes["drive"],),
        "hidden": lambda _p: False,
    }
    base.update(kw)
    return browse.Fence(**base)


def test_the_folder_of_homes_shows_only_the_users_own(homes: dict[str, Path]) -> None:
    f = _homes_fence(homes)
    assert browse.listing(homes["drive"], fence=f).folders == ("Users",)
    assert browse.listing(homes["users"], fence=f).folders == ("sam",)
    assert browse.listing(homes["users"] / "bob", fence=f).problem == browse.OTHER_HOME
    assert browse.step(homes["users"], "Public", fence=f).problem is not None


def test_a_link_into_someone_elses_home_is_left_out(homes: dict[str, Path]) -> None:
    link(homes["home"] / "Theirs", homes["users"] / "bob" / "Documents")
    f = _homes_fence(homes)
    assert "Theirs" not in browse.listing(homes["home"], fence=f).folders
    assert browse.allowed(homes["home"] / "Theirs", fence=f) == browse.OTHER_HOME


def test_a_folder_shared_from_beside_the_homes_opens(homes: dict[str, Path]) -> None:
    project = homes["users"] / "Public" / "Documents"
    f = _homes_fence(homes, shared=(project,))
    assert browse.allowed(project, fence=f) is None
    assert browse.allowed(homes["users"] / "Public", fence=f) == browse.OTHER_HOME


def _here(world: dict[str, Path], shared: Sequence[Path] = (), **kw: Any) -> browse.Fence:
    base: dict[str, Any] = {
        "home": world["home"],
        "data_dir": world["data"],
        "mounts": (world["drive"],),
        "place_folders": lambda _h: [],
        "protected": (),
        "system": (),
        "hidden": lambda _p: False,
    }
    base.update(kw)
    return browse.Fence.here(shared, **base)


def test_fence_here_takes_its_world(world: dict[str, Path]) -> None:
    website = world["home"] / "Documents" / "Clients" / "Website"
    f = _here(world, [website])
    assert f.home == world["home"]
    assert f.data == world["data"]
    assert f.drives == (world["drive"],)
    assert f.shared == (website,)
    assert browse.allowed(world["drive"] / "Photos", fence=f) is None


def test_a_share_swapped_for_a_link_does_not_widen_the_fence(world: dict[str, Path]) -> None:
    shared = world["home"] / "Documents" / "Shared"
    link(shared, world["out"])
    f = _here(world, [shared])
    assert f.shared == ()
    assert browse.allowed(world["out"], fence=f) == browse.OUTSIDE


@pytest.mark.parametrize("which", ["home", "drive", "data"])
def test_a_share_d4_refuses_is_no_root(world: dict[str, Path], which: str) -> None:
    assert _here(world, [world[which]]).shared == ()


def test_the_places_folders_open_wherever_they_are(world: dict[str, Path]) -> None:
    moved = world["out"] / "OneDrive" / "Documents"
    (moved / "Clients").mkdir(parents=True)
    places_now = [browse.Place("Documents", moved, "folder")]
    f = _here(world, place_folders=lambda _h: places_now)
    assert f.folders == (moved,)
    assert browse.listing(moved, fence=f).folders == ("Clients",)
    # Up from a place's root is past the fence: the places list.
    assert browse.step(moved, "up", fence=f).to is None
    assert browse.allowed(world["out"], fence=f) == browse.OUTSIDE


def test_a_place_folder_d6_refuses_is_no_root(world: dict[str, Path]) -> None:
    hidden = world["out"] / ".Documents"
    hidden.mkdir()
    f = _here(world, place_folders=lambda _h: [browse.Place("Documents", hidden, "folder")])
    assert f.folders == ()


@pytest.mark.parametrize("where", ["top", "homes", "link to homes"])
def test_a_place_folder_too_big_to_share_is_no_root(world: dict[str, Path], where: str) -> None:
    # A Desktop set to `/`, or a Documents link to the folder of homes, must not open
    # the whole computer (on Linux `/tmp`, `/srv`, `/run/user/...`).
    homes = world["home"].parent
    if where == "link to homes":
        link(world["home"] / "Docs", homes)
    path = {"top": Path(world["tmp"].anchor), "homes": homes}.get(where, world["home"] / "Docs")
    f = _here(world, place_folders=lambda _h: [browse.Place("Desktop", path, "folder")])
    assert f.folders == ()
    assert browse.allowed(world["out"], fence=f) == browse.OUTSIDE


def test_the_fence_names_drives_without_touching_them(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def touched(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a drive was touched")

    looked: list[Path] = []

    def folders(h: Path) -> list[browse.Place]:
        looked.append(h)
        return [browse.Place("Documents", h / "Documents", "folder")]

    monkeypatch.setattr(browse, "_finder", touched)
    monkeypatch.setattr(rules, "mount_point", touched)
    monkeypatch.setattr(browse, "_system_folders", folders)
    monkeypatch.setattr(browse, "_folders_cache", {})
    for _ in range(3):
        f = _here(world, mounts=None, place_folders=None)
    assert f.folders == (world["home"] / "Documents",)
    # Looked up once, then kept for a few seconds.
    assert looked == [world["home"]]


def test_the_fence_takes_every_drive_letter_without_asking_for_a_disk() -> None:
    kinds = {"C:\\": 3, "D:\\": 5, "E:\\": 2}

    def never(_root: str) -> bool:
        raise AssertionError("a drive was asked for its disk")

    drives = browse._windows_drives(
        lambda: sum(1 << (ord(d) - ord("A")) for d in "CDEF"),
        lambda root: kinds.get(root, 1),
        None,
    )
    assert [p.label for p in drives] == ["C:", "D:", "E:"]
    assert [p.label for p in browse._windows_drives(lambda: 0b100, kinds.get, never)] == ["C:"]


def test_places_leave_out_a_folder_beside_the_homes(homes: dict[str, Path]) -> None:
    beside = homes["users"] / "Public" / "Documents"

    def finder(h: Path) -> tuple[list[browse.Place], list[browse.Place]]:
        return [browse.Place("Documents", beside, "folder")], []

    got = browse.places(home=homes["home"], finder=finder, fence=_homes_fence(homes))
    assert [p.kind for p in got] == ["home"]


def test_up_stops_at_the_fence(world: dict[str, Path]) -> None:
    # A share inside a folder the browser may not open: up is the places list.
    inside = world["out"] / "Project"
    inside.mkdir()
    f = fence(world, shared=(inside,))
    assert browse.step(inside, "up", fence=f).to is None


@pytest.mark.parametrize("name", ["..", ".", "a/b", "a\\b", ""])
def test_names_that_are_not_one_folder(world: dict[str, Path], name: str) -> None:
    got = browse.step(world["home"] / "Documents", name, fence=fence(world))
    assert got.to == world["home"] / "Documents"
    assert got.problem is not None


def test_an_unknown_user_is_a_problem_not_an_error(world: dict[str, Path]) -> None:
    got = browse.step(world["home"], "~nosuchuser-trillion/x", fence=fence(world))
    assert got.to == world["home"]
    assert got.problem == "that folder could not be found"


def test_a_fence_that_cannot_be_built_is_a_problem(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: Any, **_k: Any) -> browse.Fence:
        raise OSError("no home")

    monkeypatch.setattr(browse.Fence, "here", broken)
    assert browse.listing(world["home"]).problem == "that folder could not be read"
    assert browse.step(world["home"], "Documents").problem == "that folder could not be read"
    assert browse.allowed(world["home"]) == "that folder could not be checked"


def test_allowed_refuses_when_the_check_raises(world: dict[str, Path]) -> None:
    def broken(_p: PurePath) -> bool:
        raise ValueError("bad")

    f = fence(world, hidden=broken)
    assert browse.allowed(world["home"], fence=f) == "that folder could not be checked"


def test_the_fence_is_built_within_the_deadline(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()

    def slow(*_a: Any, **_k: Any) -> browse.Fence:
        gate.wait(5)
        return fence(world)

    monkeypatch.setattr(browse.Fence, "here", slow)
    start = time.monotonic()
    got = browse.listing(world["home"], deadline_s=0.2)
    gate.set()
    assert time.monotonic() - start < 1.5
    assert got.problem == "that folder took too long to open"


def test_calls_still_running_are_capped(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browse, "_SLOTS", threading.BoundedSemaphore(2))
    gate = threading.Event()
    asked: list[PurePath] = []

    def stuck(p: PurePath) -> bool:
        asked.append(p)
        gate.wait(5)
        return False

    f = fence(world, hidden=stuck)
    home, music, docs = world["home"], world["home"] / "Music", world["home"] / "Documents"
    try:
        for hung in (home, music):
            got = browse.listing(hung, fence=f, deadline_s=0.05)
            assert got.problem == "that folder took too long to open"
        before = len(asked)
        # Asking again about a folder still being read waits on that call: no new one.
        assert browse.listing(home, fence=f, deadline_s=1.0).problem == (
            "that folder took too long to open"
        )
        # Both slots are held by hung calls: the next answers at once, starting nothing,
        # and says what is wrong rather than blaming the folder asked for.
        start = time.monotonic()
        assert browse.listing(docs, fence=fence(world), deadline_s=1.0).problem == browse.BUSY
        assert browse.step(docs, "up", fence=fence(world)).problem == browse.BUSY
        assert browse.allowed(docs, fence=fence(world)) == browse.BUSY
        assert time.monotonic() - start < 0.5
        assert len(asked) == before
        # Moves that touch no disk still work: the user can always get back.
        assert browse.step(docs, "places", fence=f) == browse.Step(None)
        assert browse.step(None, "up", fence=f) == browse.Step(None)
        shown = [browse.Place("Documents", docs, "folder")]
        assert browse.step(None, "documents", fence=f, places_now=shown).to == docs
    finally:
        gate.set()
    # Once they finish, their slots come back.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if browse.listing(docs, fence=fence(world)).problem is None:
            break
        time.sleep(0.02)
    assert browse.listing(docs, fence=fence(world)).problem is None


def test_places_cut_short_say_so_in_the_log(
    world: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    gate = threading.Event()

    def finder(h: Path) -> tuple[list[browse.Place], list[browse.Place]]:
        gate.wait(5)
        return [], []

    got = browse.places(home=world["home"], finder=finder, deadline_s=0.1)
    gate.set()
    assert [p.kind for p in got] == ["home"]
    assert "places list cut short" in caplog.text


def test_a_listing_carries_its_breadcrumb(world: dict[str, Path]) -> None:
    got = browse.listing(world["home"] / "Documents" / "Clients", fence=fence(world))
    assert [label for label, _p in got.crumbs] == ["Home", "Documents", "Clients"]


# --- the real system ---------------------------------------------------------------


# On this computer, as the user would see it: in time, and nothing but folders.


def test_real_places_and_home_listing_answer_in_time() -> None:
    start = time.monotonic()
    browse.Fence.here()
    assert time.monotonic() - start < 2.0
    start = time.monotonic()
    found = browse.places()
    assert time.monotonic() - start < 2.0
    assert found[0].kind == "home"
    start = time.monotonic()
    got = browse.listing(Path.home())
    assert time.monotonic() - start < 2.0
    assert got.problem is None or got.folders == ()
    for name in got.folders:
        assert (got.path / name).is_dir(), name
