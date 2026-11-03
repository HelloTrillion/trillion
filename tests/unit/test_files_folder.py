"""A folder named as the file to write is refused as a folder, never as a file that
already exists: that reason led her to say the folder was read only."""

from __future__ import annotations

from pathlib import Path

import pytest

from trillion.spine.contract import BaseTool, ToolContext
from trillion.tools.files import FileAppend, FileCreate, FileEdit, FileOverwrite


def _ctx(root: Path) -> ToolContext:
    return ToolContext(
        owner_id="local", session_id="s1", turn=1, call_id="c1", via="text", granted_roots=(root,)
    )


@pytest.mark.parametrize("tool", [FileCreate(), FileOverwrite(), FileAppend(), FileEdit()])
async def test_a_folder_is_refused_as_a_folder(tool: BaseTool, tmp_path: Path) -> None:
    folder = tmp_path / "Desktop"
    folder.mkdir()
    args = {"path": str(folder), "content": "hi", "old": "a", "new": "b"}
    pre = await tool.precheck(args, _ctx(tmp_path))
    assert not pre.ok
    assert "is a folder" in pre.reason
    assert "name a file inside it" in pre.reason
    assert "Desktop/note.txt" in pre.reason
    assert "already exists" not in pre.reason


def test_the_create_description_says_to_name_a_file() -> None:
    said = FileCreate().definition.description
    assert "file inside" in said and "not a folder" in said
