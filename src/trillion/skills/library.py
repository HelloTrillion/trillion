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
import threading
import time
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
# On Windows, replacing a file another thread has open fails for a moment.
_REPLACE_TRIES, _REPLACE_WAIT = 5, 0.05
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


def _is_folder(path: Path) -> bool:
    """A folder we can see, and not a link to one. Never raises."""
    try:
        return path.is_dir() and not is_link(path)
    except (OSError, ValueError):
        return False


def _same(a: Path, b: Path) -> bool:
    """The same folder, whatever the case of its name on a disk that ignores case."""
    if os.path.normcase(a) == os.path.normcase(b):
        return True
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def _within(path: Path, folder: Path) -> bool:
    return any(_same(p, folder) for p in (path, *path.parents))


def _show(path: Path) -> str:
    """A folder as the user knows it: their home folder as "~", which also keeps
    their account name out of the prompt."""
    try:
        home = Path.home()
    except RuntimeError:
        return str(path)
    for h in (home, home.resolve()):
        if path.is_relative_to(h):
            return str(Path("~") / path.relative_to(h))
    return str(path)


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
        self,
        settings_path: Path,
        curated_root: Path = CURATED,
        *,
        data_root: Path | None = None,
        imported_root: Path | None = None,
    ) -> None:
        self._path = settings_path
        self._curated = curated_root
        self._data = (data_root or paths.home()).expanduser().resolve()
        # Skills `trillion import` copied in (CON-8): inside Trillion's own folder, which
        # the user cannot add, so it is listed here instead.
        self._imported = imported_root or self._data / "skills"
        # Every change reads, edits and writes the whole file: one at a time.
        self._lock = threading.Lock()

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
        """Write the file whole, synced, and confirm it reads back as written."""
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=".skills-", suffix=".json", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            for attempt in range(_REPLACE_TRIES):
                try:
                    os.replace(tmp, self._path)
                    break
                except PermissionError:
                    if attempt == _REPLACE_TRIES - 1:
                        raise
                    time.sleep(_REPLACE_WAIT)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        if json.loads(self._path.read_text(encoding="utf-8")) != data:
            raise LibraryError("skills.json did not save; try again")

    # ── Finding skills ────────────────────────────────────────────────

    def folders(self) -> list[Folder]:
        return [Folder(Path(f), _is_folder(Path(f))) for f in self._read(strict=False)["folders"]]

    def _candidates(self) -> list[_Candidate]:
        """Every skill folder, by id, without reading any of them. Ids are built from
        the stored folder and the subfolder's name, never by following a link."""
        out: dict[str, _Candidate] = {}
        for sub in _subfolders(self._curated):
            out.setdefault(f"curated:{sub.name}", (f"curated:{sub.name}", "curated", sub, None))
        if _is_folder(self._imported):
            for sub in _subfolders(self._imported):
                sid = f"folder:{sub}"
                out.setdefault(sid, (sid, "folder", sub, self._imported))
        for f in self.folders():
            if not f.found:
                continue
            subs = [f.path] if _has_skill_md(f.path) else _subfolders(f.path)
            for sub in subs:
                out.setdefault(f"folder:{sub}", (f"folder:{sub}", "folder", sub, f.path))
        return list(out.values())

    def _entry(
        self, sid: str, source: Source, folder: Path, root: Path | None, approved: str | None
    ) -> Entry:
        """Read one skill. The parse sits between two fingerprints, the second over the
        SKILL.md bytes the parse read, so the instructions are the bytes the fingerprint
        covers, and its file listing must be the one the second fingerprint walked:
        files changing meanwhile never count as on. An invalid skill is never hashed."""

        def invalid(reason: str) -> Entry:
            return Entry(
                id=sid,
                source=source,
                folder=folder,
                root=root,
                skill=None,
                reason=reason,
                fingerprint="",
                state="invalid",
            )

        found = parse(folder)  # first, so an invalid skill is never hashed
        for _ in range(2):
            if isinstance(found, Invalid):
                return invalid(found.reason)
            before = fingerprint(folder)
            found = parse(folder)
            if isinstance(found, Invalid):
                return invalid(found.reason)
            skill = found
            # The SKILL.md bytes that were parsed, not whatever is on disk by now.
            walked: set[str] = set()
            mark = fingerprint(folder, override={SKILL_MD: skill.source}, walked=walked)
            # A file made and removed between the two hashes could be in the parse's
            # listing, which skill_open shows, but in neither hash. ("" hashed nothing,
            # and is never on.)
            listed = {f.path for f in skill.files} | {SKILL_MD}
            stable = mark == before and (not mark or listed == walked)
            if stable:
                break
        # Else it kept changing: never "on".
        if not stable and approved is None:
            return invalid("its files are being changed; try again")
        state: State
        if approved is None:
            state = "off"
        else:
            state = "on" if stable and mark and approved == mark else "changed"
        return Entry(
            id=sid,
            source=source,
            folder=folder,
            root=root,
            skill=skill,
            reason="",
            fingerprint=mark,
            state=state,
        )

    def turned_on(self) -> list[Entry]:
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
        on = [e for e in self.turned_on() if e.state == "on"]
        return sorted(on, key=lambda e: (e.name, e.id))

    def lookup(self, ref: str) -> Entry:
        """A skill that is turned on, by id, or by a name only one skill that is on has.
        By id it may be "changed" or "invalid": the caller must refuse anything not "on".
        When no skill that is on has the name, one whose files changed is returned with
        state "changed", for the caller to refuse."""
        ref = ref.strip()
        turned = self.turned_on()
        for e in turned:
            if e.id == ref:
                return e
        named = [e for e in turned if e.skill is not None and e.skill.name == ref]
        found = [e for e in named if e.state == "on"] or named
        if not found:
            raise LibraryError(f"{ref!r} is not turned on, or there is no such skill")
        if len(found) > 1:
            raise LibraryError(
                f"more than one skill is called {ref!r}; use its id: "
                + ", ".join(e.id for e in found)
            )
        return found[0]

    def prompt_lines(self, entries: list[Entry] | None = None) -> list[tuple[str, str]]:
        """(label, description) for each skill that is on; `entries` is enabled() if the
        caller already has it. A name two skills share is labelled with where each came
        from: the user's folder, or the skill's own when that folder has both."""
        on = self.enabled() if entries is None else entries
        out: list[tuple[str, str]] = []
        for e in on:
            label = e.name
            same = [o for o in on if o.name == e.name]
            if len(same) > 1 and e.root is not None:
                shared = sum(1 for o in same if o.root == e.root) > 1
                where = " ".join(_show(e.folder if shared else e.root).split())  # one line
                label = f"{e.name} (from {where})"
            out.append((label, e.skill.description if e.skill else ""))
        return out

    # ── Changing the user's choices ───────────────────────────────────

    def turn_on(self, sid: str, expected: str | None = None) -> Entry:
        """Record the skill's fingerprint. `expected` is the fingerprint the user was
        shown: if the files changed since, they haven't seen what they'd approve."""
        with self._lock:
            return self._turn_on(sid, expected)

    def _turn_on(self, sid: str, expected: str | None) -> Entry:
        data = self._read(strict=True)
        match = [c for c in self._candidates() if c[0] == sid]
        if not match:
            raise LibraryError("there is no such skill")
        entry = self._entry(*match[0], None)
        if entry.state == "invalid":
            raise LibraryError(f"that skill can't be used: {entry.reason}")
        if not entry.fingerprint:  # it grew too big between the parse and the hash
            raise LibraryError("that skill's files could not be read; try again")
        if expected is not None and entry.fingerprint != expected:
            raise LibraryError("that skill changed since this page showed it; look again")
        data["enabled"][sid] = entry.fingerprint
        self._write(data)
        return self._entry(*match[0], entry.fingerprint)

    def turn_off(self, sid: str) -> None:
        with self._lock:
            self._turn_off(sid)

    def _turn_off(self, sid: str) -> None:
        data = self._read(strict=True)
        if data["enabled"].pop(sid, None) is not None:
            self._write(data)

    @staticmethod
    def _resolve(raw: str) -> Path:
        # Windows doesn't refuse a NUL the way POSIX path calls do: refuse it here.
        if "\x00" in raw:
            raise LibraryError("that is not a folder on this computer")
        try:
            return Path(raw.strip()).expanduser().resolve()
        except (OSError, ValueError, RuntimeError):
            raise LibraryError("that is not a folder on this computer") from None

    def add_folder(self, raw: str) -> Path:
        with self._lock:
            return self._add_folder(raw)

    def _add_folder(self, raw: str) -> Path:
        # A link (~/.claude/skills often is one) is stored as the folder it points to.
        path = self._resolve(raw)
        if not _is_folder(path):
            raise LibraryError("that is not a folder on this computer")
        if path == Path(path.anchor):
            raise LibraryError("a whole drive is too much; choose the folder your skills are in")
        # Inside Trillion's folder, or holding it (as a home folder does), in any case.
        if _within(path, self._data) or _within(self._data, path):
            raise LibraryError("that holds Trillion's own files; choose another folder")
        data = self._read(strict=True)
        if not any(_same(Path(f), path) for f in data["folders"]):
            data["folders"].append(str(path))
            self._write(data)
        return path

    def remove_folder(self, raw: str) -> None:
        """Forget a folder and its skills' choices: the folder itself, or its direct
        subfolders, as the library only offers those. A skill another folder still
        offers keeps its choice."""
        with self._lock:
            self._remove_folder(raw)

    def _remove_folder(self, raw: str) -> None:
        path = self._resolve(raw)
        data = self._read(strict=True)
        gone = [f for f in data["folders"] if _same(Path(f), path)]
        kept = [f for f in data["folders"] if f not in gone]

        def under(sid: str, folder: str) -> bool:
            if not sid.startswith("folder:"):
                return False
            skill = Path(sid.removeprefix("folder:"))
            return _same(skill, Path(folder)) or _same(skill.parent, Path(folder))

        data["folders"] = kept
        data["enabled"] = {
            k: v
            for k, v in data["enabled"].items()
            if not any(under(k, g) for g in gone) or any(under(k, f) for f in kept)
        }
        self._write(data)

    def problems(self) -> list[str]:
        """What keeps skills the user turned on from being used, in plain words: a
        settings file that can't be read, and skills turned on that are no longer found
        (a folder gone, moved or unreadable). For doctor and Settings."""
        try:
            lost = self.lost()
        except LibraryError as e:
            return [str(e)]
        return [
            f"turned on but not found: {' '.join(sid.partition(':')[2].split())}" for sid in lost
        ]

    def lost(self) -> list[str]:
        """The ids of skills turned on that are no longer found, sorted. Raises
        LibraryError when skills.json can't be read."""
        data = self._read(strict=True)
        found = {c[0] for c in self._candidates()}
        return [sid for sid in sorted(data["enabled"]) if sid not in found]

    def fact(self, entries: list[Entry] | None = None) -> str:
        """For describe_self (BRN-6). `entries` is enabled() if the caller has it."""
        on = self.enabled() if entries is None else entries
        if not on:
            return "none turned on"
        return (
            f"{len(on)} turned on: {', '.join(e.name for e in on)}. skill_open reads one; "
            "skill_file reads a file in it. Their scripts are never run"
        )
