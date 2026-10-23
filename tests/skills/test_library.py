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


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
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


posix_not_root = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root"
)


@posix_not_root
def test_a_folder_behind_one_that_cannot_be_opened_is_not_found(
    lib: Library, tmp_path: Path
) -> None:
    make(tmp_path / "p" / "q", "alpha")
    lib.add_folder(str(tmp_path / "p" / "q"))
    lib.turn_on("curated:weekly-review")
    (tmp_path / "p").chmod(0)
    try:
        assert [f.found for f in lib.folders()] == [False]
        assert [e.id for e in lib.enabled()] == ["curated:weekly-review"]
        assert ids(lib) == {"curated:weekly-review": "on"}
        with pytest.raises(LibraryError, match="not a folder"):
            lib.add_folder(str(tmp_path / "p" / "q"))
    finally:
        (tmp_path / "p").chmod(0o755)


def test_a_name_shared_with_a_changed_skill_still_finds_the_one_that_is_on(
    lib: Library, tmp_path: Path
) -> None:
    dup = make(tmp_path / "mine", "weekly-review")
    lib.add_folder(str(tmp_path / "mine"))
    lib.turn_on("curated:weekly-review")
    lib.turn_on(f"folder:{dup.resolve()}")
    (dup / "extra.md").write_text("new", encoding="utf-8")
    assert [label for label, _ in lib.prompt_lines()] == ["weekly-review"]
    assert lib.lookup("weekly-review").id == "curated:weekly-review"
    (tmp_path / "curated" / "weekly-review" / "extra.md").write_text("new", encoding="utf-8")
    with pytest.raises(LibraryError, match="more than one"):
        lib.lookup("weekly-review")  # both changed: say which, by id


def test_a_lone_changed_skill_is_found_for_the_caller_to_refuse(
    lib: Library, tmp_path: Path
) -> None:
    lib.turn_on("curated:weekly-review")
    (tmp_path / "curated" / "weekly-review" / "extra.md").write_text("new", encoding="utf-8")
    assert lib.lookup("curated:weekly-review").state == "changed"


def _case_insensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "probe"
    probe.mkdir(exist_ok=True)
    return (tmp_path / "PROBE").exists()


def test_trillions_folder_is_refused_in_any_case(lib: Library, tmp_path: Path) -> None:
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a file system that ignores case")
    (tmp_path / "home").mkdir(exist_ok=True)
    with pytest.raises(LibraryError, match="Trillion's own"):
        lib.add_folder(str(tmp_path / "HOME"))
    swapped = tmp_path.parent / tmp_path.name.swapcase()
    with pytest.raises(LibraryError, match="Trillion's own"):
        lib.add_folder(str(swapped))


def test_the_same_folder_in_another_case_is_added_once(lib: Library, tmp_path: Path) -> None:
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a file system that ignores case")
    make(tmp_path / "mine", "alpha")
    lib.add_folder(str(tmp_path / "mine"))
    lib.add_folder(str(tmp_path / "MINE"))
    assert len(lib.folders()) == 1
    lib.remove_folder(str(tmp_path / "MINE"))
    assert lib.folders() == []


def test_removing_a_folder_keeps_one_added_inside_it(lib: Library, tmp_path: Path) -> None:
    a = make(tmp_path / "mine", "alpha")
    lib.add_folder(str(tmp_path / "mine"))
    lib.add_folder(str(a))
    lib.turn_on(f"folder:{a.resolve()}")
    lib.remove_folder(str(tmp_path / "mine"))
    assert ids(lib)[f"folder:{a.resolve()}"] == "on"


