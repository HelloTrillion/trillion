"""Assemble the prompt: a stable, cacheable block and volatile context for the user turn (PER-5).

The identity files live in the user data directory and are written only by the
privileged setup path (`persona.setup`). Nothing here writes, and no tool can reach
them (PER-2).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Literal

from trillion import paths
from trillion.brain.types import SystemBlock

DEFAULT_AGENT_NAME = "Trillion"  # the brand is the default name (PER-1a)
DEFAULT_USER_NAME = "the user"
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Directness settings that add an authored overlay. "balanced" is the soul as written.
_TONES = ("blunt", "gentle")
_FIELD = re.compile(r"^([a-z_]+):\s*(.+?)\s*$")
NAME_LIMIT = 40


@dataclass(frozen=True, kw_only=True)
class Identity:
    agent_name: str = DEFAULT_AGENT_NAME
    user_name: str = DEFAULT_USER_NAME
    # How direct the user asked her to be. An overlay adjusts her; it never replaces her.
    directness: str = "balanced"


@dataclass(frozen=True, kw_only=True)
class PendingNote:
    pending_id: str
    summary: str


def fields(path: Path) -> dict[str, str]:
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
    return cleaned[:NAME_LIMIT].strip() or default


def load_identity(directory: Path | None = None) -> Identity:
    d = directory or paths.persona_dir()
    user = fields(d / "USER.md")
    directness = user.get("directness", "")
    return Identity(
        agent_name=_name(fields(d / "IDENTITY.md").get("agent_name"), DEFAULT_AGENT_NAME),
        user_name=_name(user.get("user_name"), DEFAULT_USER_NAME),
        directness=directness if directness in _TONES else "balanced",
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
    identity: Identity,
    soul: str,
    granted_roots: Sequence[Path] = (),
    standing: Sequence[tuple[str, str]] = (),
) -> list[SystemBlock]:
    """Soul, then rules, then the modality overlay: one block, identical every turn.

    `standing` is the user's standing rules as (id, text). They sit here, outside the
    conversation, so summarizing old turns can never drop one (BRN-5). Their text
    is the user's own and is not filled in, so a brace in it stays a brace."""
    shared = "\n".join(f"- {r}" for r in granted_roots) or "(none: file tools cannot be used)"
    # Names are filled into the authored text only, never into a folder path.
    parts = [
        _fill(_HTML_COMMENT.sub("", soul).strip(), identity),
        _fill(_prompt("rules.md"), identity),
        f"Shared folders:\n{shared}",
        _fill(_prompt("text.md"), identity),
    ]
    if standing:
        listed = "\n".join(f"- [{rid}] {text}" for rid, text in standing)
        parts.append(f"{_fill(_prompt('standing.md'), identity)}\n{listed}")
    if identity.directness in _TONES:
        parts.insert(1, _fill(_prompt(f"tone_{identity.directness}.md"), identity))
    return [SystemBlock(text="\n\n".join(parts), cache=True)]


Leg = Literal["model", "hearing", "voice"]


@cache
def _degraded() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _HTML_COMMENT.sub("", _prompt("degraded.md")).splitlines():
        key, sep, said = line.partition(":")
        if sep and key.strip():
            out[key.strip()] = said.strip()
    return out


def degraded(leg: Leg, kind: str) -> str:
    """Her one sentence for one leg being down (VOI-14): what broke, and what happens next."""
    lines = _degraded()
    return lines.get(f"{leg}.{kind}") or lines[f"{leg}.other"]


def compaction(identity: Identity) -> str:
    """What the summarizer is told (BRN-5). Authored, like every other instruction."""
    return _fill(_prompt("compact.md"), identity)


def greeting(identity: Identity) -> str:
    """What the app tells her when onboarding ends, so her first real line is her own."""
    return _fill(_prompt("greeting.md"), identity)


def volatile_context(now: datetime, pending: Sequence[PendingNote], recalled: str = "") -> str:
    """What changes between turns. It is prepended to the newest user message only,
    so the cached prefix and every earlier message stay byte-identical.

    `recalled` is memory that matches the message, already wrapped as untrusted
    text by the caller. It stays outside the identity block and outside `<context>`:
    what was once said is never promoted to something the host asserts (MEM-6b)."""
    lines = [f"now: {now.strftime('%A %Y-%m-%d %H:%M %Z').strip()}"]
    lines.extend(f"waiting for an answer: {p.pending_id}: {p.summary}" for p in pending)
    context = "<context>\n" + "\n".join(lines) + "\n</context>"
    return f"{context}\n{recalled}" if recalled else context
