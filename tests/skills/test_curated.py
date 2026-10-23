"""Every curated skill is valid, small, and uses only tools Trillion has (CON-6)."""

from __future__ import annotations

import os
from pathlib import Path

from trillion.connectors.google.tools import TOOLS as GOOGLE
from trillion.skills.library import CURATED, Library
from trillion.skills.parse import SKILL_MD, Skill, parse
from trillion.spine.registry import ToolRegistry

NAMES = {"email-replies", "meeting-prep", "weekly-review"}


def test_the_curated_set_is_valid_and_uses_real_tools() -> None:
    google = {tool.name() for tools in GOOGLE.values() for tool in tools}
    assert {"gmail_search", "gmail_read", "gmail_draft", "gmail_send", "calendar_events"} <= google
    have = {d.name for d in ToolRegistry.discover().definitions()} | google
    folders = sorted(p for p in CURATED.iterdir() if p.is_dir() and p.name != "__pycache__")
    assert {f.name for f in folders} == NAMES
    for folder in folders:
        assert os.listdir(folder) == [SKILL_MD], folder.name  # named in capitals, alone
        found = parse(folder)
        assert isinstance(found, Skill), (folder.name, found)
        assert found.name == folder.name and not found.has_scripts
        assert found.allowed_tools and set(found.allowed_tools) <= have, found.allowed_tools
        assert found.license == "Apache-2.0"
        assert len(found.instructions) < 4000


def test_the_curated_set_starts_off(tmp_path: Path) -> None:
    lib = Library(tmp_path / "skills.json", data_root=tmp_path / "data")
    entries = lib.scan()
    assert {e.id for e in entries} == {f"curated:{n}" for n in NAMES}
    assert {e.state for e in entries} == {"off"} and lib.enabled() == []
