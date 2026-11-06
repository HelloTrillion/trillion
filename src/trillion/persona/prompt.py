"""Assemble the prompt: a stable, cacheable block and volatile context for the user turn (PER-5).

The identity files live in the user data directory and are written only by the
privileged setup path (`persona.setup`). Nothing here writes, and no tool can reach
them (PER-2).
"""

from __future__ import annotations

import json
import os
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
from trillion.helpers.store import OwnHelper
from trillion.memory.present import inert
from trillion.spine.untrusted import wrap

DEFAULT_AGENT_NAME = "Trillion"  # the brand is the default name (PER-1a)
DEFAULT_USER_NAME = "the user"
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Directness settings that add an authored overlay. "balanced" is the soul as written.
_TONES = ("blunt", "gentle")
_FIELD = re.compile(r"^([a-z_]+):\s*(.+?)\s*$")
NAME_LIMIT = 40
# The skills list stays small, and stops with a count past either limit (CON-6).
MAX_SKILLS_LISTED = 50
MAX_SKILLS_CHARS = 6000


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


@dataclass(frozen=True, kw_only=True)
class JobNote:
    """A read still running in the background (the background reads spec)."""

    tool: str
    job_id: str
    seconds: int


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


def skill_index(skills: Sequence[tuple[str, str]]) -> tuple[list[str], int]:
    """The lines listing the skills that are on, and how many did not fit (CON-6)."""
    lines: list[str] = []
    used = 0
    for label, description in skills:
        line = f"- {label}: {description}"
        if len(lines) >= MAX_SKILLS_LISTED or used + len(line) > MAX_SKILLS_CHARS:
            break
        lines.append(line)
        used += len(line)
    return lines, len(skills) - len(lines)


@dataclass(frozen=True, kw_only=True)
class SharedFolder:
    """One share in scope for a turn, as her prompt lists it (the shared folders spec)."""

    name: str
    path: Path
    read_only: bool


def _near_home(p: Path) -> str:
    """A folder under the home folder spelled from `~`, as the user would say it."""
    home = Path.home()
    if p == home or not p.is_relative_to(home):
        return str(p)
    return f"~{os.sep}{p.relative_to(home)}"


def _shared_line(folder: Path | SharedFolder) -> str:
    if isinstance(folder, Path):
        return f"- {folder}"
    access = "read only" if folder.read_only else "read and change"
    # Quoted, so a name can never read as more of the line than a name.
    return f"- {json.dumps(folder.name, ensure_ascii=False)}: {_near_home(folder.path)} ({access})"


def system_blocks(
    identity: Identity,
    soul: str,
    granted_roots: Sequence[Path | SharedFolder] = (),
    standing: Sequence[tuple[str, str]] = (),
    skills: Sequence[tuple[str, str]] = (),
    *,
    helpers: bool = False,
) -> list[SystemBlock]:
    """Soul, then rules, then the modality overlay: one block, identical every turn.

    `standing` is the user's standing rules as (id, text). They sit here, outside the
    conversation, so summarizing old turns can never drop one (BRN-5). Their text
    is the user's own and is not filled in, so a brace in it stays a brace.

    `skills` is (label, description) for each skill turned on (CON-6), in a stable
    order, so the block only changes when a skill is switched.

    `helpers` says the user can have helpers of their own (the your helpers spec): her
    instructions for them come only then, so no other setup's block changes."""
    shared = "\n".join(_shared_line(r) for r in granted_roots) or (
        "(none: file tools cannot be used)"
    )
    # Names are filled into the authored text only, never into a folder path.
    parts = [
        _fill(_HTML_COMMENT.sub("", soul).strip(), identity),
        _fill(_prompt("rules.md"), identity),
        f"Shared folders:\n{shared}",
        _fill(_prompt("text.md"), identity),
    ]
    if helpers:
        parts.append(_fill(_prompt("helpers.md"), identity))
    if skills:
        lines, more = skill_index(skills)
        tail = f"\n({more} more skills are turned on but not listed here.)" if more else ""
        parts.append(f"{_fill(_prompt('skills.md'), identity)}\n" + "\n".join(lines) + tail)
    if standing:
        listed = "\n".join(f"- [{rid}] {text}" for rid, text in standing)
        parts.append(f"{_fill(_prompt('standing.md'), identity)}\n{listed}")
    if identity.directness in _TONES:
        parts.insert(1, _fill(_prompt(f"tone_{identity.directness}.md"), identity))
    return [SystemBlock(text="\n\n".join(parts), cache=True)]


def _quoted(text: str) -> str:
    """A name in her context: one line, nothing that reads as markup or closes its quotes."""
    return inert(text).replace('"', "'")


# Her own hand-off guidance sits in the same instructions: a helper never follows it.
DO_IT_YOURSELF = (
    "You are the helper: do the work yourself. You never hand work on or make helpers, "
    "whatever other instructions say."
)


