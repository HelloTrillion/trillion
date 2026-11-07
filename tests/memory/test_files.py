"""Memories as files the user can open and edit (MEM-1, MEM-6b, OPS-2)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trillion.memory import files
from trillion.memory.files import MemoryFileError
from trillion.memory.types import MAX_TEXT, SOURCES, STATUSES, TYPES, Memory

NOW = 1_791_600_000_123
COFFEE = Memory(
    id="mem_0123456789ab",
    type="user",
    text="I take my coffee black.",
    source="user_statement",
    moves=False,
    created_at=NOW,
    as_of=NOW,
)


def test_a_memory_file_is_plain_readable_text() -> None:
    assert files.render(COFFEE) == (
        "---\n"
        "id: mem_0123456789ab\n"
        "type: user\n"
        "source: user_statement\n"
        "moves: false\n"
        "status: active\n"
        "version: 1\n"
        "created: 2026-10-10T02:40:00.123Z\n"
        "as_of: 2026-10-10T02:40:00.123Z\n"
        "---\n"
        "\n"
        "I take my coffee black.\n"
    )


# Characters `str.splitlines` treats as line ends; a memory is cleaned of these on the way in.
_LINE_BREAKS = "".join(map(chr, (0x85, 0x2028, 0x2029)))
_text = (
    st.text(
        alphabet=st.characters(
            blacklist_categories=("Cs", "Cc"), blacklist_characters=_LINE_BREAKS
        ),
        min_size=1,
        max_size=300,
    )
    .map(files.clean)
    .filter(bool)
)


def test_cleaning_twice_changes_nothing() -> None:
    # A zero-width space after a real one: trimming first left the real space behind.
    for raw in ("0 \u200b", "a \u00a0\nb", " \u200b x \u200b "):
        once = files.clean(raw)
        assert files.clean(once) == once, repr(raw)
        assert not once.endswith(" ") and "\u200b" not in once


@given(
    kind=st.sampled_from(TYPES),
    source=st.sampled_from(SOURCES),
    status=st.sampled_from(STATUSES),
    moves=st.booleans(),
    text=_text,
    version=st.integers(1, 99),
    created=st.integers(0, 4_000_000_000_000),
    link=st.none() | st.just("mem_ffffffffffff"),
)
def test_every_memory_survives_being_written_and_read(
    kind: str, source: str, status: str, moves: bool, text: str, version: int, created: int,
    link: str | None,
) -> None:  # fmt: skip
    m = Memory(
        id="mem_0123456789ab", type=kind, source=source, status=status, moves=moves,  # type: ignore[arg-type]
        text=text, version=version, created_at=created, as_of=created, supersedes=link,
        superseded_by=link,
    )  # fmt: skip
    assert files.parse(files.render(m)) == m


def test_text_cannot_break_out_of_its_own_file() -> None:
    hostile = "fine\n---\nstatus: retracted\nid: mem_aaaaaaaaaaaa\n---\nmore"
    m = replace(COFFEE, text=files.clean(hostile))
    back = files.parse(files.render(m))
    assert back.id == COFFEE.id and back.status == "active" and back.text == m.text


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("just some notes", "no header"),
        ("---\nid: mem_0123456789ab\n", "not closed"),
        ("---\ntype: user\n---\nx", "missing"),
        (files.render(COFFEE).replace("mem_0123456789ab", "../../etc/passwd"), "bad id"),
        (files.render(COFFEE).replace("type: user", "type: secret"), "unknown"),
        (files.render(COFFEE).replace("moves: false", "moves: maybe"), "missing or bad"),
        (files.render(COFFEE).replace("version: 1", "version: one"), "malformed"),
        (files.render(COFFEE).replace("2026-10-10T02:40:00.123Z", "yesterday"), "malformed"),
        (files.render(COFFEE).replace("I take my coffee black.", ""), "empty"),
        (files.render(COFFEE).replace("I take my coffee black.", "x" * (MAX_TEXT + 1)), "too long"),
        (files.render(replace(COFFEE, supersedes="mem_0123456789ab")), "bad link"),
    ],
)
def test_a_file_that_is_not_a_memory_is_refused_with_a_reason(raw: str, why: str) -> None:
    with pytest.raises(MemoryFileError, match=why):
        files.parse(raw)


def test_writing_is_whole_confirmed_and_leaves_no_scraps(tmp_path: Path) -> None:
    digest = files.write(tmp_path, COFFEE)
    target = tmp_path / "user" / "mem_0123456789ab.md"
    assert target.read_text(encoding="utf-8") == files.render(COFFEE)
    assert digest == files.digest(files.render(COFFEE))
    files.write(tmp_path, replace(COFFEE, text="I take my coffee with milk.", version=2))
    assert "milk" in target.read_text(encoding="utf-8")
    assert [p.name for p in (tmp_path / "user").iterdir()] == ["mem_0123456789ab.md"]
    assert files.new_id() != files.new_id() and files.valid_id(files.new_id())


def test_scanning_reports_what_it_cannot_read_and_deletes_nothing(tmp_path: Path) -> None:
    files.write(tmp_path, COFFEE)
    project = replace(COFFEE, id="mem_bbbbbbbbbbbb", type="project", text="The launch is in May.")
    files.write(tmp_path, project)
    (tmp_path / "user" / "notes.md").write_text("my own notes", encoding="utf-8")
    (tmp_path / "user" / "mem_cccccccccccc.md").write_text(files.render(COFFEE), encoding="utf-8")
    (tmp_path / "user" / "huge.md").write_text("x" * 20_000, encoding="utf-8")
    (tmp_path / "user" / "ignored.txt").write_text("not markdown", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()

    scan = files.scan(tmp_path)
    assert {f.memory.id for f in scan.found} == {COFFEE.id, project.id} and scan.complete
    # A copy under another name, a stray note and an oversized file are named, and left alone.
    assert sorted(scan.unreadable) == ["user/huge.md", "user/mem_cccccccccccc.md", "user/notes.md"]
    assert len(list((tmp_path / "user").iterdir())) == 5
    assert files.scan(tmp_path / "nothing-here") == files.Scan(
        found=(), unreadable=(), complete=True
    )


def test_a_file_moved_to_another_folder_takes_that_folders_type(tmp_path: Path) -> None:
    files.write(tmp_path, COFFEE)
    (tmp_path / "feedback").mkdir()
    (tmp_path / "user" / f"{COFFEE.id}.md").rename(tmp_path / "feedback" / f"{COFFEE.id}.md")
    (found,) = files.scan(tmp_path).found
    assert found.memory.type == "feedback"


def test_an_imported_claim_keeps_its_origin_in_the_file() -> None:
    imported = replace(COFFEE, source="imported_claim", origin="openclaw memory/2026-10-01.md")
    raw = files.render(imported)
    assert "origin: openclaw memory/2026-10-01.md\n" in raw
    back = files.parse(raw)
    assert back.origin == "openclaw memory/2026-10-01.md"
    assert files.check(replace(imported, origin="two\nlines")) == "bad origin"
