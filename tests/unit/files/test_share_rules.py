"""D4: the folders that can never be shared, judged with a fake home, data folder and drives."""

from __future__ import annotations

import sys
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

import pytest

from tests.links import junction, symlink_or_skip
from trillion.files import rules
from trillion.spine import deny_floor


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    """A fake home with Trillion's data inside it, a fake drive, and a folder to share."""
    users = tmp_path / "Users"
    home = users / "sam"
    data = home / ".trillion"
    data.mkdir(parents=True)
    project = home / "Documents" / "Website"
    project.mkdir(parents=True)
    volumes = tmp_path / "Volumes"
    drive = volumes / "Backup"
    drive.mkdir(parents=True)
    keychains = home / "Library" / "Keychains"
    keychains.mkdir(parents=True)
    return {
        "tmp": tmp_path,
        "users": users,
        "home": home,
        "data": data,
        "project": project,
        "volumes": volumes,
        "drive": drive,
        "keychains": keychains,
    }


def why(world: dict[str, Path], p: Path) -> str | None:
    return rules.why_not(
        p,
        home=world["home"],
        data_dir=world["data"],
        protected_roots=(world["keychains"],),
        mounts=(world["drive"],),
        system=(),
        hidden=lambda _p: False,
    )


def link(at: Path, to: Path) -> None:
    # A junction needs no privilege on Windows, so the link tests run there too.
    if sys.platform == "win32":
        junction(at, to)
    else:
        symlink_or_skip(at, to)


def test_an_ordinary_folder_may_be_shared(world):
    assert why(world, world["project"]) is None


def test_the_home_folder_itself_is_refused(world):
    reason = why(world, world["home"])
    assert reason is not None and "home" in reason


def test_a_folder_holding_the_home_folder_is_refused(world):
    assert why(world, world["users"]) is not None


def test_a_drive_root_is_refused(world):
    anchor = Path(world["tmp"].anchor)  # "/" or "C:\\"
    reason = why(world, anchor)
    assert reason is not None and "drive" in reason


def test_a_mounted_drive_is_refused(world):
    reason = why(world, world["drive"])
    assert reason is not None and "drive" in reason
    # A folder on the drive is fine; the folder holding the drives is not.
    inner = world["drive"] / "Photos"
    inner.mkdir()
    assert why(world, inner) is None
    assert why(world, world["volumes"]) is not None


def test_the_data_folder_and_anything_in_it_is_refused(world):
    assert why(world, world["data"]) is not None
    inner = world["data"] / "memory"
    inner.mkdir()
    reason = why(world, inner)
    assert reason is not None and "Trillion" in reason


def test_a_folder_holding_the_data_folder_is_refused(tmp_path):
    # Trillion's data kept outside the home folder: its parents are refused for it.
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "elsewhere" / "state" / "trillion"
    data.mkdir(parents=True)
    for p in (data.parent, data.parent.parent):
        reason = rules.why_not(
            p,
            home=home,
            data_dir=data,
            protected_roots=(),
            mounts=(),
            system=(),
            hidden=lambda _p: False,
        )
        assert reason is not None and "Trillion" in reason


@pytest.mark.parametrize("name", [".ssh", ".SSH", ".\u017fsh", ".git", ".aws", ".gnupg"])
def test_protected_folders_are_refused(world, name):
    folder = world["project"] / name
    folder.mkdir(exist_ok=True)
    assert why(world, folder) is not None


def test_a_protected_root_and_what_is_inside_it_is_refused(world):
    assert why(world, world["keychains"]) is not None
    assert why(world, world["home"] / "Library" / "KEYCHAINS" / "x") is not None


def test_hidden_folders_are_refused(world):
    dotted = world["project"] / ".cache"
    dotted.mkdir()
    reason = why(world, dotted)
    assert reason is not None and "hidden" in reason
    inside = dotted / "pages"
    inside.mkdir()
    assert why(world, inside) is not None


def test_a_folder_marked_hidden_by_the_system_is_refused(world):
    reason = rules.why_not(
        world["project"],
        home=world["home"],
        data_dir=world["data"],
        protected_roots=(),
        mounts=(),
        system=(),
        hidden=lambda p: p == world["project"],
    )
    assert reason is not None and "hidden" in reason


@pytest.mark.parametrize("target", ["home", "data", "drive", "keychains", "ssh"])
def test_a_link_to_a_refused_folder_is_refused(world, target):
    ssh = world["home"] / ".ssh"
    ssh.mkdir(exist_ok=True)
    to = ssh if target == "ssh" else world[target]
    at = world["project"] / "innocent"
    link(at, to)
    assert why(world, at) is not None