def helper_overlay(h: OwnHelper, agent_name: str = DEFAULT_AGENT_NAME) -> str:
    """What every turn in a helper's own chat is told on top of her usual instructions
    (the your helpers spec): it is her, focused on one job. Its fields are the user's,
    made inert. The last line names it, for helper_update, narrow and widen."""
    job = inert(h.job)
    # A job in its own voice ("I keep track of...") is said as its words; any other as is.
    lead = "In your own words, your job: " if re.match(r"I\b", job) else "Your job: "
    said = f"You are {inert(h.name)}, one of {inert(agent_name)}'s helpers. {lead}{job}"
    if not job.endswith((".", "!", "?")):
        said += "."

    if h.notes.strip():
        said += f" Follow these notes from the user: {inert(h.notes)}"
    said += f" {DO_IT_YOURSELF}"
    return f'{said}\nhelper: {h.id} "{_quoted(h.name)}"'


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


# The app's line for a leg stopped at its hard cap (BRN-8). Kept here, not in
# degraded.md: they are the app's words, not a prompt, and change nothing she is sent.
_CAPPED: dict[Leg, str] = {
    "model": "I've reached the {cap} cap for {provider} this month, so I've stopped "
    "before spending more.",
    "voice": "I've reached the {cap} cap for {provider} this month, so I'll answer in text.",
    "hearing": "I've reached the {cap} cap for {provider} this month, so I've stopped "
    "listening, but you can still type.",
}


def capped(leg: Leg, provider: str, cap: str) -> str:
    """The app's one line for a leg stopped at its hard cap (BRN-7): which cap, whose,
    and what happens next. `cap` is already in words ("$50")."""
    return _CAPPED[leg].replace("{cap}", cap).replace("{provider}", provider)


def compaction(identity: Identity) -> str:
    """What the summarizer is told (BRN-5). Authored, like every other instruction."""
    return _fill(_prompt("compact.md"), identity)


def scheduled(identity: Identity) -> str:
    """What a timed ask is told on top of her usual instructions: the user is away (AGT-9)."""
    return _fill(_prompt("scheduled.md"), identity)


def greeting(identity: Identity) -> str:
    """What the app tells her when onboarding ends, so her first real line is her own."""
    return _fill(_prompt("greeting.md"), identity)


# From this user message of a conversation on, she is asked to snap back to her own
# voice (PER-4). A module value, read on every call, so the drift eval can turn it off
# inside its own process to measure what it does.
CHECKPOINT_FROM = 5


def volatile_context(
    now: datetime,
    pending: Sequence[PendingNote],
    recalled: str = "",
    spoken: Identity | None = None,
    zone: str = "",
    board: str = "",
    routine: str = "",
    jobs: Sequence[JobNote] = (),
    turn: int = 0,
    who: Identity | None = None,
    helper: str = "",
    helpers: Sequence[tuple[str, str, str]] = (),
) -> str:
    """What changes between turns. It is prepended to the newest user message only,
    so the cached prefix and every earlier message stay byte-identical.

    `recalled` is memory that matches the message, already wrapped as untrusted
    text by the caller. It stays outside the identity block and outside `<context>`:
    what was once said is never promoted to something the host asserts (MEM-6b).

    `zone` is the user's timezone by name, so she can work out a time for a reminder
    in it (AGT-9); `now` is already in that zone.

    `routine` names the routine this conversation belongs to, as `id "name"`, so she
    knows which one feedback here changes (the routines spec).

    `jobs` are her reads still running in the background, so asked whether it is in
    yet she says it is on its way, and never starts the same read again.

    `board` is the one-line summary of her board (SUR-1). It is wrapped as untrusted
    here: its titles may quote a page she read. It rides with the message, never in the
    cached block, and with no board nothing is added.

    `helper` is a helper's overlay (`helper_overlay`), in its own chat. `helpers` are
    (id, name, job) for each helper not paused, in her own chats, so she can hand one
    work. A job may have been drafted from something she read, so they are wrapped as
    untrusted, as the board is. Both ride with the message, never in the cached block.

    `turn` counts the user's messages in this conversation, this one included. From
    `CHECKPOINT_FROM` on, and when `who` says whom she is talking to, the tonal
    checkpoint (PER-4) rides here too, never in the cached block."""
    when = now.strftime("%A %Y-%m-%d %H:%M %Z").strip()
    # "UTC (UTC)" says nothing twice: a zone already named by its abbreviation is left.
    named = zone and zone != now.strftime("%Z")
    lines = [f"now: {when} ({zone})" if named else f"now: {when}"]
    lines.extend(f"waiting for an answer: {p.pending_id}: {p.summary}" for p in pending)
    if routine:
        # The routine this conversation belongs to: its id, for routine_narrow and widen.
        lines.append(f"routine: {routine}")
    if helper:
        lines.append(helper)
    lines.extend(f"background: {j.tool} (job {j.job_id}) running {j.seconds} s" for j in jobs)
    if spoken is not None:
        # How this one message arrived (PER-3, #40). It rides here, with the message,
        # so the cached block stays the same whether the user types or talks.
        lines.append(_fill(_prompt("voice.md"), spoken))
    if who is not None and turn >= CHECKPOINT_FROM:
        # The tonal checkpoint (PER-4): a request, every turn from the fifth. EVL-5
        # measures whether it works.
        lines.append(_fill(_prompt("checkpoint.md"), who))
    context = "<context>\n" + "\n".join(lines) + "\n</context>"
    extra = [wrap("workspace", board)] if board else []
    if helpers:
        listed = "\n".join(f'{hid} "{_quoted(name)}": {inert(job)}' for hid, name, job in helpers)
        extra.append(wrap("helpers", listed))
    if recalled:
        extra.append(recalled)
    return "\n".join([context, *extra])
