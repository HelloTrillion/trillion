"""`trillion import openclaw|hermes`: a dry run unless `--apply` (CON-8).

This runs on the user's own command line, outside every model-facing path, so it
may use the privileged setup path for names (PER-2). It reads the other app's
folders and nothing on the network.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from trillion import paths
from trillion.importers import apply, plan, sources
from trillion.persona.setup import SetupError

# The one local owner, as in the runtime.
OWNER = "local"


def _asker(stdin: TextIO, out: TextIO) -> Callable[[plan.Change], bool] | None:
    """A yes/no question on the terminal, or None when nobody is there to answer."""
    if not stdin.isatty():
        return None

    def confirm(c: plan.Change) -> bool:
        out.write(f"Replace {plan.LABEL[c.field]} {c.old!r} with {c.new!r}? [y/N] ")
        out.flush()
        return stdin.readline().strip().lower() in {"y", "yes"}

    return confirm


async def run(
    app: sources.App,
    source: Path | None,
    *,
    do: bool,
    replace_profile: bool,
    out: TextIO | None = None,
    stdin: TextIO | None = None,
    now_ms: int | None = None,
) -> int:
    out = out or sys.stdout
    stdin = stdin or sys.stdin
    now = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        found = sources.read(app, source, now)
    except sources.SourceError as e:
        sys.stderr.write(f"trillion: {e}\n")
        return 1
    the_plan = plan.make(
        found,
        persona_dir=paths.persona_dir(),
        memory_root=paths.memory_dir(),
        skills_root=paths.skills_dir(),
    )
    out.write(plan.render(the_plan, applied=do))
    if not do:
        out.write("\nRun again with --apply to import.\n")
        return 0
    if plan.nothing_to_do(the_plan):
        out.write("\nNothing new to import.\n")
        return 0
    out.write("\n")
    confirm = (lambda c: True) if replace_profile else _asker(stdin, out)
    done = apply.Done()
    try:
        apply.apply_profile(the_plan, paths.persona_dir(), confirm, done)
    except (SetupError, OSError):
        out.write("Your name and timezone could not be saved; nothing else was changed.\n")
        return 1
    if the_plan.memories:
        await _memories(the_plan, now, done)
    apply.apply_skills(the_plan, paths.skills_dir(), done)
    out.write(_summary(done))
    return 0


async def _memories(the_plan: plan.Plan, now: int, done: apply.Done) -> None:
    from trillion.memory.book import MemoryBook
    from trillion.storage.db import connect
    from trillion.storage.migrate import migrate
    from trillion.storage.sqlite.memory_store import SqliteMemoryStore

    conn = await connect(paths.db_path())
    try:
        await migrate(conn)
        book = MemoryBook(SqliteMemoryStore(conn), paths.memory_dir(), OWNER)
        await book.sync(now)
        await apply.apply_memories(the_plan, book, now, done)
    finally:
        await conn.close()


def _summary(done: apply.Done) -> str:
    lines = ["Done."]
    for c in done.profile:
        lines.append(f"  {plan.LABEL[c.field]} is now {c.new}")
    for c in done.kept:
        lines.append(f"  {plan.LABEL[c.field]} kept as {c.old}")
    memories = f"  {done.memories} memories added as imported claims"
    if done.duplicates:
        memories += f" ({done.duplicates} were already held)"
    lines.append(memories)
    if done.stopped:
        lines.append(f"  stopped early: {done.stopped}")
    if done.skills:
        lines.append(
            f"  {len(done.skills)} skills copied, all off: turn them on in Settings → Skills"
        )
    for name, why in done.skipped_skills:
        lines.append(f"  skill {name} not copied: {why}")
    lines.append("If Trillion is running, restart it so she picks these up.")
    return "\n".join(lines) + "\n"
