"""Where OpenClaw and Hermes keep what they know, and reading it (CON-8).

Nothing here writes, and nothing here follows what it reads: every line is data. Only
Markdown files and skill folders are opened; the other app's config, keys, sessions
and databases never are. A file or folder that is not there is simply not found.
Layouts as documented on 2026-11-07 (docs/specs/2026-11-07-importers.md).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from trillion.memory.types import MemoryType
from trillion.skills.parse import SKILL_MD, is_link

App = Literal["openclaw", "hermes"]
APPS: tuple[App, ...] = ("openclaw", "hermes")
TITLE: dict[App, str] = {"openclaw": "OpenClaw", "hermes": "Hermes"}

# A Markdown file bigger than this is not notes: it is left out, never cut.
MAX_FILE = 1024 * 1024
# Daily notes read, newest first, and skill folders looked at, per run.
MAX_NOTES = 1000
MAX_SKILLS = 200

_WORKSPACES = ("workspace", "workspace-main", "workspace.default")
_OPENCLAW_HOMES = (".openclaw", ".clawdbot", ".moltbot")
_WORKSPACE_MARKS = ("SOUL.md", "USER.md", "MEMORY.md", "IDENTITY.md", "AGENTS.md", "memory")
# The other app's personality and working rules: never imported (CON-8, PER-1).
_SOUL = "Trillion keeps her own soul. Yours stays where it is."
_RULES = "{app}'s working rules, not something you told it."
_OPENCLAW_RULES = ("AGENTS.md", "TOOLS.md", "HEARTBEAT.md", "BOOT.md", "BOOTSTRAP.md")
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_HERMES_SPLIT = re.compile(r"^[ \t]*§[ \t]*$", re.M)
# `- **Name:** Sam`, `Timezone: Europe/Berlin`, `**What to call them**: Sam`.
_FIELD = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?(?P<key>[A-Za-z][A-Za-z '\u2019]{0,30}?)\s*(?:\*\*|__)?\s*:"
    r"\s*(?:\*\*|__)?\s*(?P<value>.*?)\s*$"
)
_ITEM = re.compile(r"^(?:[-*+]|\d{1,3}[.)])\s+")
_NESTED = re.compile(r"^\s+(?:[-*+]|\d{1,3}[.)])\s+")
_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_HINT = re.compile(r"^(?:_[^_].*_|\*[^*].*\*)$")  # a line all in italics: a template's hint
_COMMENT = re.compile(r"<!--.*?-->", re.S)

USER_NAME_KEYS = ("what to call them", "preferred name", "call them", "name")
TIMEZONE_KEYS = ("timezone", "time zone")


@dataclass(frozen=True, kw_only=True)
class Entry:
    """One thing the other app held, to become one memory."""

    type: MemoryType
    text: str
    file: str  # where in the source, relative, with forward slashes
    as_of: int  # when the source knew it, ms since the epoch


@dataclass(frozen=True, kw_only=True)
class LeftOut:
    name: str
    why: str


@dataclass(kw_only=True)
class Found:
    app: App
    root: Path  # the folder shown to the user as what was read
    user_name: str | None = None
    timezone: str | None = None
    agent_name: str | None = None
    entries: list[Entry] = field(default_factory=list)
    skills: list[Path] = field(default_factory=list)
    left_out: list[LeftOut] = field(default_factory=list)


class SourceError(Exception):
    """The other app's folder cannot be found. The message is safe to show."""


# ── Finding the folders ──────────────────────────────────────────────────


def _dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_workspace(path: Path) -> bool:
    return any((path / mark).exists() for mark in _WORKSPACE_MARKS)


def openclaw_folders(
    source: Path | None, env: Mapping[str, str], home: Path
) -> tuple[Path | None, Path | None]:
    """(state folder, workspace) for OpenClaw. Either may be None."""
    if source is not None:
        base = source.expanduser()
        if not _dir(base):
            return None, None
        if _is_workspace(base):
            return None, base
        return base, next((base / w for w in _WORKSPACES if _dir(base / w)), None)
    state_raw = env.get("OPENCLAW_STATE_DIR")
    if state_raw:
        state: Path | None = Path(state_raw).expanduser()
    else:
        state = next((home / h for h in _OPENCLAW_HOMES if _dir(home / h)), None)
    if state is not None and not _dir(state):
        state = None
    workspace_raw = env.get("OPENCLAW_WORKSPACE_DIR")
    workspace: Path | None = None
    if workspace_raw:
        workspace = Path(workspace_raw).expanduser()
    elif state is not None:
        workspace = next((state / w for w in _WORKSPACES if _dir(state / w)), None)
    if workspace is not None and not _dir(workspace):
        workspace = None
    return state, workspace


