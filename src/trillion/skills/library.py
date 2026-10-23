"""Where skills come from, which are on, and whether they changed since (CON-6).

Skills come from the curated set shipped in this package and from folders the user
added. A folder is a skill if it has a SKILL.md; otherwise each direct subfolder that
has one is. The user's choices are in skills.json: the folders, and for each skill
turned on, the fingerprint they approved. A skill whose files no longer match is
"changed", and counts as off until the user turns it on again.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Literal

from trillion import paths
from trillion.skills.fingerprint import fingerprint
from trillion.skills.parse import SKILL_MD, Invalid, Skill, is_link, parse

CURATED = Path(__file__).resolve().parent / "curated"
# A folder of skills is read one level down. These keep a big folder added by
# mistake (a home folder, a project) from being read entry by entry.
MAX_LISTED = 5000
MAX_SUBFOLDERS = 500
State = Literal["off", "on", "changed", "invalid"]
Source = Literal["curated", "folder"]
_Candidate = tuple[str, Source, Path, Path | None]


class LibraryError(ValueError):
    """Something the user asked for cannot be done. The message is safe to show."""


@dataclass(frozen=True, kw_only=True)
class Entry:
    id: str
    source: Source
    folder: Path  # the skill's own folder
    root: Path | None  # the user's folder it was found in; None when curated
    skill: Skill | None
    reason: str  # why it is invalid, else ""
    fingerprint: str  # "" when invalid: an invalid skill is never hashed
    state: State

    @property
    def name(self) -> str:
        return self.skill.name if self.skill else self.folder.name


@dataclass(frozen=True)
class Folder:
    path: Path
    found: bool


def _has_skill_md(folder: Path) -> bool:
    try:
        return (folder / SKILL_MD).exists()
    except OSError:  # a folder we may not look in
        return False


def _subfolders(root: Path) -> list[Path]:
    """The direct subfolders of `root` that hold a SKILL.md, links never followed. A
    folder that can't be listed has none."""
    subs: list[Path] = []
    try:
        with os.scandir(root) as it:
            for item in islice(it, MAX_LISTED):
                path = Path(item.path)
                if item.is_dir(follow_symlinks=False) and not is_link(path):
                    subs.append(path)
    except OSError:
        return []
    return [s for s in sorted(subs)[:MAX_SUBFOLDERS] if _has_skill_md(s)]


