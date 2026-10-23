"""skill_open and skill_file (CON-6): reading a skill the user turned on.

Both only read, so they run at once. What they return is text the user approved
byte for byte when they turned the skill on, so it is not wrapped as untrusted; it
still cannot change a gate, because the gates are code. Every call checks the
fingerprint again: a skill whose files changed is refused until the user looks at it.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from trillion.skills.fingerprint import fingerprint
from trillion.skills.library import Entry, Library, LibraryError
from trillion.skills.parse import MAX_FILE, SKILL_MD, Skill, is_link, open_regular
from trillion.spine.contract import (
    JSON,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

OPEN, FILE = "skill_open", "skill_file"
_DRIVE = re.compile(r"^[A-Za-z]:")
_SKILL_ARG = {
    "type": "string",
    "minLength": 1,
    "maxLength": 600,
    "description": "The skill's name as listed, or its id.",
}


def _refused(text: str, kind: str = "refused") -> ToolResult:
    return ToolResult(ok=False, content=text, effect="none", error_kind=kind)


def _kb(size: int) -> str:
    return f"{max(1, round(size / 1024))} KB"


class _SkillTool(BaseTool):
    def __init__(self) -> None:
        self._library: Library | None = None

    def bind(self, library: Library) -> None:
        """Called once at startup with the running process's library."""
        self._library = library

    def _entry(self, ref: object) -> tuple[Entry, Skill] | ToolResult:
        if self._library is None:
            return _refused("skills are not available", "unavailable")
        try:
            entry = self._library.lookup(str(ref))
        except LibraryError as e:
            return _refused(f"{e}. Only skills the user turned on in Settings → Skills can be read")
        if entry.state == "invalid" or entry.skill is None:
            return _refused(
                f"the skill {entry.name!r} can't be used: {entry.reason or 'it is not valid'}",
                "invalid",
            )
        if entry.state != "on":
            return _refused(
                f"the skill {entry.name!r} changed since it was turned on, so it is off until "
                "the user looks at it again in Settings → Skills",
                "changed",
            )
        return entry, entry.skill


class SkillOpen(_SkillTool):
    definition = ToolDefinition(
        name=OPEN,
        description=(
            "Read the instructions of one of the skills listed in your instructions, when "
            "the user's request matches what it says it is for. Returns its instructions "
            "and the list of its other files."
        ),
        input_schema={
            "type": "object",
            "properties": {"skill": _SKILL_ARG},
            "required": ["skill"],
            "additionalProperties": False,
        },
        risk=Risk.READ,
        category="skills",
        timeout_s=10,
        trusted_output=True,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        found = self._entry(args.get("skill", ""))
        if isinstance(found, ToolResult):
            return found
        entry, skill = found
        # The library parsed it between two matching fingerprints: these are the
        # instructions the user approved.
        lines = [f'<skill name="{entry.name}">', skill.instructions, "</skill>"]
        listed = [f for f in skill.files if f.text]
        if listed:
            lines.append("Files in this skill (read one with skill_file):")
            for f in listed:
                note = ", a script: Trillion never runs it" if f.script else ""
                lines.append(f"- {f.path} ({_kb(f.size)}{note})")
        return ToolResult(ok=True, content="\n".join(lines), effect="none")


class SkillFile(_SkillTool):
    definition = ToolDefinition(
        name=FILE,
        description=(
            "Read one file from a skill you opened with skill_open, by its path as listed "
            "there. Scripts come back as text: you can read them, never run them."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "skill": _SKILL_ARG,
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 400,
                    "description": "The file's path inside the skill, as skill_open listed it.",
                },
            },
            "required": ["skill", "path"],
            "additionalProperties": False,
        },
        risk=Risk.READ,
        category="skills",
        timeout_s=10,
        # Inside the skill's own folder, never the granted folders: checked here.
        outside_path_args=("path",),
        trusted_output=True,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        found = self._entry(args.get("skill", ""))
        if isinstance(found, ToolResult):
            return found
        entry, skill = found
        raw = str(args.get("path", "")).strip()
        rel = PurePosixPath(raw.replace("\\", "/"))
        if not raw or rel.is_absolute() or _DRIVE.match(raw) or ".." in rel.parts:
            return _refused("give the file's path inside the skill, as skill_open listed it")
        name = rel.as_posix()
        if name == SKILL_MD:
            return _refused("use skill_open to read SKILL.md")
        listed = {f.path: f for f in skill.files}.get(name)
        if listed is None:
            return _refused("that file is not in this skill", "not_found")
        here = entry.folder
        for part in rel.parts:
            here = here / part
            if is_link(here):
                return _refused("that file is a link; links in skills are never followed")
        if not listed.text:
            return _refused("that file is not text Trillion can read")
        try:
            with open_regular(here) as f:  # never a link, a pipe or a device
                data = f.read(MAX_FILE + 1)
        except OSError:
            return _refused("that file could not be read", "unreadable")
        if len(data) > MAX_FILE:
            return _refused(f"that file is too large (over {MAX_FILE // 1024} KB)")
        # Swapped between the lookup and the read: what was read is not what was approved.
        if fingerprint(entry.folder) != entry.fingerprint:
            return _refused(
                "that skill changed while it was being read, so it is off until the user "
                "looks at it again in Settings → Skills",
                "changed",
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _refused("that file is not text Trillion can read")
        if listed.script:
            text = "(A script. Trillion never runs it; read it to follow the skill.)\n" + text
        return ToolResult(ok=True, content=text, effect="none")
