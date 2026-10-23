"""skill_open and skill_file: only skills that are on and unchanged, only inside them."""

from __future__ import annotations

import asyncio
import os
import sys
from io import BufferedReader
from pathlib import Path

import pytest

import trillion.tools.skills as skills_tools
from trillion.skills.library import Library
from trillion.spine.contract import Risk, ToolContext
from trillion.spine.registry import validate
from trillion.tools.skills import SkillFile, SkillOpen

CTX = ToolContext(owner_id="o", session_id="s", turn=1, call_id="c", via="text")
Setup = tuple[Library, Path, SkillOpen, SkillFile]


def _skill(folder: Path, name: str = "pdf-forms") -> None:
    (folder / "reference").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Fill PDF forms.\n---\nRead reference/fields.md.\n",
        encoding="utf-8",
    )
    (folder / "reference" / "fields.md").write_text("Name, date.", encoding="utf-8")


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    curated = tmp_path / "curated"
    folder = curated / "pdf-forms"
    _skill(folder)
    (folder / "fill.py").write_text("print('hi')", encoding="utf-8")
    (folder / "logo.png").write_bytes(b"\x89PNG\xff\xfe")
    lib = Library(tmp_path / "home" / "skills.json", curated, data_root=tmp_path / "home")
    opener, reader = SkillOpen(), SkillFile()
    opener.bind(lib)
    reader.bind(lib)
    return lib, folder, opener, reader


def run(tool: SkillOpen | SkillFile, **args: str) -> tuple[bool, str]:
    r = asyncio.run(tool.execute(args, CTX))
    return r.ok, r.content


def test_both_are_first_party_reads_with_trusted_output() -> None:
    for tool in (SkillOpen(), SkillFile()):
        validate(tool, "trillion.tools.skills")
        assert tool.definition.risk is Risk.READ and tool.definition.trusted_output


def test_unbound_tools_refuse() -> None:
    ok, said = run(SkillOpen(), skill="pdf-forms")
    assert not ok and "not available" in said


def test_a_skill_that_is_off_is_never_opened(setup: Setup) -> None:
    _, _, opener, reader = setup
    ok, said = run(opener, skill="pdf-forms")
    assert not ok and "turned on" in said
    assert not run(reader, skill="pdf-forms", path="reference/fields.md")[0]


def test_open_returns_instructions_and_files(setup: Setup) -> None:
    lib, _, opener, _ = setup
    lib.turn_on("curated:pdf-forms")
    ok, said = run(opener, skill="pdf-forms")
    assert ok and "Read reference/fields.md." in said
    assert "reference/fields.md" in said and "fill.py" in said and "never runs" in said
    assert "logo.png" not in said  # not text: not offered
    assert "description:" not in said  # the header is not repeated
    assert run(opener, skill="curated:pdf-forms")[0]  # by id too


def test_a_changed_skill_is_refused(setup: Setup) -> None:
    lib, folder, opener, reader = setup
    lib.turn_on("curated:pdf-forms")
    (folder / "SKILL.md").write_text(
        "---\nname: pdf-forms\ndescription: x\n---\nDo something else.\n", encoding="utf-8"
    )
    ok, said = run(opener, skill="pdf-forms")
    assert not ok and "changed" in said and "Settings" in said
    assert not run(reader, skill="pdf-forms", path="reference/fields.md")[0]


def test_an_invalid_skill_is_refused(setup: Setup) -> None:
    lib, folder, opener, reader = setup
    lib.turn_on("curated:pdf-forms")
    (folder / "SKILL.md").write_text("---\nname: pdf-forms\n---\nNo description.\n", "utf-8")
    ok, said = run(opener, skill="curated:pdf-forms")
    assert not ok and "can't be used" in said and "No description" not in said
    ok, said = run(reader, skill="curated:pdf-forms", path="reference/fields.md")
    assert not ok and "Name, date." not in said
    assert not run(opener, skill="pdf-forms")[0]


def test_a_name_two_skills_share_asks_for_the_id(tmp_path: Path) -> None:
    lib = Library(tmp_path / "home" / "skills.json", tmp_path / "none", data_root=tmp_path / "home")
    for side in ("a", "b"):
        _skill(tmp_path / side / "pdf-forms")
        lib.add_folder(str(tmp_path / side))
    for e in lib.scan():
        lib.turn_on(e.id)
    opener = SkillOpen()
    opener.bind(lib)
    ok, said = run(opener, skill="pdf-forms")
    assert not ok and "more than one" in said
    sid = f"folder:{(tmp_path / 'a').resolve() / 'pdf-forms'}"
    assert run(opener, skill=sid)[0]


def test_file_reads_inside_the_skill_only(setup: Setup) -> None:
    lib, _, _, reader = setup
    lib.turn_on("curated:pdf-forms")
    assert run(reader, skill="pdf-forms", path="reference/fields.md") == (True, "Name, date.")
    ok, said = run(reader, skill="pdf-forms", path="fill.py")
    assert ok and said.startswith("(A script. Trillion never runs it") and "print('hi')" in said
    for bad in ("../x", "/etc/passwd", "reference/../../x", "SKILL.md", "logo.png", "nope.md"):
        assert not run(reader, skill="pdf-forms", path=bad)[0], bad
    assert not run(reader, skill="pdf-forms", path="C:\\Windows\\win.ini")[0]


def test_a_file_over_the_limit_is_not_read_even_if_listed(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib, _, _, reader = setup
    lib.turn_on("curated:pdf-forms")
    monkeypatch.setattr(skills_tools, "MAX_FILE", 4)  # "Name, date." is 11 bytes
    ok, said = run(reader, skill="pdf-forms", path="reference/fields.md")
    assert not ok and "too large" in said and "Name" not in said


def test_a_change_while_reading_is_refused(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    lib, _, _, reader = setup
    lib.turn_on("curated:pdf-forms")
    real = skills_tools.open_regular

    def swapped(path: Path) -> BufferedReader:
        # Between the lookup and the read, the file the user approved is replaced.
        path.write_text("Unapproved.", encoding="utf-8")
        return real(path)

    monkeypatch.setattr(skills_tools, "open_regular", swapped)
    ok, said = run(reader, skill="pdf-forms", path="reference/fields.md")
    assert not ok and "changed while it was being read" in said and "Unapproved" not in said


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlink_is_refused(setup: Setup, tmp_path: Path) -> None:
    lib, folder, _, reader = setup
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
    os.symlink(tmp_path / "secret.md", folder / "link.md")
    lib.turn_on("curated:pdf-forms")
    ok, said = run(reader, skill="pdf-forms", path="link.md")
    assert not ok and "secret" not in said
