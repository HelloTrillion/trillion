"""Assemble the prompt: a stable, cacheable block and volatile context for the user turn (PER-5).

The identity files live in the user data directory and are written only by the
privileged setup path. Nothing here writes, and no tool can reach them (PER-2).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from trillion import paths
from trillion.brain.types import SystemBlock

DEFAULT_AGENT_NAME = "Trillion"  # the brand is the default name (PER-1a)
DEFAULT_USER_NAME = "the user"
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FIELD = re.compile(r"^([a-z_]+):\s*(.+?)\s*$")
_NAME_LIMIT = 40


@dataclass(frozen=True, kw_only=True)
class Identity:
    agent_name: str = DEFAULT_AGENT_NAME
    user_name: str = DEFAULT_USER_NAME


@dataclass(frozen=True, kw_only=True)
class PendingNote:
    pending_id: str
    summary: str


def _fields(path: Path) -> dict[str, str]:
    """`key: value` lines from a file's front matter. A missing file has none."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        m = _FIELD.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _name(raw: str | None, default: str) -> str:
    """A name goes into every prompt: one line, no braces, a sane length."""
    if raw is None:
        return default
    cleaned = "".join(ch for ch in raw.strip().strip("\"'") if ch.isprintable() and ch not in "{}")
    return cleaned[:_NAME_LIMIT].strip() or default


def load_identity(directory: Path | None = None) -> Identity:
    d = directory or paths.persona_dir()
    return Identity(
        agent_name=_name(_fields(d / "IDENTITY.md").get("agent_name"), DEFAULT_AGENT_NAME),
        user_name=_name(_fields(d / "USER.md").get("user_name"), DEFAULT_USER_NAME),
    )


def _shipped_soul() -> str:
    bundled = files("trillion.persona") / "default" / "SOUL.md"
    if bundled.is_file():
        return bundled.read_text(encoding="utf-8")
    # A source checkout: the authored default at the repository root.
    return (paths.package_root().parents[1] / "persona" / "SOUL.md").read_text(encoding="utf-8")


def load_soul(directory: Path | None = None) -> str:
    """The user's copy if onboarding has made one, otherwise the shipped default."""
    try:
        return ((directory or paths.persona_dir()) / "SOUL.md").read_text(encoding="utf-8")
    except OSError:
        return _shipped_soul()


def _prompt(name: str) -> str:
    return (files("trillion.persona") / "prompts" / name).read_text(encoding="utf-8").strip()


def _fill(text: str, identity: Identity) -> str:
    return text.replace("{agent_name}", identity.agent_name).replace(
        "{user_name}", identity.user_name
    )


def system_blocks(
    identity: Identity, soul: str, granted_roots: Sequence[Path] = ()
) -> list[SystemBlock]:
    """Soul, then rules, then the modality overlay: one block, identical every turn."""
    shared = "\n".join(f"- {r}" for r in granted_roots) or "(none: file tools cannot be used)"
    # Names are filled into the authored text only, never into a folder path.
    parts = (
        _fill(_HTML_COMMENT.sub("", soul).strip(), identity),
        _fill(_prompt("rules.md"), identity),
        f"Shared folders:\n{shared}",
        _fill(_prompt("text.md"), identity),
    )
    return [SystemBlock(text="\n\n".join(parts), cache=True)]


def volatile_context(now: datetime, pending: Sequence[PendingNote]) -> str:
    """What changes between turns. It is prepended to the newest user message only,
    so the cached prefix and every earlier message stay byte-identical."""
    lines = [f"now: {now.strftime('%A %Y-%m-%d %H:%M %Z').strip()}"]
    lines.extend(f"waiting for an answer: {p.pending_id}: {p.summary}" for p in pending)
    return "<context>\n" + "\n".join(lines) + "\n</context>"