class Library:
    def __init__(
        self, settings_path: Path, curated_root: Path = CURATED, *, data_root: Path | None = None
    ) -> None:
        self._path = settings_path
        self._curated = curated_root
        self._data = (data_root or paths.home()).expanduser().resolve()

    # ── The settings file ─────────────────────────────────────────────

    def _read(self, *, strict: bool) -> dict[str, Any]:
        """The user's choices. A file that can't be read counts as empty, but `strict`
        refuses it, so a change never overwrites what the user may want to fix."""
        empty: dict[str, Any] = {"folders": [], "enabled": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty
        except (OSError, ValueError):
            if strict:
                raise LibraryError(
                    "skills.json could not be read; fix or remove it first"
                ) from None
            return empty
        folders, enabled = (
            (raw.get("folders"), raw.get("enabled")) if isinstance(raw, dict) else (None, None)
        )
        if not isinstance(folders, list) or not isinstance(enabled, dict):
            if strict:
                raise LibraryError("skills.json is not in the right shape; fix or remove it")
            return empty
        return {
            "folders": [f for f in folders if isinstance(f, str)],
            "enabled": {str(k): v for k, v in enabled.items() if isinstance(v, str)},
        }

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".skills-", suffix=".json", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # ── Finding skills ────────────────────────────────────────────────

    def folders(self) -> list[Folder]:
        return [Folder(Path(f), Path(f).is_dir()) for f in self._read(strict=False)["folders"]]

    def _candidates(self) -> list[_Candidate]:
        """Every skill folder, by id, without reading any of them."""
        out: dict[str, _Candidate] = {}
        for sub in _subfolders(self._curated):
            out.setdefault(f"curated:{sub.name}", (f"curated:{sub.name}", "curated", sub, None))
        for f in self.folders():
            if not f.found:
                continue
            subs = [f.path] if _has_skill_md(f.path) else _subfolders(f.path)
            for sub in subs:
                sid = f"folder:{sub.resolve()}"
                out.setdefault(sid, (sid, "folder", sub.resolve(), f.path))
        return list(out.values())

    def _entry(
        self, sid: str, source: Source, folder: Path, root: Path | None, approved: str | None
    ) -> Entry:
        found = parse(folder)
        if isinstance(found, Invalid):
            return Entry(
                id=sid,
                source=source,
                folder=folder,
                root=root,
                skill=None,
                reason=found.reason,
                fingerprint="",
                state="invalid",
            )
        mark = fingerprint(folder)
        state: State
        if approved is None:
            state = "off"
        else:
            state = "on" if approved == mark else "changed"
        return Entry(
            id=sid,
            source=source,
            folder=folder,
            root=root,
            skill=found,
            reason="",
            fingerprint=mark,
            state=state,
        )

    def _turned_on(self) -> list[Entry]:
        """Every skill the user turned on, in any state. Only those are read."""
        on = self._read(strict=False)["enabled"]
        if not on:
            return []
        return [self._entry(*c, on[c[0]]) for c in self._candidates() if c[0] in on]

    def scan(self) -> list[Entry]:
        """Every skill, read and fingerprinted, for Settings."""
        enabled = self._read(strict=False)["enabled"]
        return [self._entry(*c, enabled.get(c[0])) for c in self._candidates()]

    def enabled(self) -> list[Entry]:
        """The skills that are on and unchanged, by name, then id."""
        on = [e for e in self._turned_on() if e.state == "on"]
        return sorted(on, key=lambda e: (e.name, e.id))

    def lookup(self, ref: str) -> Entry:
        """A skill that is on, by id or by a name only one skill that is on has. A skill
        whose files changed is returned with state "changed", for the caller to refuse."""
        ref = ref.strip()
        found = [
            e
            for e in self._turned_on()
            if e.id == ref or (e.skill is not None and e.skill.name == ref)
        ]
        if not found:
            raise LibraryError(f"{ref!r} is not turned on, or there is no such skill")
        if len(found) > 1:
            raise LibraryError(
                f"more than one skill is called {ref!r}; use its id: "
                + ", ".join(e.id for e in found)
            )
        return found[0]

    def prompt_lines(self) -> list[tuple[str, str]]:
        """(label, description) for each skill that is on. A name two skills share is
        labelled with the folder each came from."""
        on = self.enabled()
        names = [e.name for e in on]
        out: list[tuple[str, str]] = []
        for e in on:
            label = e.name
            if names.count(e.name) > 1 and e.root is not None:
                label = f"{e.name} (from {e.root})"
            out.append((label, e.skill.description if e.skill else ""))
        return out

    # ── Changing the user's choices ───────────────────────────────────

    def turn_on(self, sid: str) -> Entry:
        data = self._read(strict=True)
        match = [c for c in self._candidates() if c[0] == sid]
        if not match:
            raise LibraryError("there is no such skill")
        entry = self._entry(*match[0], None)
        if entry.state == "invalid":
            raise LibraryError(f"that skill can't be used: {entry.reason}")
        if not entry.fingerprint:  # it grew too big between the parse and the hash
            raise LibraryError("that skill's files could not be read; try again")
        data["enabled"][sid] = entry.fingerprint
        self._write(data)
        return self._entry(*match[0], entry.fingerprint)

    def turn_off(self, sid: str) -> None:
        data = self._read(strict=True)
        if data["enabled"].pop(sid, None) is not None:
            self._write(data)

    def add_folder(self, raw: str) -> Path:
        path = Path(raw.strip()).expanduser()
        if not path.is_dir():
            raise LibraryError("that is not a folder on this computer")
        path = path.resolve()
        if path == Path(path.anchor):
            raise LibraryError("a whole drive is too much; choose the folder your skills are in")
        # Inside Trillion's folder, or holding it (as a home folder does).
        if path.is_relative_to(self._data) or self._data.is_relative_to(path):
            raise LibraryError("that holds Trillion's own files; choose another folder")
        data = self._read(strict=True)
        if str(path) not in data["folders"]:
            data["folders"].append(str(path))
            self._write(data)
        return path

    def remove_folder(self, raw: str) -> None:
        data = self._read(strict=True)
        path = str(Path(raw.strip()).expanduser().resolve())
        data["folders"] = [f for f in data["folders"] if f != path]
        prefix = f"folder:{path}"
        data["enabled"] = {
            k: v
            for k, v in data["enabled"].items()
            if not (k == prefix or k.startswith(prefix + os.sep))
        }
        self._write(data)

    def fact(self) -> str:
        """For describe_self (BRN-6)."""
        on = self.enabled()
        if not on:
            return "none turned on"
        return (
            f"{len(on)} turned on: {', '.join(e.name for e in on)}. skill_open reads one; "
            "skill_file reads a file in it. Their scripts are never run"
        )
