"""A folder named as the file to write is refused as a folder, never as a file that
already exists: that reason led her to say the folder was read only."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.links import symlink_or_skip
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
    assert "already exists" not in pre.reason


async def test_create_suggests_a_file_name(tmp_path: Path) -> None:
    folder = tmp_path / "Desktop"
    folder.mkdir()
    pre = await FileCreate().precheck({"path": str(folder), "content": "hi"}, _ctx(tmp_path))
    assert "Desktop/note.txt" in pre.reason


@pytest.mark.parametrize("tool", [FileOverwrite(), FileAppend(), FileEdit()])
async def test_a_change_names_no_new_file(tool: BaseTool, tmp_path: Path) -> None:
    """Changing a file needs the one that is there, never a made-up note.txt."""
    folder = tmp_path / "Desktop"
    folder.mkdir()
    args = {"path": str(folder), "content": "hi", "old": "a", "new": "b"}
    pre = await tool.precheck(args, _ctx(tmp_path))
    assert "name the file inside it" in pre.reason and "note.txt" not in pre.reason


@pytest.mark.parametrize("tool", [FileCreate(), FileOverwrite(), FileAppend(), FileEdit()])
async def test_a_link_to_a_folder_is_a_folder(tool: BaseTool, tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "Desktop"
    symlink_or_skip(link, real)
    args = {"path": str(link), "content": "hi", "old": "a", "new": "b"}
    pre = await tool.precheck(args, _ctx(tmp_path))
    assert not pre.ok and "is a folder" in pre.reason


@pytest.mark.parametrize("tool", [FileCreate(), FileOverwrite(), FileAppend(), FileEdit()])
def test_each_write_says_to_name_a_file(tool: BaseTool) -> None:
    said = tool.definition.description
    assert "file inside" in said and "not a folder" in said