def test_files_changing_while_read_never_count_as_on(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib.turn_on("curated:weekly-review")
    calls = iter(range(1000))
    monkeypatch.setattr(library, "fingerprint", lambda folder: f"sha256:{next(calls)}")
    (entry,) = lib.scan()
    assert entry.state == "changed"
    lib.turn_off("curated:weekly-review")
    (entry,) = lib.scan()
    assert entry.state == "invalid" and "being changed" in entry.reason
    with pytest.raises(LibraryError, match="being changed"):
        lib.turn_on("curated:weekly-review")


def test_turning_on_what_the_page_showed(lib: Library, tmp_path: Path) -> None:
    (shown,) = lib.scan()
    (tmp_path / "curated" / "weekly-review" / "extra.md").write_text("new", encoding="utf-8")
    with pytest.raises(LibraryError, match="changed since this page showed it"):
        lib.turn_on(shown.id, expected=shown.fingerprint)
    (now,) = lib.scan()
    assert lib.turn_on(now.id, expected=now.fingerprint).state == "on"


def test_an_empty_fingerprint_never_counts_as_on(
    lib: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "home" / "skills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"folders": [], "enabled": {"curated:weekly-review": ""}}', encoding="utf-8")
    monkeypatch.setattr(library, "fingerprint", lambda folder: "")
    assert ids(lib) == {"curated:weekly-review": "changed"}


def test_the_settings_file_is_synced_and_read_back(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(library.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    lib.turn_on("curated:weekly-review")
    assert synced
    real_replace = os.replace

    def replace_badly(src: str, dst: Path) -> None:
        real_replace(src, dst)
        Path(dst).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(library.os, "replace", replace_badly)
    with pytest.raises(LibraryError, match="did not save"):
        lib.turn_off("curated:weekly-review")


def test_a_turn_can_reuse_the_skills_it_already_read(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib.turn_on("curated:weekly-review")
    on = lib.enabled()
    monkeypatch.setattr(library, "parse", lambda folder: pytest.fail("read again"))
    assert lib.prompt_lines(on) == [("weekly-review", "Does a thing.")]
    assert lib.fact(on).startswith("1 turned on: weekly-review")


def test_labels_tell_apart_names_shared_in_one_folder_and_shorten_home(
    lib: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path.resolve()))
    monkeypatch.setenv("USERPROFILE", str(tmp_path.resolve()))
    mine = tmp_path / "mine"
    for sub in ["a", "b"]:
        folder = mine / sub
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("---\nname: pdf\ndescription: d\n---\nx", "utf-8")
    lib.add_folder(str(mine))
    for e in lib.scan():
        if e.source == "folder":
            lib.turn_on(e.id)
    labels = [label for label, _ in lib.prompt_lines()]
    sep = os.sep
    assert labels == [f"pdf (from ~{sep}mine{sep}a)", f"pdf (from ~{sep}mine{sep}b)"]


@pytest.mark.parametrize("raw", ["a\x00b"])
def test_a_path_that_cannot_be_a_path_is_refused(lib: Library, raw: str) -> None:
    with pytest.raises(LibraryError):
        lib.add_folder(raw)
    with pytest.raises(LibraryError):
        lib.remove_folder(raw)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_link_to_a_folder_is_stored_as_where_it_points(lib: Library, tmp_path: Path) -> None:
    a = make(tmp_path / "mine", "alpha")  # as ~/.claude/skills often is, from dotfiles
    (tmp_path / "link").symlink_to(tmp_path / "mine", target_is_directory=True)
    assert lib.add_folder(str(tmp_path / "link")) == (tmp_path / "mine").resolve()
    assert [(f.path, f.found) for f in lib.folders()] == [((tmp_path / "mine").resolve(), True)]
    assert f"folder:{a.resolve()}" in ids(lib)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_stored_folder_later_swapped_for_a_link_is_not_found(
    lib: Library, tmp_path: Path
) -> None:
    make(tmp_path / "mine", "alpha")
    make(tmp_path / "other", "beta")
    lib.add_folder(str(tmp_path / "mine"))
    (tmp_path / "mine").rename(tmp_path / "old")
    (tmp_path / "mine").symlink_to(tmp_path / "other", target_is_directory=True)
    assert [f.found for f in lib.folders()] == [False]
    assert ids(lib) == {"curated:weekly-review": "off"}


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_ids_do_not_follow_a_subfolder_swapped_for_a_link(lib: Library, tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    make(mine, "alpha")
    lib.add_folder(str(mine))
    (entry,) = [e for e in lib.scan() if e.source == "folder"]
    assert entry.id == f"folder:{mine.resolve() / 'alpha'}"


def test_an_invalid_skill_is_never_hashed(
    lib: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "mine" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("no header", encoding="utf-8")
    (bad / "data.bin").write_bytes(b"x" * 1024)
    lib.add_folder(str(tmp_path / "mine"))
    hashed: list[str] = []
    real = library.fingerprint
    monkeypatch.setattr(library, "fingerprint", lambda f: (hashed.append(f.name), real(f))[1])
    lib.scan()
    assert "bad" not in hashed


def test_removing_a_folder_inside_a_kept_one_drops_its_skills(lib: Library, tmp_path: Path) -> None:
    make(tmp_path / "mine", "top")
    sk = make(tmp_path / "mine" / "b", "sk")
    lib.add_folder(str(tmp_path / "mine"))
    lib.add_folder(str(tmp_path / "mine" / "b"))
    lib.turn_on(f"folder:{sk.resolve()}")
    lib.remove_folder(str(tmp_path / "mine" / "b"))
    saved = json.loads((tmp_path / "home" / "skills.json").read_text())
    assert saved["enabled"] == {}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_settings_folder_is_private(lib: Library, tmp_path: Path) -> None:
    lib.turn_on("curated:weekly-review")
    assert (tmp_path / "home").stat().st_mode & 0o777 == 0o700
