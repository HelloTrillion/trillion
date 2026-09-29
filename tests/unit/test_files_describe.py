"""Approval cards say where, what and what changes (SPN-2a, issue #15).

The card is what the user consents to. "notes.md in Documents" could not tell two
granted folders called Documents apart, and no card showed the content at all.
"""

from pathlib import Path

from trillion.spine.contract import Risk
from trillion.spine.router import _safe_summary
from trillion.tools.files import (
    FileAppend,
    FileCreate,
    FileEdit,
    FileList,
    FileOverwrite,
    FileRead,
    human_size,
)


def test_risk_levels_every_write_asks():
    assert FileList().definition.risk is Risk.READ
    assert FileRead().definition.risk is Risk.READ
    for t in (FileCreate(), FileOverwrite(), FileAppend(), FileEdit()):
        assert t.definition.risk is Risk.IRREVERSIBLE
    assert FileOverwrite().definition.recoverable is False
    assert FileEdit().definition.recoverable is False
    assert FileCreate().definition.recoverable is True


def test_only_file_read_is_lossless():
    assert FileRead().definition.lossless_output is True
    for t in (FileList(), FileCreate(), FileOverwrite(), FileAppend(), FileEdit()):
        assert t.definition.lossless_output is False


def test_human_size():
    assert human_size(12) == "12 bytes"
    assert human_size(2150) == "2.1 KB"
    assert human_size(3_400_000) == "3.2 MB"


# ── Where ─────────────────────────────────────────────────────────────


def test_two_folders_with_the_same_name_give_different_cards(tmp_path):
    a, b = tmp_path / "work" / "Documents", tmp_path / "home" / "Documents"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    card_a = FileCreate().describe({"path": str(a / "notes.md"), "content": "x"}).text
    card_b = FileCreate().describe({"path": str(b / "notes.md"), "content": "x"}).text
    assert card_a != card_b
    assert "work/Documents/notes.md" in card_a.replace("\\", "/")


def test_a_path_under_home_is_shown_from_home():
    p = Path.home() / "Documents" / "notes.md"
    card = FileCreate().describe({"path": str(p), "content": "x"}).text
    assert "~/Documents/notes.md" in card


# ── What ──────────────────────────────────────────────────────────────


def test_create_shows_the_start_of_the_content(tmp_path):
    s = FileCreate().describe({"path": str(tmp_path / "note.txt"), "content": "hi there!!!!"})
    assert s.text.startswith("Create ")
    assert "(12 bytes)" in s.text
    assert "“hi there!!!!”" in s.text


def test_append_shows_what_is_added(tmp_path):
    f = tmp_path / "log.md"
    f.write_text("old\n")
    s = FileAppend().describe({"path": str(f), "content": "- bought milk\n"})
    assert "“- bought milk”" in s.text


def test_a_long_preview_is_cut_and_marked(tmp_path):
    s = FileCreate().describe({"path": str(tmp_path / "n.txt"), "content": "word " * 200})
    assert "…”" in s.text


def test_overwrite_says_how_much_changes_and_where(tmp_path):
    f = tmp_path / "report.md"
    f.write_text("\n".join(f"line {i}" for i in range(1, 81)) + "\n")
    new = f.read_text().replace("line 12\n", "line twelve\n").replace("line 13\n", "line 13!\n")
    s = FileOverwrite().describe({"path": str(f), "content": new})
    assert "2 of 80 lines change" in s.text
    assert "first at line 12" in s.text


def test_edit_says_where_and_what(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("a\nb\nfind me\nc\n")
    s = FileEdit().describe({"path": str(f), "old": "find me", "new": "found"})
    assert "line 3" in s.text
    assert "“find me” → “found”" in s.text


def test_every_card_survives_the_router_one_line_limit(tmp_path):
    """The router cuts a card to one line of 200 characters. The operation and the
    destination come first, so a long preview is what gets cut, never the path."""
    deep = tmp_path / ("x" * 30) / ("y" * 30)
    deep.mkdir(parents=True)
    raw = FileCreate().describe({"path": str(deep / "n.md"), "content": "z" * 5000}).text
    shown = _safe_summary(raw)
    assert shown.startswith("Create ") and "n.md" in shown
    assert len(shown) <= 200