def test_a_link_to_an_allowed_folder_is_judged_as_that_folder(world):
    at = world["tmp"] / "shortcut"
    link(at, world["project"])
    assert why(world, at) is None
    assert rules.resolved(at) == deny_floor.canonical(world["project"].resolve())


def test_a_name_with_a_colon_is_refused(world):
    # On Windows `name::$DATA` is a stream of the file, not a folder of its own.
    assert why(world, world["project"] / "x::$DATA") is not None


def test_the_reasons_are_plain_words(world):
    for p in (world["home"], world["data"], world["drive"], Path(world["tmp"].anchor)):
        reason = why(world, p)
        assert reason and reason[0].islower() and "_" not in reason


def test_the_defaults_refuse_the_real_home_and_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "th"))
    assert rules.why_not(Path.home()) is not None
    assert rules.why_not(Path(Path.home().anchor)) is not None
    assert rules.why_not(tmp_path / "th") is not None


def test_mount_roots_on_each_system(tmp_path):
    (tmp_path / "Volumes" / "Backup").mkdir(parents=True)
    (tmp_path / "media" / "sam" / "USB").mkdir(parents=True)
    (tmp_path / "run" / "media" / "sam" / "Card").mkdir(parents=True)
    (tmp_path / "mnt" / "data").mkdir(parents=True)
    found = set(rules.mount_roots(root=tmp_path))
    assert found == {
        tmp_path / "Volumes" / "Backup",
        tmp_path / "media" / "sam" / "USB",
        tmp_path / "run" / "media" / "sam" / "Card",
        tmp_path / "mnt" / "data",
    }


def test_mount_roots_with_none_present(tmp_path):
    assert rules.mount_roots(root=tmp_path) == ()


