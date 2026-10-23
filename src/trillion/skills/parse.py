"""Reading one skill folder: the header, the instructions, and the files beside them.

The header follows the Agent Skills format: YAML between two `---` lines, with a
required `name` and `description`. Anything that breaks a rule or a limit makes the
skill invalid, with a reason the user can read. Nothing here runs or imports a file.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

SKILL_MD = "SKILL.md"
MAX_SKILL_MD = 64 * 1024
MAX_FILE = 256 * 1024
MAX_FILES = 200
MAX_DEPTH = 8
MAX_NAME = 64
MAX_DESCRIPTION = 1024
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".ts", ".rb", ".ps1"})
# What editors and operating systems leave behind: never part of a skill.
IGNORED = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__", ".git"})


@dataclass(frozen=True, kw_only=True)
class SkillFile:
    path: str  # relative to the skill's folder, with forward slashes
    size: int
    script: bool
    text: bool


@dataclass(frozen=True, kw_only=True)
class Skill:
    name: str
    description: str
    instructions: str
    allowed_tools: tuple[str, ...]
    license: str
    files: tuple[SkillFile, ...]  # every file but SKILL.md

    @property
    def has_scripts(self) -> bool:
        return any(f.script for f in self.files)


@dataclass(frozen=True)
class Invalid:
    reason: str


def walk(folder: Path) -> Iterator[tuple[str, Path, int]]:
    """Every file under `folder` as (relative path, path, depth), symlinks included and
    never followed, ignored names skipped, in sorted order."""
    for root, dirs, files in os.walk(folder, followlinks=False):
        here = Path(root)
        depth = len(here.relative_to(folder).parts)
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and not (here / d).is_symlink())
        links = [d for d in os.listdir(here) if (here / d).is_symlink() and (here / d).is_dir()]
        for name in sorted({*files, *links}):
            if name in IGNORED:
                continue
            path = here / name
            yield path.relative_to(folder).as_posix(), path, depth


def _header(text: str) -> tuple[dict[str, object], str] | Invalid:
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return Invalid("its SKILL.md has no header (the --- block at the top)")
    end = text.find("\n---", 4)
    if end < 0:
        return Invalid("its SKILL.md has no header (the --- block at the top)")
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return Invalid("its header could not be read")
    if not isinstance(data, dict):
        return Invalid("its header is not a set of fields")
    body = text[end + 4 :].removeprefix("\n")
    return data, body.strip()


def _tools(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        return tuple(t for t in re.split(r"[\s,]+", raw) if t)
    if isinstance(raw, list):
        return tuple(str(t).strip() for t in raw if str(t).strip())
    return ()


def _is_text(path: Path) -> bool:
    try:
        path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True


def _is_script(path: Path, mode: int) -> bool:
    return path.suffix.lower() in SCRIPT_SUFFIXES or (
        stat.S_ISREG(mode) and bool(mode & 0o111) and os.name != "nt"
    )


def parse(folder: Path) -> Skill | Invalid:
    md = folder / SKILL_MD
    try:
        size = md.lstat().st_size
    except OSError:
        return Invalid("it has no SKILL.md")
    if md.is_symlink() or not md.is_file():
        return Invalid("its SKILL.md is not a plain file")
    if size > MAX_SKILL_MD:
        return Invalid(f"its SKILL.md is too large (over {MAX_SKILL_MD // 1024} KB)")
    try:
        text = md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Invalid("its SKILL.md is not readable text")
    header = _header(text)
    if isinstance(header, Invalid):
        return header
    fields, instructions = header
    name = str(fields.get("name") or "").strip()
    description = " ".join(str(fields.get("description") or "").split())
    if not name:
        return Invalid("its header has no name")
    if len(name) > MAX_NAME or not _NAME.match(name):
        return Invalid(
            "its name must be lowercase letters, digits and single hyphens, "
            f"up to {MAX_NAME} characters"
        )
    if not description:
        return Invalid("its header has no description")
    if len(description) > MAX_DESCRIPTION:
        return Invalid(f"its description is longer than {MAX_DESCRIPTION} characters")
    found: list[SkillFile] = []
    for rel, path, depth in walk(folder):
        if depth >= MAX_DEPTH:
            return Invalid(f"its folders are too deep (over {MAX_DEPTH} levels)")
        if rel == SKILL_MD:
            continue
        if len(found) >= MAX_FILES:
            return Invalid(f"it has too many files (over {MAX_FILES})")
        info = path.lstat()
        if info.st_size > MAX_FILE:
            return Invalid(f"{rel} is too large (over {MAX_FILE // 1024} KB)")
        link = stat.S_ISLNK(info.st_mode)
        found.append(
            SkillFile(
                path=rel,
                size=info.st_size,
                script=not link and _is_script(path, info.st_mode),
                text=not link and _is_text(path),
            )
        )
    return Skill(
        name=name,
        description=description,
        instructions=instructions,
        allowed_tools=_tools(fields.get("allowed-tools")),
        license=str(fields.get("license") or "").strip(),
        files=tuple(found),
    )
