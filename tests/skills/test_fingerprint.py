"""A skill's fingerprint changes with any change to its files, and only then."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from trillion.skills.fingerprint import fingerprint


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    f = tmp_path / "s"
    f.mkdir()
    (f / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nx", encoding="utf-8")
    (f / "notes.md").write_text("a", encoding="utf-8")
    return f


def test_the_same_files_give_the_same_fingerprint(folder: Path) -> None:
    assert fingerprint(folder) == fingerprint(folder)
    assert fingerprint(folder).startswith("sha256:")


@pytest.mark.parametrize("change", ["edit", "add", "remove", "rename"])
def test_any_change_changes_it(folder: Path, change: str) -> None:
    before = fingerprint(folder)
    if change == "edit":
        (folder / "notes.md").write_text("b", encoding="utf-8")
    elif change == "add":
        (folder / "more.md").write_text("", encoding="utf-8")
    elif change == "remove":
        (folder / "notes.md").unlink()
    else:
        (folder / "notes.md").rename(folder / "other.md")
    assert fingerprint(folder) != before


def test_clutter_does_not_change_it(folder: Path) -> None:
    before = fingerprint(folder)
    (folder / ".DS_Store").write_bytes(b"\0")
    assert fingerprint(folder) == before


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_retargeting_a_symlink_changes_it(folder: Path, tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("a", encoding="utf-8")
    os.symlink(tmp_path / "a.md", folder / "link.md")
    before = fingerprint(folder)
    (folder / "link.md").unlink()
    os.symlink(tmp_path / "b.md", folder / "link.md")
    assert fingerprint(folder) != before


def test_file_contents_cannot_pose_as_other_files(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "a.md").write_bytes(b"X\0b.md\0file\0Y")
    (b / "a.md").write_bytes(b"X")
    (b / "b.md").write_bytes(b"Y")
    assert fingerprint(a) != fingerprint(b)


@pytest.mark.skipif(sys.platform == "win32", reason="no named pipes in the file system")
def test_a_named_pipe_is_never_opened(folder: Path) -> None:
    os.mkfifo(folder / "pipe")
    result: list[str] = []
    thread = threading.Thread(target=lambda: result.append(fingerprint(folder)), daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():  # let the stuck reader go before failing
        try:
            os.close(os.open(folder / "pipe", os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass
    assert result and result[0].startswith("sha256:")


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux allows non-UTF-8 names")
def test_a_file_name_that_is_not_text_is_still_hashed(folder: Path) -> None:
    before = fingerprint(folder)
    (folder / os.fsdecode(b"bad\xff.md")).write_bytes(b"x")
    assert fingerprint(folder) != before


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_a_junction_is_hashed_by_its_target_and_not_entered(folder: Path, tmp_path: Path) -> None:
    winapi = pytest.importorskip("_winapi")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("a", encoding="utf-8")
    winapi.CreateJunction(str(outside), str(folder / "more"))
    before = fingerprint(folder)
    (outside / "secret.md").write_text("b", encoding="utf-8")
    assert fingerprint(folder) == before


def test_a_folder_too_big_to_walk_has_no_fingerprint(folder: Path) -> None:
    from trillion.skills.parse import MAX_FILES

    for i in range(MAX_FILES + 1):
        (folder / f"f{i}.md").write_text("", encoding="utf-8")
    assert fingerprint(folder) == ""