def hermes_folder(source: Path | None, env: Mapping[str, str], home: Path) -> Path | None:
    if source is not None:
        base = source.expanduser()
    elif env.get("HERMES_HOME"):
        base = Path(env["HERMES_HOME"]).expanduser()
    else:
        base = home / ".hermes"
    return base if _dir(base) else None


def default_place(app: App, env: Mapping[str, str], home: Path) -> str:
    """Where the app's folder is looked for, as the user would write it."""
    if app == "openclaw":
        return env.get("OPENCLAW_STATE_DIR") or "~/.openclaw"
    return env.get("HERMES_HOME") or "~/.hermes"


# ── Reading files ────────────────────────────────────────────────────────


def _read(path: Path, name: str, found: Found) -> str | None:
    """The file's text, or None. A file that can't be used is listed as left out."""
    if not _file(path):
        return None
    try:
        if path.stat().st_size > MAX_FILE:
            found.left_out.append(LeftOut(name=name, why="too large to be notes (over 1 MB)"))
            return None
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        found.left_out.append(LeftOut(name=name, why="not UTF-8 text"))
    except OSError:
        found.left_out.append(LeftOut(name=name, why="could not be read"))
    return None


def _modified(path: Path, now_ms: int) -> int:
    try:
        return min(now_ms, int(path.stat().st_mtime * 1000))
    except OSError:
        return now_ms


def _day(name: str, fallback: int, now_ms: int) -> int:
    m = _DAY.match(name)
    if not m:
        return fallback
    try:
        day = datetime(int(m[1]), int(m[2]), int(m[3]), 12, tzinfo=UTC)
    except ValueError:
        return fallback
    return min(now_ms, int(day.timestamp() * 1000))


def _value(raw: str) -> str:
    """A field's value without Markdown emphasis or quotes. A placeholder is empty."""
    value = raw.strip().strip("*_`\"'\u201c\u201d").strip()
    if value.startswith(("(", "[", "<")) or value.lower() in {"tbd", "unknown", "n/a", "-"}:
        return ""
    return value


def field_of(line: str) -> tuple[str, str] | None:
    """(key as written, value) when the line reads `Key: value`, else None."""
    m = _FIELD.match(line)
    if not m:
        return None
    return " ".join(m["key"].split()), _value(m["value"])


def split_markdown(text: str) -> list[str]:
    """Entries from free-form notes: each list item (with its sub-items) and each
    paragraph is one. Headings, rules, comments and a front matter block are not."""
    text = _COMMENT.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end is not None:
            lines = lines[end + 1 :]
    entries: list[str] = []
    current: list[str] = []
    fenced = False

    def flush() -> None:
        body = "\n".join(current).strip()
        current.clear()
        if body:
            entries.append(body)

    for line in lines:
        bare = line.strip()
        if bare.startswith(("```", "~~~")):
            fenced = not fenced
            current.append(line.rstrip())
            continue
        if fenced:
            current.append(line.rstrip())
            continue
        if not bare or bare.startswith("#") or _RULE.match(line):
            flush()
            continue
        if _ITEM.match(line):
            flush()
            current.append(_ITEM.sub("", line, count=1).strip())
            continue
        if _NESTED.match(line) and current:
            current.append(bare)
            continue
        if not current and _HINT.match(bare):
            continue
        current.append(bare)
    flush()
    return entries


def split_hermes(text: str) -> list[str]:
    """Hermes memory files: entries separated by a line that is only `§`."""
    parts = _HERMES_SPLIT.split(text.replace("\r\n", "\n"))
    return [p.strip() for p in parts if p.strip()]


def _about_user(
    pieces: list[str], file: str, as_of: int, found: Found, *, profile: bool = True
) -> None:
    """USER.md: the name and timezone are kept aside for setup; the rest are memories.
    A `Key:` line with no value is a template's blank, and is dropped."""
    for piece in pieces:
        kept: list[str] = []
        for line in piece.split("\n"):
            pair = field_of(line) if profile else None
            if pair is None:
                kept.append(line)
                continue
            written, value = pair
            key = written.lower()
            if key in USER_NAME_KEYS and value:
                # "What to call them" beats "Name": it is what she should say.
                if found.user_name is None or key != "name":
                    found.user_name = value
                continue
            if key in TIMEZONE_KEYS and value:
                found.timezone = value.split()[0]
                continue
            if value:
                kept.append(f"{written}: {value}")
        body = "\n".join(kept).strip()
        if body:
            found.entries.append(Entry(type="user", text=body, file=file, as_of=as_of))


def _agent_name(text: str, found: Found) -> None:
    for line in text.split("\n"):
        pair = field_of(line)
        if pair and pair[0].lower() == "name" and pair[1]:
            found.agent_name = pair[1]
            return


