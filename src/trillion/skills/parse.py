"""Reading one skill folder: the header, the instructions, and the files beside them.

The header follows the Agent Skills format: YAML between two `---` lines, with a
required `name` and `description`. Anything that breaks a rule or a limit makes the
skill invalid, with a reason the user can read. Nothing here runs or imports a file.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from io import BufferedReader
from pathlib import Path

import yaml

SKILL_MD = "SKILL.md"
MAX_SKILL_MD = 64 * 1024
MAX_FILE = 256 * 1024
MAX_FILES = 200
MAX_DEPTH = 8
MAX_NAME = 64
MAX_DESCRIPTION = 1024
_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
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
    # The exact bytes of SKILL.md this was parsed from, for the fingerprint to cover.
    source: bytes = field(default=b"", repr=False, compare=False)

    @property
    def has_scripts(self) -> bool:
        return any(f.script for f in self.files)


@dataclass(frozen=True)
class Invalid:
    reason: str


class TooBig(Exception):
    """A folder is too big or too deep to be a skill. Raised before any of it is used,
    so a huge folder is never walked to the end."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def is_link(path: Path) -> bool:
    """A symlink, or a Windows junction, which `is_symlink` does not count."""
    return path.is_symlink() or os.path.isjunction(path)


def walk(folder: Path) -> list[tuple[str, Path, int]]:
    """Every file under `folder` as (relative path, path, depth), links included and
    never followed, ignored names skipped, in sorted order of relative path.

    Raises TooBig as soon as there are more files or folders than a skill may have,
    or a folder deeper than MAX_DEPTH, so nothing walks a home folder by mistake. A
    folder deeper than MAX_DEPTH counts even when empty. The checks run per folder:
    os.walk still lists each folder whole, so one huge folder is listed before the
    walk stops."""
    found: list[tuple[str, Path, int]] = []
    folders = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        here = Path(root)
        depth = len(here.relative_to(folder).parts)
        # A link to a folder shows up in `dirs`: list it like a file, never enter it.
        links = [d for d in dirs if is_link(here / d)]
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and d not in links)
        if dirs and depth >= MAX_DEPTH:
            raise TooBig(f"its folders are too deep (over {MAX_DEPTH} levels)")
        folders += len(dirs)
        if folders > MAX_FILES:
            raise TooBig(f"it has too many folders (over {MAX_FILES})")
        for name in [*files, *links]:
            if name in IGNORED:
                continue
            path = here / name
            found.append((path.relative_to(folder).as_posix(), path, depth))
        if len(found) > MAX_FILES + 1:  # SKILL.md and MAX_FILES others
            raise TooBig(f"it has too many files (over {MAX_FILES})")
    return sorted(found, key=lambda entry: entry[0])


