"""Reading one skill folder: its header, its instructions, its files and its limits."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from trillion.skills.parse import MAX_FILES, MAX_SKILL_MD, Invalid, Skill, parse

GOOD = "---\nname: pdf-forms\ndescription: Fill in PDF forms.\n---\n# PDF forms\nStep one.\n"


def skill(tmp_path: Path, text: str = GOOD, **files: str) -> Path:
    folder = tmp_path / "pdf-forms"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    for name, body in files.items():
        target = folder / name.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return folder


def test_a_good_skill_has_its_header_instructions_and_files(tmp_path: Path) -> None:
    found = parse(skill(tmp_path, **{"reference__forms.md": "Field list"}))
    assert isinstance(found, Skill)
    assert found.name == "pdf-forms" and found.description == "Fill in PDF forms."
    assert found.instructions == "# PDF forms\nStep one."
    assert [f.path for f in found.files] == ["reference/forms.md"]
    assert not found.has_scripts


def test_scripts_are_noticed_by_suffix(tmp_path: Path) -> None:
    found = parse(skill(tmp_path, **{"scripts__fill.py": "print(1)", "notes.md": "x"}))
    assert isinstance(found, Skill) and found.has_scripts
    assert {f.path for f in found.files if f.script} == {"scripts/fill.py"}


@pytest.mark.skipif(sys.platform == "win32", reason="no executable bit on Windows")
def test_scripts_are_noticed_by_the_executable_bit(tmp_path: Path) -> None:
    folder = skill(tmp_path, tools__run="#!/bin/sh\necho hi\n")
    os.chmod(folder / "tools" / "run", 0o755)  # noqa: S103 - the test needs the executable bit
    found = parse(folder)
    assert isinstance(found, Skill)
    assert {f.path for f in found.files if f.script} == {"tools/run"}


def test_allowed_tools_may_be_a_string_or_a_list(tmp_path: Path) -> None:
    text = "---\nname: a\ndescription: d\nallowed-tools: Read, web_fetch Bash\n---\nx"
    found = parse(skill(tmp_path, text))
    assert isinstance(found, Skill) and found.allowed_tools == ("Read", "web_fetch", "Bash")
    text = "---\nname: a\ndescription: d\nallowed-tools: [Read, web_fetch]\nlicense: MIT\n---\nx"
    found = parse(skill(tmp_path / "b", text))
    assert isinstance(found, Skill)
    assert found.allowed_tools == ("Read", "web_fetch") and found.license == "MIT"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("# no header\n", "no header"),
        ("---\n- a list\n---\nx", "header is not a set of fields"),
        ("---\nname: [unclosed\n---\nx", "header could not be read"),
        ("---\ndescription: d\n---\nx", "no name"),
        ("---\nname: a\n---\nx", "no description"),
        ("---\nname: Bad_Name\ndescription: d\n---\nx", "name"),
        ("---\nname: -a\ndescription: d\n---\nx", "name"),
        ("---\nname: a--b\ndescription: d\n---\nx", "name"),
        ("---\nname: " + "a" * 65 + "\ndescription: d\n---\nx", "name"),
        ("---\nname: a\ndescription: " + "d" * 1025 + "\n---\nx", "description"),
    ],
)
def test_a_broken_header_says_why(tmp_path: Path, text: str, reason: str) -> None:
    found = parse(skill(tmp_path, text))
    assert isinstance(found, Invalid) and reason in found.reason


def test_a_folder_without_skill_md_is_invalid(tmp_path: Path) -> None:
    assert isinstance(parse(tmp_path), Invalid)


def test_limits_make_a_skill_invalid_not_half_loaded(tmp_path: Path) -> None:
    big = GOOD + "x" * MAX_SKILL_MD
    assert "too large" in getattr(parse(skill(tmp_path, big)), "reason", "")
    many = {f"f{i}": "" for i in range(MAX_FILES + 1)}
    assert "too many files" in getattr(parse(skill(tmp_path / "b", **many)), "reason", "")
    deep = skill(tmp_path / "c")
    nested = deep.joinpath(*[f"d{i}" for i in range(9)])
    nested.mkdir(parents=True)
    (nested / "x.md").write_text("x", encoding="utf-8")
    assert "too deep" in getattr(parse(deep), "reason", "")


def test_editor_and_system_clutter_is_ignored(tmp_path: Path) -> None:
    folder = skill(tmp_path)
    (folder / ".DS_Store").write_bytes(b"\0\1")
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x.pyc").write_bytes(b"\0")
    found = parse(folder)
    assert isinstance(found, Skill) and found.files == ()


def test_a_binary_file_is_listed_but_not_text(tmp_path: Path) -> None:
    folder = skill(tmp_path)
    (folder / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    found = parse(folder)
    assert isinstance(found, Skill)
    (logo,) = found.files
    assert logo.path == "logo.png" and not logo.text