def test_the_system_hidden_flag(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert rules.marked_hidden(plain) is False
    assert rules.marked_hidden(tmp_path / "missing") is False


@pytest.mark.skipif(sys.platform != "darwin", reason="UF_HIDDEN is a macOS flag")
def test_the_macos_hidden_flag_is_seen(tmp_path):
    import os
    import stat

    folder = tmp_path / "secretive"
    folder.mkdir()
    os.chflags(folder, stat.UF_HIDDEN)
    assert rules.marked_hidden(folder) is True


# ── Folders inside hidden ones, and the apps' own folders ──────────────


def test_a_folder_inside_a_system_hidden_one_is_refused(world):
    support = world["home"] / "Library" / "Application Support"
    support.mkdir(parents=True)
    reason = rules.why_not(
        support,
        home=world["home"],
        data_dir=world["data"],
        protected_roots=(),
        mounts=(),
        system=(),
        hidden=lambda q: q == world["home"] / "Library",
    )
    assert reason is not None and "hidden" in reason


@pytest.mark.parametrize(
    "rel", ["Library", "Library/Mail", "LIBRARY/Mail", "AppData", "AppData/Roaming/App"]
)
def test_the_apps_own_folders_are_refused_even_when_not_hidden(world, rel):
    folder = world["home"] / rel
    folder.mkdir(parents=True, exist_ok=True)
    reason = why(world, folder)
    assert reason is not None and "apps" in reason


def test_a_folder_named_library_elsewhere_may_be_shared(world):
    books = world["project"] / "Library"
    books.mkdir()
    assert why(world, books) is None


# ── Other people's homes, system folders, mount points ─────────────────


@pytest.mark.parametrize("name", ["Guest", "other", "Shared"])
def test_another_home_folder_is_refused(world, name):
    other = world["users"] / name
    other.mkdir()
    reason = why(world, other)
    assert reason is not None and "home" in reason
    inside = other / "Documents"
    inside.mkdir()
    assert why(world, inside) is None


@pytest.mark.parametrize("raw", ["/root", "/var/root"])
def test_roots_home_is_refused(raw):
    reason = rules.judge(
        PurePosixPath(raw), home=PurePosixPath("/home/sam"), data=PurePosixPath("/home/sam/.t")
    )
    assert reason is not None and "home" in reason


@pytest.mark.parametrize(
    "raw",
    [
        "/System",
        "/System/Volumes/Data",
        "/Library/Application Support",
        "/Applications/App.app",
        "/usr/local",
        "/etc",
        "/bin",
        "/sbin",
        "/var/log",
        "/private/tmp",
        "/proc/1",
        "/sys",
        "/dev",
    ],
)
def test_system_folders_are_refused(raw):
    reason = rules.judge(
        PurePosixPath(raw),
        home=PurePosixPath("/Users/sam"),
        data=PurePosixPath("/Users/sam/.trillion"),
        system=rules.POSIX_SYSTEM,
    )
    assert reason is not None and "system" in reason


@pytest.mark.parametrize(
    "raw",
    [
        "C:/Windows",
        "c:/windows/System32",
        "C:/Program Files/App",
        "C:/Program Files (x86)",
        "C:/ProgramData/App",
    ],
)
def test_windows_system_folders_are_refused(raw):
    reason = rules.judge(
        PureWindowsPath(raw),
        home=PureWindowsPath("C:/Users/sam"),
        data=PureWindowsPath("C:/Users/sam/.trillion"),
        system=rules.WINDOWS_SYSTEM,
    )
    assert reason is not None and "system" in reason


def test_the_system_list_follows_the_platform(monkeypatch):
    monkeypatch.setattr(rules.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", "D:/Win")
    assert PureWindowsPath("D:/Win") in rules.system_folders()
    monkeypatch.setattr(rules.sys, "platform", "linux")
    assert rules.system_folders() == rules.POSIX_SYSTEM


def test_a_mount_point_is_refused(world):
    reason = rules.why_not(
        world["project"],
        home=world["home"],
        data_dir=world["data"],
        protected_roots=(),
        mounts=(),
        system=(),
        hidden=lambda _p: False,
        is_mount=lambda q: q == world["project"],
    )
    assert reason is not None and "drive" in reason


def test_mount_point_compares_devices(tmp_path):
    assert rules.mount_point(Path(tmp_path.anchor)) is True
    assert rules.mount_point(tmp_path) is False
    assert rules.mount_point(tmp_path / "missing") is False


def test_holding_home_is_seen_by_the_folder_not_only_its_spelling(world, monkeypatch):
    # A firmlink gives one folder two spellings; the rule asks the disk too.
    alias = world["tmp"] / "alias"
    alias.mkdir()
    monkeypatch.setattr(rules, "_samefile", lambda a, b: a == alias and b == world["users"])
    reason = why(world, alias)
    assert reason is not None and "holds a home" in reason
    monkeypatch.setattr(rules, "_samefile", lambda a, b: a == alias and b == world["home"])
    assert "home folder is never" in (why(world, alias) or "")
    monkeypatch.setattr(rules, "_samefile", lambda a, b: a == alias and b == world["data"])
    assert "Trillion" in (why(world, alias) or "")


def test_samefile_of_a_missing_folder_is_false(tmp_path):
    assert rules._samefile(tmp_path / "a", tmp_path / "b") is False


def test_mount_roots_are_looked_up_at_most_every_few_seconds(tmp_path):
    (tmp_path / "mnt" / "one").mkdir(parents=True)
    first = rules.mount_roots(root=tmp_path)
    (tmp_path / "mnt" / "two").mkdir()
    assert rules.mount_roots(root=tmp_path) == first
    rules._mounts_cache.clear()
    assert len(rules.mount_roots(root=tmp_path)) == 2


# ── Windows spellings ──────────────────────────────────────────────────

_WIN_HOME = PureWindowsPath("C:/Users/sam")
_WIN_DATA = PureWindowsPath("C:/Users/sam/.trillion")


def win(raw: str) -> str | None:
    return rules.judge(PureWindowsPath(raw), home=_WIN_HOME, data=_WIN_DATA)


@pytest.mark.parametrize(
    "raw", ["C:/", "D:/", "d:\\", "//server/share", "//?/C:/", "//?/UNC/server/share"]
)
def test_windows_drives_and_shares_are_refused(raw):
    assert "drive" in (win(raw) or "")


@pytest.mark.parametrize("raw", ["C:/Users/sam", "c:/users/SAM", "//?/C:/Users/sam"])
def test_the_windows_home_in_any_spelling_is_refused(raw):
    assert "home folder is never" in (win(raw) or "")


def test_windows_other_homes_and_appdata_are_refused():
    assert "someone's home" in (win("C:/Users/Public") or "")
    assert "apps" in (win("C:/Users/sam/AppData/Roaming") or "")
    assert "apps" in (win("//?/C:/Users/sam/appdata/Local") or "")


def test_a_windows_project_may_be_shared():
    assert win("C:/Users/sam/Documents/Website") is None
    assert win("//?/C:/Users/sam/Documents/Website") is None
    assert win("D:/Projects") is None


def test_a_windows_stream_name_is_refused():
    assert win("C:/Users/sam/Documents/x::$DATA") is not None


def test_the_home_in_another_case_is_refused():
    reason = rules.judge(
        PurePosixPath("/Users/sam"),
        home=PurePosixPath("/Users/Sam"),
        data=PurePosixPath("/Users/Sam/.trillion"),
    )
    assert reason is not None and "home" in reason


def test_plain_leaves_posix_paths_alone():
    p: PurePath = PurePosixPath("/a/b")
    assert rules._plain(p) is p