def open_regular(path: Path) -> BufferedReader:
    """Open `path` for reading only if it is a regular file: never a link, a pipe or a
    device. The open does not block, so a named pipe swapped in can't hang us, and
    fstat checks what was actually opened."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


# The closing line is exactly `---`, give or take trailing spaces: `----` is not one.
_OPEN = re.compile(r"\A---[ \t]*\n")
_CLOSE = re.compile(r"^---[ \t]*$", re.M)


def _uses_references(text: str) -> bool:
    """Anchors and aliases let a tiny header expand into gigabytes. Skills don't need
    them, so any at all is refused before the header is built."""
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            return True
    return False


def _header(text: str) -> tuple[dict[str, object], str] | Invalid:
    text = text.removeprefix("\ufeff").replace("\r\n", "\n")
    opening = _OPEN.match(text)
    closing = _CLOSE.search(text, opening.end()) if opening else None
    if not opening or not closing:
        return Invalid("its SKILL.md has no header (the --- block at the top)")
    raw = text[opening.end() : closing.start()]
    try:
        if _uses_references(raw):
            return Invalid("its header uses YAML references, which skills don't need")
        data = yaml.safe_load(raw)
    except (yaml.YAMLError, RecursionError):
        return Invalid("its header could not be read")
    if data is None:  # an empty block: no fields, so no name
        data = {}
    if not isinstance(data, dict):
        return Invalid("its header is not a set of fields")
    body = text[closing.end() :].removeprefix("\n")
    return data, body.strip()


def _tools(raw: object) -> tuple[str, ...] | Invalid:
    # Claude Code writes `Bash(git add:*), Read`: with a comma, only commas separate.
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts = raw.split(",") if "," in raw else raw.split()
        return tuple(t.strip() for t in parts if t.strip())
    if isinstance(raw, list) and all(isinstance(t, str) for t in raw):
        return tuple(t.strip() for t in raw if t.strip())
    return Invalid("its allowed-tools must be text or a list of text")


def _is_text(path: Path) -> bool:
    try:
        with open_regular(path) as f:
            f.read(MAX_FILE + 1).decode("utf-8")
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
        info = md.lstat()
    except OSError:
        return Invalid("it has no SKILL.md")
    if is_link(md) or not stat.S_ISREG(info.st_mode):
        return Invalid("its SKILL.md is not a plain file")
    too_large = Invalid(f"its SKILL.md is too large (over {MAX_SKILL_MD // 1024} KB)")
    if info.st_size > MAX_SKILL_MD:
        return too_large
    try:
        with open_regular(md) as f:
            data = f.read(MAX_SKILL_MD + 1)
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return Invalid("its SKILL.md is not readable text")
    if len(data) > MAX_SKILL_MD:  # it grew after the lstat
        return too_large
    header = _header(text)
    if isinstance(header, Invalid):
        return header
    fields, instructions = header
    # Only plain strings: str() on an arbitrary YAML value could be huge.
    for key in ("name", "description"):
        if fields.get(key) is not None and not isinstance(fields[key], str):
            return Invalid(f"its {key} must be text")
    name = str(fields.get("name") or "").strip()
    description = " ".join(str(fields.get("description") or "").split())
    if not name:
        return Invalid("its header has no name")
    if len(name) > MAX_NAME or not _NAME.fullmatch(name):
        return Invalid(
            "its name must be lowercase letters, digits and single hyphens, "
            f"up to {MAX_NAME} characters"
        )
    if not description:
        return Invalid("its header has no description")
    if len(description) > MAX_DESCRIPTION:
        return Invalid(f"its description is longer than {MAX_DESCRIPTION} characters")
    tools = _tools(fields.get("allowed-tools"))
    if isinstance(tools, Invalid):
        return tools
    try:
        entries = walk(folder)
    except TooBig as e:
        return Invalid(e.reason)
    except OSError:
        return Invalid("its files could not be listed")
    # On a disk that ignores case, skill.md opens as SKILL.md but is listed as itself,
    # and the fingerprint would not cover the bytes read under the other name.
    if not any(rel == SKILL_MD for rel, _, _ in entries):
        return Invalid("its instructions file must be named SKILL.md, in capitals")
    found: list[SkillFile] = []
    for rel, path, depth in entries:
        try:
            rel.encode("utf-8")  # a name the OS let through but that isn't text
        except UnicodeEncodeError:
            return Invalid("a file name is not valid text")
        # Belt and braces: walk() raises TooBig before either of these can be true.
        if depth > MAX_DEPTH:
            return Invalid(f"its folders are too deep (over {MAX_DEPTH} levels)")
        if rel == SKILL_MD:
            continue
        if len(found) >= MAX_FILES:  # belt and braces, as above
            return Invalid(f"it has too many files (over {MAX_FILES})")
        try:
            info = path.lstat()
        except OSError:
            return Invalid(f"{rel} could not be read")
        link = stat.S_ISLNK(info.st_mode) or os.path.isjunction(path)
        # Pipes and devices could hang a read or never end: no skill needs one.
        if not link and not stat.S_ISREG(info.st_mode):
            return Invalid(f"{rel} is not a regular file")
        if info.st_size > MAX_FILE:
            return Invalid(f"{rel} is too large (over {MAX_FILE // 1024} KB)")
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
        allowed_tools=tools,
        # An odd license (a number, a list) is only a label: ignore it, don't refuse.
        license=lic.strip() if isinstance(lic := fields.get("license"), str) else "",
        files=tuple(found),
        source=data,
    )