def _skill_folders(root: Path, depth: int) -> list[Path]:
    """Folders under `root`, `depth` levels down at most, that hold a SKILL.md. Links
    are never followed."""
    out: list[Path] = []
    if not _dir(root) or is_link(root):
        return out
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and not is_link(p))
    except OSError:
        return out
    for child in children[: MAX_SKILLS * 2]:
        if _file(child / SKILL_MD):
            out.append(child)
        elif depth > 1 and not child.name.startswith("."):
            out.extend(_skill_folders(child, depth - 1))
    return out


# ── Each app ─────────────────────────────────────────────────────────────


def read_openclaw(state: Path | None, workspace: Path | None, now_ms: int) -> Found:
    root = workspace or state
    if root is None:
        raise SourceError("No OpenClaw folder was found.")
    found = Found(app="openclaw", root=root)
    if workspace is not None:
        if _file(workspace / "SOUL.md"):
            found.left_out.append(LeftOut(name="SOUL.md", why=_SOUL))
        for name in _OPENCLAW_RULES:
            if _file(workspace / name):
                found.left_out.append(LeftOut(name=name, why=_RULES.format(app="OpenClaw")))
        user = workspace / "USER.md"
        if (text := _read(user, "USER.md", found)) is not None:
            _about_user(split_markdown(text), "USER.md", _modified(user, now_ms), found)
        if (text := _read(workspace / "IDENTITY.md", "IDENTITY.md", found)) is not None:
            _agent_name(text, found)
            found.left_out.append(
                LeftOut(name="IDENTITY.md", why="only its name is used; the rest is persona")
            )
        memory = workspace / "MEMORY.md"
        if (text := _read(memory, "MEMORY.md", found)) is not None:
            as_of = _modified(memory, now_ms)
            for piece in split_markdown(text):
                found.entries.append(
                    Entry(type="reference", text=piece, file="MEMORY.md", as_of=as_of)
                )
        _daily_notes(workspace / "memory", found, now_ms)
    seen: set[Path] = set()
    for skills in (
        workspace / "skills" if workspace else None,
        state / "skills" if state else None,
    ):
        if skills is None:
            continue
        for folder in _skill_folders(skills, 1):
            if folder.resolve() not in seen:
                seen.add(folder.resolve())
                found.skills.append(folder)
    del found.skills[MAX_SKILLS:]
    return found


def _daily_notes(folder: Path, found: Found, now_ms: int) -> None:
    if not _dir(folder) or is_link(folder):
        return
    try:
        notes = sorted(
            (p for p in folder.iterdir() if p.suffix == ".md" and _file(p)),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        found.left_out.append(LeftOut(name="memory/", why="could not be listed"))
        return
    if len(notes) > MAX_NOTES:
        found.left_out.append(
            LeftOut(
                name="memory/",
                why=f"only the newest {MAX_NOTES} of {len(notes)} notes are read",
            )
        )
    for note in notes[:MAX_NOTES]:
        name = f"memory/{note.name}"
        if (text := _read(note, name, found)) is None:
            continue
        as_of = _day(note.name, _modified(note, now_ms), now_ms)
        for piece in split_markdown(text):
            found.entries.append(Entry(type="project", text=piece, file=name, as_of=as_of))


def read_hermes(root: Path, now_ms: int) -> Found:
    found = Found(app="hermes", root=root)
    if _file(root / "SOUL.md"):
        found.left_out.append(LeftOut(name="SOUL.md", why=_SOUL))
    user = root / "memories" / "USER.md"
    if (text := _read(user, "memories/USER.md", found)) is not None:
        _about_user(split_hermes(text), "memories/USER.md", _modified(user, now_ms), found)
    memory = root / "memories" / "MEMORY.md"
    if (text := _read(memory, "memories/MEMORY.md", found)) is not None:
        as_of = _modified(memory, now_ms)
        for piece in split_hermes(text):
            found.entries.append(
                Entry(type="reference", text=piece, file="memories/MEMORY.md", as_of=as_of)
            )
    found.skills = _skill_folders(root / "skills", 2)[:MAX_SKILLS]
    return found


def read(
    app: App,
    source: Path | None,
    now_ms: int,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Found:
    """Everything the importer would use from `app`. Raises SourceError when the
    app's folder is not there."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    where = str(source) if source is not None else default_place(app, env, home)
    if app == "openclaw":
        state, workspace = openclaw_folders(source, env, home)
        if state is None and workspace is None:
            raise SourceError(f"No OpenClaw folder at {where}. Point to it with --from.")
        return read_openclaw(state, workspace, now_ms)
    root = hermes_folder(source, env, home)
    if root is None:
        raise SourceError(f"No Hermes folder at {where}. Point to it with --from.")
    return read_hermes(root, now_ms)
