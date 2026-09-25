from trillion.spine.contract import Risk
from trillion.tools.files import (
    FileAppend,
    FileCreate,
    FileList,
    FileOverwrite,
    FileRead,
    human_size,
)


def test_risk_levels_every_write_asks():
    assert FileList().definition.risk is Risk.READ
    assert FileRead().definition.risk is Risk.READ
    for t in (FileCreate(), FileOverwrite(), FileAppend()):
        assert t.definition.risk is Risk.IRREVERSIBLE
    assert FileOverwrite().definition.recoverable is False
    assert FileCreate().definition.recoverable is True


def test_human_size():
    assert human_size(12) == "12 bytes"
    assert human_size(2150) == "2.1 KB"
    assert human_size(3_400_000) == "3.2 MB"


def test_create_summary_is_host_written(tmp_path):
    s = FileCreate().describe({"path": str(tmp_path / "note.txt"), "content": "hi there!!!!"})
    assert s.text == f"Create note.txt in {tmp_path.name} (12 bytes)"


def test_overwrite_summary_states_what_it_replaces(tmp_path):
    f = tmp_path / "report.md"
    f.write_text("x" * 2150)
    s = FileOverwrite().describe({"path": str(f), "content": "y" * 3500})
    assert s.text == f"Replace report.md in {tmp_path.name} (2.1 KB → 3.4 KB)"
