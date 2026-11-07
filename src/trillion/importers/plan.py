"""What an import would do, worked out against what Trillion already holds (CON-8).

Nothing here writes. The plan is what the dry run prints, and what `--apply` does.
Every piece of the other app's text that is printed goes through `shown`, so a
file can't move the cursor, clear the screen or pose as Trillion's own output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from trillion.importers.sources import TITLE, Entry, Found, LeftOut
from trillion.memory import files
from trillion.memory.book import same_key
from trillion.memory.types import MAX_TEXT
from trillion.persona import setup
from trillion.skills.fingerprint import fingerprint
from trillion.skills.parse import Invalid, Skill, TooBig, header_fields, is_link, parse, walk

# New memories added by one run (MEM-6b). The rest wait for the next run.
MAX_NEW_MEMORIES = 500

FieldName = Literal["user_name", "timezone", "agent_name"]
LABEL: dict[FieldName, str] = {
    "user_name": "your name",
    "timezone": "timezone",
    "agent_name": "her name",
}
# set: nothing there yet. same: already so. conflict: the user set something else.
# unusable: the value can't be used, or has nowhere to go yet.
Status = Literal["set", "same", "conflict", "unusable"]


@dataclass(frozen=True, kw_only=True)
class Change:
    field: FieldName
    new: str
    old: str | None
    status: Status
    why: str = ""


@dataclass(frozen=True, kw_only=True)
class SkillPlan:
    source: Path
    name: str  # the skill's own name when it parsed, else its folder's
    status: Literal["copy", "held", "left_out"]
    why: str = ""
    needs: tuple[str, ...] = ()


@dataclass(kw_only=True)
class Plan:
    found: Found
    profile: setup.Profile | None  # what setup holds now
    changes: list[Change] = field(default_factory=list)
    memories: list[Entry] = field(default_factory=list)
    held: int = 0  # already remembered, in any state
    too_long: int = 0
    waiting: int = 0  # past this run's bound: the next run brings them
    skills: list[SkillPlan] = field(default_factory=list)


def shown(text: str, width: int = 72) -> str:
    """Untrusted text as one safe line: printable characters only, cut to `width`."""
    flat = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return flat if len(flat) <= width else flat[: width - 1].rstrip() + "…"


def origin(found: Found, entry: Entry) -> str:
    return files.clean(f"{found.app} {shown(entry.file, 120)}")


# ── Profile ──────────────────────────────────────────────────────────────


def _profile_changes(found: Found, current: setup.Profile | None) -> list[Change]:
    out: list[Change] = []
    offered: list[tuple[FieldName, str | None]] = [
        ("user_name", found.user_name),
        ("timezone", found.timezone),
        ("agent_name", found.agent_name),
    ]
    can_save = current is not None
    for name, raw in offered:
        if raw is None:
            continue
        try:
            if name == "timezone":
                new = setup.clean(setup.Profile(user_name="x", timezone=raw)).timezone
            else:
                new = setup.clean_name(raw)
        except setup.SetupError:
            what = "a timezone" if name == "timezone" else "a name"
            out.append(
                Change(
                    field=name,
                    new=raw,
                    old=None,
                    status="unusable",
                    why=f"not {what} Trillion can use",
                )
            )
            continue
        if name == "user_name":
            can_save = True
        old: str | None = None
        if current is not None:
            held = {
                "user_name": current.user_name,
                "timezone": current.timezone,
                "agent_name": current.agent_name,
            }
            old = held[name] or None
        status: Status = "set" if old is None else ("same" if old == new else "conflict")
        out.append(Change(field=name, new=new, old=old, status=status))
    if not can_save:
        # Setup keeps nothing until it has the user's name: that comes first.
        out = [
            c
            if c.status == "unusable"
            else Change(
                field=c.field,
                new=c.new,
                old=c.old,
                status="unusable",
                why="introduce yourself in the app first, then import again",
            )
            for c in out
        ]
    return out


# ── Memories ─────────────────────────────────────────────────────────────


def held_keys(memory_root: Path) -> set[str]:
    """Every memory on disk, in any state: one the user forgot stays forgotten."""
    return {same_key(f.memory.text) for f in files.scan(memory_root).found}


def _memories(plan: Plan, held: set[str]) -> None:
    seen = set(held)
    for entry in plan.found.entries:
        body = files.clean(entry.text)
        if not body:
            continue
        if len(body) > MAX_TEXT:
            plan.too_long += 1
            continue
        key = same_key(body)
        if key in seen:
            if key in held:
                plan.held += 1
            continue
        seen.add(key)
        if len(plan.memories) >= MAX_NEW_MEMORIES:
            plan.waiting += 1
            continue
        plan.memories.append(Entry(type=entry.type, text=body, file=entry.file, as_of=entry.as_of))


# ── Skills ───────────────────────────────────────────────────────────────


def _get(data: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _names(raw: object, limit: int = 8) -> list[str]:
    items = raw if isinstance(raw, list) else [raw] if raw else []
    out: list[str] = []
    for item in items:
        name = _get(item, "name") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip():
            out.append(shown(name, 40))
    return out[:limit]


def needs(skill: Skill) -> tuple[str, ...]:
    """What the skill says it needs, as the user should see before turning it on."""
    head = header_fields(skill.source)
    out: list[str] = []
    tools = [shown(t, 40) for t in skill.allowed_tools][:8]
    tools += _names(_get(head, "metadata", "hermes", "requires_tools"))
    if tools:
        out.append("tools " + ", ".join(tools))
    bins = _names(_get(head, "metadata", "openclaw", "requires", "bins"))
    if bins:
        out.append("programs " + ", ".join(bins))
    env = _names(_get(head, "metadata", "openclaw", "requires", "env"))
    env += _names(head.get("required_environment_variables"))
    if env:
        out.append("environment " + ", ".join(dict.fromkeys(env)))
    if skill.has_scripts:
        out.append("scripts Trillion won't run")
    return tuple(out)


def _has_links(folder: Path) -> bool:
    try:
        return any(is_link(path) for _, path, _ in walk(folder))
    except (TooBig, OSError):
        return False  # parse says why


def _skills(plan: Plan, skills_root: Path) -> None:
    taken: set[str] = set()
    for folder in plan.found.skills:
        label = shown(folder.name, 40)
        found = parse(folder)
        if isinstance(found, Invalid):
            plan.skills.append(
                SkillPlan(source=folder, name=label, status="left_out", why=shown(found.reason))
            )
            continue
        name = found.name
        if _has_links(folder):
            why = "it holds links, which are never copied"
            plan.skills.append(SkillPlan(source=folder, name=name, status="left_out", why=why))
            continue
        if name in taken:
            why = "another skill in this import has the same name"
            plan.skills.append(SkillPlan(source=folder, name=name, status="left_out", why=why))
            continue
        taken.add(name)
        target = skills_root / name
        if target.exists() or is_link(target):
            mine = fingerprint(folder)
            if mine and fingerprint(target) == mine:
                plan.skills.append(SkillPlan(source=folder, name=name, status="held"))
            else:
                why = "a different skill with this name is already in Trillion's skills"
                plan.skills.append(SkillPlan(source=folder, name=name, status="left_out", why=why))
            continue
        plan.skills.append(SkillPlan(source=folder, name=name, status="copy", needs=needs(found)))


def make(found: Found, *, persona_dir: Path, memory_root: Path, skills_root: Path) -> Plan:
    current = setup.load(persona_dir)
    plan = Plan(found=found, profile=current, changes=_profile_changes(found, current))
    _memories(plan, held_keys(memory_root))
    _skills(plan, skills_root)
    return plan


# ── Showing it ───────────────────────────────────────────────────────────


def _home(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except (ValueError, RuntimeError):
        return str(path)


def _change_line(c: Change) -> str:
    label = f"  {LABEL[c.field]:<11}"
    if c.status == "unusable":
        return f"{label}{shown(c.new, 40)}  (left out: {c.why})"
    if c.status == "same":
        return f"{label}{c.new}  (already so)"
    if c.status == "set":
        return f"{label}{c.new}  (not set yet)"
    return f"{label}{c.new}  (now {c.old}: kept unless you confirm)"


def render(plan: Plan, *, applied: bool = False) -> str:
    app = TITLE[plan.found.app]
    out = [f"Importing from {app}: {_home(plan.found.root)}"]
    if not applied:
        out.append("Dry run: nothing is changed.")
    left: list[LeftOut] = plan.found.left_out
    if left:
        out += ["", "Left out"]
        out += [f"  {shown(x.name, 24):<13}{x.why}" for x in left]
    if plan.changes:
        out += ["", "About you (written through setup, like onboarding)"]
        out += [_change_line(c) for c in plan.changes]
    counts = [f"{len(plan.memories)} to add as imported claims"]
    if plan.held:
        counts.append(f"{plan.held} already held")
    if plan.too_long:
        counts.append(f"{plan.too_long} too long (over {MAX_TEXT} characters), left out")
    if plan.waiting:
        counts.append(f"{plan.waiting} more next time (at most {MAX_NEW_MEMORIES} a run)")
    out += ["", "Memories: " + "; ".join(counts)]
    out += [f"  + {m.type:<10} {shown(m.text, 56):<56}  {shown(m.file, 30)}" for m in plan.memories]
    to_copy = [s for s in plan.skills if s.status == "copy"]
    out += ["", f"Skills: {len(to_copy)} to copy, off until you turn them on in Settings → Skills"]
    for s in plan.skills:
        if s.status == "copy":
            needs = f"  needs: {'; '.join(s.needs)}" if s.needs else ""
            out.append(f"  + {s.name}{needs}")
        elif s.status == "held":
            out.append(f"  = {s.name}  already copied")
        else:
            out.append(f"  - {shown(s.name, 40)}  left out: {s.why}")
    return "\n".join(out) + "\n"


def nothing_to_do(plan: Plan) -> bool:
    return not (
        plan.memories
        or any(s.status == "copy" for s in plan.skills)
        or any(c.status in ("set", "conflict") for c in plan.changes)
    )
