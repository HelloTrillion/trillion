"""Reading one skill folder: its header, its instructions, its files and its limits."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from trillion.skills.parse import MAX_DEPTH, MAX_FILES, MAX_SKILL_MD, Invalid, Skill, parse

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
    text = "---\nname: a\ndescription: d\nallowed-tools: Read web_fetch  Bash\n---\nx"
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
        ("---\nname: [a, b]\ndescription: d\n---\nx", "its name must be text"),
        ("---\nname: a\ndescription: {x: 1}\n---\nx", "its description must be text"),
        ("---\nname: a\ndescription: d\nallowed-tools: {x: 1}\n---\nx", "allowed-tools"),
        ("---\nname: a\ndescription: d\nallowed-tools: [Read, [x]]\n---\nx", "allowed-tools"),
        ("---\nname: &n a\ndescription: *n\n---\nx", "YAML references"),
        ("---\nname: a\ndescription: d\n----\nx", "no header"),
        ("---\n---\n# x", "no name"),
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


def test_eight_levels_of_folders_are_allowed_and_nine_are_not(tmp_path: Path) -> None:
    eight = skill(tmp_path / "a")
    nested = eight.joinpath(*[f"d{i}" for i in range(8)])
    nested.mkdir(parents=True)
    (nested / "x.md").write_text("x", encoding="utf-8")
    assert isinstance(parse(eight), Skill)
    (nested / "d8").mkdir()
    (nested / "d8" / "x.md").write_text("x", encoding="utf-8")
    found = parse(eight)
    assert isinstance(found, Invalid) and "too deep (over 8 levels)" in found.reason


def test_an_empty_folder_nine_levels_down_is_too_deep(tmp_path: Path) -> None:
    folder = skill(tmp_path)
    folder.joinpath(*[f"d{i}" for i in range(9)]).mkdir(parents=True)
    found = parse(folder)
    assert isinstance(found, Invalid) and "too deep" in found.reason


def test_a_very_deep_folder_is_not_walked_to_the_bottom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = skill(tmp_path)
    folder.joinpath(*[f"d{i}" for i in range(40)]).mkdir(parents=True)
    seen: list[str] = []
    real = os.walk

    def spy(top: Path, **kw: object) -> object:
        for root, dirs, files in real(top, **kw):  # type: ignore[call-overload]
            seen.append(root)
            yield root, dirs, files

    monkeypatch.setattr(os, "walk", spy)
    found = parse(folder)
    assert isinstance(found, Invalid) and "too deep" in found.reason
    assert len(seen) <= MAX_DEPTH + 1


def test_a_huge_folder_stops_being_walked_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = skill(tmp_path)
    for i in range(30):
        sub = folder / f"pkg{i:02}"
        sub.mkdir()
        for j in range(20):
            (sub / f"f{j}.md").write_text("", encoding="utf-8")
    seen: list[str] = []
    real = os.walk

    def spy(top: Path, **kw: object) -> object:
        for root, dirs, files in real(top, **kw):  # type: ignore[call-overload]
            seen.append(root)
            yield root, dirs, files

    monkeypatch.setattr(os, "walk", spy)
    found = parse(folder)
    assert isinstance(found, Invalid) and "too many files" in found.reason
    assert len(seen) < 15  # 200 files fill up after about ten folders of twenty


def test_too_many_folders_is_too_many_even_when_empty(tmp_path: Path) -> None:
    folder = skill(tmp_path)
    for i in range(MAX_FILES + 1):
        (folder / f"d{i}").mkdir()
    found = parse(folder)
    assert isinstance(found, Invalid) and "too many folders" in found.reason


def test_a_license_that_is_not_text_is_ignored(tmp_path: Path) -> None:
    found = parse(skill(tmp_path, "---\nname: a\ndescription: d\nlicense: 3\n---\nx"))
    assert isinstance(found, Skill) and found.license == ""


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


def test_allowed_tools_with_commas_split_only_on_commas(tmp_path: Path) -> None:
    text = "---\nname: a\ndescription: d\nallowed-tools: Bash(git add:*), Read\n---\nx"
    found = parse(skill(tmp_path, text))
    assert isinstance(found, Skill) and found.allowed_tools == ("Bash(git add:*)", "Read")


def test_the_closing_line_may_have_trailing_spaces(tmp_path: Path) -> None:
    found = parse(skill(tmp_path, "---\nname: a\ndescription: d\n--- \t\nbody"))
    assert isinstance(found, Skill) and found.instructions == "body"


def finishes(work: Callable[[], object], fifo: Path | None = None, seconds: float = 5) -> bool:
    """Run `work` in a thread; False if it is still running after `seconds`. A reader
    stuck on `fifo` is let go by opening and closing its other end."""
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(seconds)
    stuck = thread.is_alive()
    if stuck and fifo is not None:
        try:
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass
    return not stuck


def test_a_yaml_alias_bomb_is_refused_quickly(tmp_path: Path) -> None:
    lines = ['a0: &a0 ["lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol"]']
    for i in range(1, 9):
        lines.append(f"a{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 9) + "]")
    text = "---\n" + "\n".join(lines) + "\nname: *a8\ndescription: *a8\n---\nx"
    assert len(text) < 1024
    folder = skill(tmp_path, text)
    result: list[object] = []
    started = time.monotonic()
    assert finishes(lambda: result.append(parse(folder)))
    assert time.monotonic() - started < 2
    (found,) = result
    assert isinstance(found, Invalid) and "YAML references" in found.reason


@pytest.mark.skipif(sys.platform == "win32", reason="no named pipes in the file system")
def test_a_named_pipe_makes_it_invalid_without_hanging(tmp_path: Path) -> None:
    folder = skill(tmp_path)
    os.mkfifo(folder / "pipe")
    result: list[object] = []
    assert finishes(lambda: result.append(parse(folder)), folder / "pipe")
    (found,) = result
    assert isinstance(found, Invalid) and found.reason == "pipe is not a regular file"


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux allows non-UTF-8 names")
def test_a_file_name_that_is_not_text_makes_it_invalid(tmp_path: Path) -> None:
    folder = skill(tmp_path)
    (folder / os.fsdecode(b"bad\xff.md")).write_bytes(b"x")
    found = parse(folder)
    assert isinstance(found, Invalid) and found.reason == "a file name is not valid text"


def test_a_realistic_claude_style_skill(tmp_path: Path) -> None:
    text = (
        "---\n"
        "name: pdf-forms\n"
        "description: >\n"
        "  Fill in PDF forms and check the result.\n"
        "  Use when the user hands over a PDF with fields.\n"
        "allowed-tools: Bash(python:*), Read, Write\n"
        "license: Apache-2.0\n"
        "---\n"
        "# PDF forms\n\nRead reference/fields.md first.\n"
    )
    folder = skill(
        tmp_path,
        text,
        **{
            "reference__fields.md": "# Fields",
            "reference__checks.md": "# Checks",
            "scripts__fill.py": "print('fill')",
            "scripts__check.py": "print('check')",
        },
    )
    (folder / "assets").mkdir()
    (folder / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    found = parse(folder)
    assert isinstance(found, Skill)
    assert found.name == "pdf-forms"
    assert found.description == (
        "Fill in PDF forms and check the result. Use when the user hands over a PDF with fields."
    )
    assert found.allowed_tools == ("Bash(python:*)", "Read", "Write")
    assert found.license == "Apache-2.0"
    assert found.instructions == "# PDF forms\n\nRead reference/fields.md first."
    assert [f.path for f in found.files] == [
        "assets/logo.png",
        "reference/checks.md",
        "reference/fields.md",
        "scripts/check.py",
        "scripts/fill.py",
    ]
    assert {f.path for f in found.files if f.script} == {"scripts/check.py", "scripts/fill.py"}
    assert {f.path for f in found.files if not f.text} == {"assets/logo.png"}


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_skill_md_is_invalid(tmp_path: Path) -> None:
    (tmp_path / "real.md").write_text(GOOD, encoding="utf-8")
    folder = tmp_path / "s"
    folder.mkdir()
    os.symlink(tmp_path / "real.md", folder / "SKILL.md")
    found = parse(folder)
    assert isinstance(found, Invalid) and "not a plain file" in found.reason


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_folder_is_listed_as_a_link_and_not_entered(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    folder = skill(tmp_path / "x")
    os.symlink(outside, folder / "more", target_is_directory=True)
    found = parse(folder)
    assert isinstance(found, Skill)
    (more,) = found.files
    assert more.path == "more" and not more.text and not more.script


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_a_junction_is_listed_as_a_link_and_not_entered(tmp_path: Path) -> None:
    winapi = pytest.importorskip("_winapi")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    folder = skill(tmp_path / "x")
    winapi.CreateJunction(str(outside), str(folder / "more"))
    found = parse(folder)
    assert isinstance(found, Skill)
    (more,) = found.files
    assert more.path == "more" and not more.text and not more.script


def test_the_instructions_file_must_be_named_in_capitals(tmp_path: Path) -> None:
    folder = tmp_path / "pdf-forms"
    folder.mkdir()
    (folder / "skill.md").write_text(GOOD, encoding="utf-8")
    if not (folder / "SKILL.md").exists():
        pytest.skip("the disk tells names apart by case: SKILL.md is simply missing")
    found = parse(folder)
    assert isinstance(found, Invalid) and "in capitals" in found.reason
