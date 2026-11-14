"""Downloads, kept apart and never opened (the her browser spec, decision 10; CMP-6).

Each task's downloads go into a folder of their own in Trillion's data folder, owner
only, with a name made safe here: no folder in it, nothing hidden, no control or
invisible characters, and nothing marked runnable. Nothing here ever opens, runs or
reads one. A file larger than `MAX_BYTES` is deleted as it lands. Saving one anywhere
else is the user's, on a card (`tools.DownloadKeep`).
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from trillion.owner_only import restrict

MAX_BYTES = 200 * 1024 * 1024
NAME_MAX = 120
_UNSAFE = re.compile(r"[\\/:*?\"<>|]")


def safe_name(suggested: str) -> str:
    """A file name nothing can misread: the last part of what the page suggested,
    with anything risky replaced, never empty and never hidden."""
    name = suggested.replace("\\", "/").rsplit("/", 1)[-1]
    kept = []
    for c in name:
        cat = unicodedata.category(c)
        if cat.startswith("C") or cat in ("Zl", "Zp"):
            continue
        kept.append("_" if _UNSAFE.match(c) else c)
    name = "".join(kept).strip().lstrip(".")
    if not name:
        name = "download"
    if len(name) > NAME_MAX:
        stem, dot, ext = name.rpartition(".")
        ext = ext[:16] if dot else ""
        name = (stem if dot else name)[: NAME_MAX - len(ext) - 1] + (f".{ext}" if ext else "")
    return name


@dataclass(frozen=True, kw_only=True)
class Kept:
    id: str
    task_id: str
    name: str
    origin: str | None
    size: int
    kind: str
    path: Path

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "name": self.name,
            "origin": self.origin,
            "size": self.size,
            "type": self.kind,
        }


class Quarantine:
    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, task_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{8,64}", task_id):
            raise ValueError("not a task id")
        path = self.root / task_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        restrict(self.root)
        return path

    def place(self, task_id: str, suggested: str) -> tuple[str, Path]:
        """Where a new download goes: a fresh id and a path no other file has."""
        file_id = secrets.token_hex(8)
        folder = self.folder(task_id) / file_id
        folder.mkdir(mode=0o700)
        return file_id, folder / safe_name(suggested)

    def settle(self, path: Path) -> int | None:
        """Make a landed file owner-only and never runnable; its size, or None when it
        was too big and is gone."""
        size = path.stat().st_size
        if size > MAX_BYTES:
            shutil.rmtree(path.parent, ignore_errors=True)
            return None
        os.chmod(path, 0o600)
        return size

    def drop(self, task_id: str) -> None:
        shutil.rmtree(self.root / task_id, ignore_errors=True)
