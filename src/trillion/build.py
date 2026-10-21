"""Which build is running, and whether it is the one on disk (ONB-11).

A fix saved to disk is not a fix until the server running is restarted on it. So a
running server leaves a note in the data folder, `running.json`: its process, port,
version, and a fingerprint of its own code and page as they were when it started.
`trillion doctor` takes the same fingerprint of the files now and compares.

The note holds no secret: the session token is never written down (SPN-11a).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from trillion import __version__, paths

RUNNING = "running.json"
# What the server is made of: its code, its prompts and its migrations. The page is
# its index.html, which names every bundle it loads by a hash of its contents.
_SOURCES = frozenset({".py", ".md", ".sql"})


def fingerprint(web: Path | None) -> str:
    """A short hash of the code and the page this build would run."""
    digest = hashlib.sha256()
    root = paths.package_root()
    found = (
        f
        for f in root.rglob("*")
        if f.suffix in _SOURCES and "__pycache__" not in f.parts and f.is_file()
    )
    for f in sorted(found):
        digest.update(f.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(f.read_bytes())
    page = web / "index.html" if web is not None else None
    if page is not None and page.is_file():
        digest.update(b"page\0")
        digest.update(page.read_bytes())
    return digest.hexdigest()[:16]


@dataclass(frozen=True, kw_only=True)
class Running:
    pid: int
    port: int
    version: str
    fingerprint: str
    started_at: int


def announce(port: int, web: Path | None, home: Path | None = None) -> Running:
    """Write the note for this process. Replaces one a crashed server left."""
    folder = home or paths.home()
    note = Running(
        pid=os.getpid(),
        port=port,
        version=__version__,
        fingerprint=fingerprint(web),
        started_at=time.time_ns() // 1_000_000,
    )
    target = folder / RUNNING
    temporary = folder / f".{RUNNING}.{note.pid}.tmp"
    temporary.write_text(json.dumps(asdict(note)), encoding="utf-8")
    os.replace(temporary, target)
    return note


def withdraw(home: Path | None = None) -> None:
    """Remove the note, if it is still this process's own."""
    target = (home or paths.home()) / RUNNING
    found = read(target.parent)
    if found is not None and found.pid == os.getpid():
        with contextlib.suppress(OSError):
            target.unlink()


def read(home: Path | None = None) -> Running | None:
    """The note a server left, or None if there is none or it does not make sense."""
    try:
        raw: Any = json.loads(((home or paths.home()) / RUNNING).read_text(encoding="utf-8"))
        return Running(
            pid=int(raw["pid"]),
            port=int(raw["port"]),
            version=str(raw["version"]),
            fingerprint=str(raw["fingerprint"]),
            started_at=int(raw["started_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def alive(pid: int) -> bool:
    """Whether a process with this id exists. Unknown on Windows, where asking the
    same way would end it: the caller checks the port as well."""
    if os.name != "posix":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
