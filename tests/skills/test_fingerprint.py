"""A skill's fingerprint changes with any change to its files, and only then."""

from __future__ import annotations

import os
import sys
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
