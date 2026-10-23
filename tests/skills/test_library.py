"""Finding skills, their state, and the user's choices."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from trillion import paths
from trillion.skills import library
from trillion.skills.library import Library, LibraryError


def make(root: Path, name: str, description: str = "Does a thing.") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n", encoding="utf-8"
    )
    return folder


@pytest.fixture
def lib(tmp_path: Path) -> Library:
    curated = tmp_path / "curated"
    make(curated, "weekly-review")
    return Library(tmp_path / "home" / "skills.json", curated, data_root=tmp_path / "home")


def ids(lib: Library) -> dict[str, str]:
    return {e.id: e.state for e in lib.scan()}


def test_the_settings_file_is_in_trillions_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path))
    assert paths.skills_config_path() == paths.home() / "skills.json"


def test_curated_skills_are_found_and_start_off(lib: Library) -> None:
    assert ids(lib) == {"curated:weekly-review": "off"}


def test_a_folder_of_skills_and_a_folder_that_is_one(lib: Library, tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    a = make(mine, "alpha")
    make(mine / "nested" / "deeper", "too-deep")  # only direct subfolders count
    single = make(tmp_path, "solo")
    lib.add_folder(str(mine))
    lib.add_folder(str(single))
    assert ids(lib) == {
        "curated:weekly-review": "off",
        f"folder:{a.resolve()}": "off",
        f"folder:{single.resolve()}": "off",
    }


def test_a_skill_found_twice_is_listed_once(lib: Library, tmp_path: Path) -> None:
    a = make(tmp_path / "mine", "alpha")
    lib.add_folder(str(tmp_path / "mine"))
    lib.add_folder(str(a))
    assert [e.id for e in lib.scan()].count(f"folder:{a.resolve()}") == 1


def test_turning_on_records_the_fingerprint_and_a_change_turns_it_off(
    lib: Library, tmp_path: Path
) -> None:
    lib.turn_on("curated:weekly-review")
    assert ids(lib)["curated:weekly-review"] == "on"
    saved = json.loads((tmp_path / "home" / "skills.json").read_text())
    assert saved["enabled"]["curated:weekly-review"].startswith("sha256:")
    (tmp_path / "curated" / "weekly-review" / "extra.md").write_text("new", encoding="utf-8")
    assert ids(lib)["curated:weekly-review"] == "changed"
    assert lib.enabled() == []
    lib.turn_on("curated:weekly-review")  # looked again and chose again
    assert ids(lib)["curated:weekly-review"] == "on"
    lib.turn_off("curated:weekly-review")
    assert ids(lib)["curated:weekly-review"] == "off"


def test_an_invalid_skill_cannot_be_turned_on(lib: Library, tmp_path: Path) -> None:
    bad = tmp_path / "mine" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("no header", encoding="utf-8")
    lib.add_folder(str(tmp_path / "mine"))
    (entry,) = [e for e in lib.scan() if e.source == "folder"]
    assert entry.state == "invalid" and "no header" in entry.reason
    assert entry.fingerprint == ""  # never hashed: it can't be turned on
    with pytest.raises(LibraryError):
        lib.turn_on(entry.id)
    with pytest.raises(LibraryError):
        lib.turn_on("folder:/nowhere")


def test_only_skills_that_are_on_are_read_to_use_them(
    lib: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(tmp_path / "mine", "alpha")
    make(tmp_path / "mine", "beta")
    lib.add_folder(str(tmp_path / "mine"))
    lib.turn_on("curated:weekly-review")
    touched: list[str] = []
    real_parse, real_fingerprint = library.parse, library.fingerprint

    def parse(folder: Path) -> object:
        touched.append(folder.name)
        return real_parse(folder)

    def fingerprint(folder: Path) -> str:
        touched.append(folder.name)
        return real_fingerprint(folder)

    monkeypatch.setattr(library, "parse", parse)
    monkeypatch.setattr(library, "fingerprint", fingerprint)
    lib.enabled()
    lib.lookup("weekly-review")
    lib.prompt_lines()
    assert set(touched) == {"weekly-review"}
    touched.clear()
    lib.scan()
    assert set(touched) == {"weekly-review", "alpha", "beta"}


def test_folders_are_checked(lib: Library, tmp_path: Path) -> None:
    with pytest.raises(LibraryError, match="not a folder"):
        lib.add_folder(str(tmp_path / "missing"))
    (tmp_path / "home").mkdir(exist_ok=True)
    with pytest.raises(LibraryError, match="Trillion's own"):
        lib.add_folder(str(tmp_path / "home"))
    with pytest.raises(LibraryError, match="Trillion's own"):
        lib.add_folder(str(tmp_path))  # holds Trillion's folder
    with pytest.raises(LibraryError, match="whole drive"):
        lib.add_folder(tmp_path.anchor)


def test_a_folder_that_disappears_is_reported_not_dropped(lib: Library, tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    make(mine, "alpha")
    lib.add_folder(str(mine))
    (mine / "alpha" / "SKILL.md").unlink()
    (mine / "alpha").rmdir()
    mine.rmdir()
    assert [(f.path, f.found) for f in lib.folders()] == [(mine.resolve(), False)]
    assert ids(lib) == {"curated:weekly-review": "off"}


def test_a_linked_subfolder_is_not_a_skill(lib: Library, tmp_path: Path) -> None:
    elsewhere = make(tmp_path / "elsewhere", "alpha")
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "alpha").symlink_to(elsewhere, target_is_directory=True)
    lib.add_folder(str(mine))
    assert ids(lib) == {"curated:weekly-review": "off"}


def test_only_so_many_subfolders_are_looked_at(
    lib: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "MAX_SUBFOLDERS", 3)
    for name in ["a", "b", "c", "d", "e"]:
        make(tmp_path / "mine", name)
    lib.add_folder(str(tmp_path / "mine"))
    assert len([e for e in lib.scan() if e.source == "folder"]) == 3


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root"
)
def test_a_folder_that_cannot_be_listed_has_no_skills(lib: Library, tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    make(mine, "alpha")
    lib.add_folder(str(mine))
    mine.chmod(0)
    try:
        assert [(f.path, f.found) for f in lib.folders()] == [(mine.resolve(), True)]
        assert ids(lib) == {"curated:weekly-review": "off"}
    finally:
        mine.chmod(0o755)


def test_removing_a_folder_forgets_its_skills(lib: Library, tmp_path: Path) -> None:
    a = make(tmp_path / "mine", "alpha")
    lib.add_folder(str(tmp_path / "mine"))
    lib.turn_on(f"folder:{a.resolve()}")
    lib.remove_folder(str(tmp_path / "mine"))
    saved = json.loads((tmp_path / "home" / "skills.json").read_text())
    assert saved == {"folders": [], "enabled": {}}


def test_lookup_by_name_or_id_and_duplicate_names(lib: Library, tmp_path: Path) -> None:
    dup = make(tmp_path / "mine", "weekly-review")
    lib.add_folder(str(tmp_path / "mine"))
    lib.turn_on("curated:weekly-review")
    assert lib.lookup("weekly-review").id == "curated:weekly-review"
    lib.turn_on(f"folder:{dup.resolve()}")
    with pytest.raises(LibraryError, match="more than one"):
        lib.lookup("weekly-review")
    assert lib.lookup(f"folder:{dup.resolve()}").source == "folder"
    labels = [label for label, _ in lib.prompt_lines()]
    mine = (tmp_path / "mine").resolve()  # resolved, as stored (macOS: /var is /private/var)
    assert labels == ["weekly-review", f"weekly-review (from {mine})"]
    with pytest.raises(LibraryError, match="not turned on"):
        lib.lookup("nothing-here")


def test_lookup_returns_a_changed_skill_for_the_caller_to_refuse(
    lib: Library, tmp_path: Path
) -> None:
    lib.turn_on("curated:weekly-review")
    (tmp_path / "curated" / "weekly-review" / "extra.md").write_text("new", encoding="utf-8")
    assert lib.lookup("weekly-review").state == "changed"


def test_a_damaged_settings_file_is_never_overwritten(lib: Library, tmp_path: Path) -> None:
    path = tmp_path / "home" / "skills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert ids(lib) == {"curated:weekly-review": "off"}
    with pytest.raises(LibraryError, match=r"skills\.json"):
        lib.turn_on("curated:weekly-review")
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_settings_file_of_the_wrong_shape_is_read_as_empty(lib: Library, tmp_path: Path) -> None:
    path = tmp_path / "home" / "skills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"folders": "x", "enabled": ["a"]}', encoding="utf-8")
    assert ids(lib) == {"curated:weekly-review": "off"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_settings_file_is_private(lib: Library, tmp_path: Path) -> None:
    lib.turn_on("curated:weekly-review")
    assert (tmp_path / "home" / "skills.json").stat().st_mode & 0o777 == 0o600
