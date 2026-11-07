"""Doing what the plan says (CON-8). The other app's folders are only ever read.

- Names and the timezone: through `persona.setup.save`, the privileged setup path
  (PER-2). A value the user set is replaced only when `confirm` says yes.
- Memories: through `MemoryBook.write`, file and index row (MEM-1), as imported
  claims with their origin and the date the source knew them (MEM-6a).
- Skills: copied into a staging folder, parsed again there, then moved into place.
  Never turned on.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from trillion.importers.plan import Change, Plan, SkillPlan, origin
from trillion.memory.book import MemoryBook
from trillion.memory.types import MemoryError_
from trillion.persona import setup
from trillion.skills.parse import Invalid, open_regular, parse, walk

# Asked once per value the user already set: "Replace … with …?"
Confirm = Callable[[Change], bool]


@dataclass(kw_only=True)
class Done:
    profile: list[Change] = field(default_factory=list)  # written
    kept: list[Change] = field(default_factory=list)  # a conflict left as it was
    memories: int = 0
    duplicates: int = 0
    stopped: str = ""  # why memories stopped early, if they did
    skills: list[str] = field(default_factory=list)
    skipped_skills: list[tuple[str, str]] = field(default_factory=list)


def apply_profile(plan: Plan, persona_dir: Path, confirm: Confirm | None, done: Done) -> None:
    wanted: list[Change] = []
    for c in plan.changes:
        if c.status == "set":
            wanted.append(c)
        elif c.status == "conflict":
            if confirm is not None and confirm(c):
                wanted.append(c)
            else:
                done.kept.append(c)
    if not wanted:
        return
    # Read again just before writing: what onboarding holds now is what is kept.
    current = setup.load(persona_dir)
    values = {c.field: c.new for c in wanted}
    base = current or setup.Profile(user_name=values.get("user_name", ""))
    profile = replace(base, **values)  # type: ignore[arg-type]
    setup.save(profile, persona_dir)
    done.profile = wanted


async def apply_memories(plan: Plan, book: MemoryBook, now_ms: int, done: Done) -> None:
    for entry in plan.memories:
        try:
            written = await book.write(
                type=entry.type,
                text=entry.text,
                source="imported_claim",
                moves=False,
                now_ms=now_ms,
                as_of=entry.as_of,
                origin=origin(plan.found, entry),
            )
        except MemoryError_ as e:
            done.stopped = {
                "full": "her memory is full",
                "disk": "a memory file could not be written",
            }.get(e.kind, "a memory could not be saved")
            return
        if written.duplicate:
            done.duplicates += 1
        else:
            done.memories += 1


def _copy(source: Path, staging: Path) -> None:
    """Every file of the skill, as bytes, with its executable bit. Links were refused
    by the plan; `open_regular` refuses any that appeared since."""
    for rel, path, _ in walk(source):
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open_regular(path) as src, open(target, "xb") as dst:
            shutil.copyfileobj(src, dst)
        mode = path.lstat().st_mode
        os.chmod(target, 0o700 if mode & 0o100 else 0o600)


def copy_skill(skill: SkillPlan, skills_root: Path, done: Done) -> None:
    skills_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = skills_root / skill.name
    # Staged beside the skills folder, not in it, so a half-copied skill is never listed.
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=skills_root.parent))
    try:
        _copy(skill.source, staging)
        again = parse(staging)
        if isinstance(again, Invalid) or again.name != skill.name:
            done.skipped_skills.append((skill.name, "it changed while it was copied"))
            return
        if target.exists():
            done.skipped_skills.append((skill.name, "a skill with this name arrived meanwhile"))
            return
        os.rename(staging, target)
        done.skills.append(skill.name)
    except OSError:
        done.skipped_skills.append((skill.name, "it could not be copied"))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def apply_skills(plan: Plan, skills_root: Path, done: Done) -> None:
    for skill in plan.skills:
        if skill.status == "copy":
            copy_skill(skill, skills_root, done)
