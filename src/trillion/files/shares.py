"""The folders the user shared with her, kept in folders.json (the shared folders spec).

Each share has a name, a resolved path, an access (`read` or `change`) and a scope
(`everyone`, or one routine). The file is read on every call, so a change applies
from the next tool call with no restart, and written whole: a temporary file, a
rename, then a read back (OPS-2). This module is the only one that reads or writes
it (scripts/check_one_writer.py).

A damaged file is never overwritten: no saved shares apply until the user fixes or
removes it, because a guess at what it meant could share more than they chose.
Shares from `--grant` live only for the run and are never written; they still apply
while the file is damaged, since they were never in it.

The lock is per instance: the runtime owns exactly one `Shares`, so every change in
the process goes through one lock.

Looking at a share's folder touches the disk, and a share on a hung network drive
would stall whatever looks. So what the shares reach is kept as a view: `known*` read
it without touching the disk, for code on the event loop; `refresh` (in a thread)
looks again when the file changed or the view is a moment old; every change through
this store makes a fresh view before it returns, so narrowing applies to the very
next call. That look runs while the change holds the lock, so it waits on a slow
drive: callers on the event loop (the Settings routes, her tools) run every change
in a thread.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import string
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from trillion import paths
from trillion.files.rules import resolved, why_not
from trillion.spine.card import hidden_marks
from trillion.spine.deny_floor import CASE_BLIND, fold

Access = Literal["read", "change"]
ACCESS: tuple[Access, ...] = ("read", "change")
EVERYONE = "everyone"
VERSION = 1
MAX_NAME = 60
_ROUTINE = re.compile(r"routine:[A-Za-z0-9_-]{1,64}")
_ID_LETTERS = string.ascii_lowercase + string.digits
# How long a view of the shares' folders is trusted before `refresh` looks again.
VIEW_FOR_S = 2.0
# On Windows, replacing a file another thread has open fails for a moment.
_REPLACE_TRIES, _REPLACE_WAIT = 5, 0.05


class ShareError(ValueError):
    """A share that cannot be made or changed. The message is safe to show the user."""


class NoSuchShare(ShareError):
    """No share has that id."""


class WidensError(ShareError):
    """A change asked to narrow a share would let it reach more."""


@dataclass(frozen=True, kw_only=True)
class Share:
    id: str
    name: str
    path: Path  # resolved, as the deny floor spells it
    access: Access
    scope: str  # "everyone" or "routine:<id>"
    added_ms: int
    run_only: bool = False  # from `--grant`: listed for this run, never written


@dataclass(frozen=True)
class _View:
    """The shares, and those whose folder is there to reach, as last looked at."""

    stamp: tuple[int, int] | None  # the file's (mtime_ns, size), None when there is none
    at: float  # when the look that made it began
    shares: tuple[Share, ...]
    reaching: tuple[Share, ...]


def _in(scope: str, share: Share) -> bool:
    return share.scope in (EVERYONE, scope)


def routine_scope(routine_id: str) -> str:
    return f"routine:{routine_id}"


def _valid_scope(scope: str) -> bool:
    return scope == EVERYONE or _ROUTINE.fullmatch(scope) is not None


def widens(old: Share | None, *, access: str, scope: str) -> bool:
    """Whether making `old` into this reaches more than it does now. A new share, more
    access, or a scope that is not the same or narrower all widen; only narrowing may
    happen without the user's tap (D3)."""
    # Saving a `--grant` folder keeps it past this run: that reaches more too.
    if old is None or old.run_only or (access == "change" and old.access == "read"):
        return True
    # Everyone to one routine narrows; one routine to another reaches a new one.
    return scope != old.scope and not (old.scope == EVERYONE and scope != EVERYONE)


def _key(path: Path) -> str:
    """One folder however it is spelled, compared the way this system's disks do."""
    return fold(str(path)) if CASE_BLIND else str(path)


# The zero-width joiner and non-joiner: names that differ only by one are one name.
_ZWJ = "\u200d"
_ZWNJ = "\u200c"


def _clean_name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > MAX_NAME:
        raise ShareError(f"a folder's name must be 1 to {MAX_NAME} characters")
    # Newlines and other control characters would break her prompt's list of shares.
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in name):
        raise ShareError("a folder's name must be one line")
    # Invisible format characters (right-to-left overrides, zero-width spaces) could
    # make one name look like another on the card.
    if hidden_marks(name):
        raise ShareError("a folder's name can't have invisible characters")
    return name


def _name_key(name: str) -> str:
    """Names that read the same are the same name: full-width, long s, any case, and
    with or without a zero-width joiner or non-joiner."""
    return fold(name.replace(_ZWNJ, "").replace(_ZWJ, ""))


def _row(share: Share) -> dict[str, Any]:
    return {
        "id": share.id,
        "name": share.name,
        "path": str(share.path),
        "access": share.access,
        "scope": share.scope,
        "added_ms": share.added_ms,
    }


def _parse(raw: object) -> list[Share] | None:
    """The shares in a file, or None when any part of it is not as written here."""
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        return None
    rows = raw.get("shares")
    if not isinstance(rows, list):
        return None
    out: list[Share] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        sid, name, path, access, scope, added = (
            row.get(k) for k in ("id", "name", "path", "access", "scope", "added_ms")
        )
        if not (
            isinstance(sid, str)
            and sid
            and isinstance(name, str)
            and isinstance(path, str)
            and Path(path).is_absolute()
            and access in ACCESS
            and isinstance(scope, str)
            and _valid_scope(scope)
            and isinstance(added, int)
            and not isinstance(added, bool)
        ):
            return None
        try:
            name = _clean_name(name)
        except ShareError:
            return None
        out.append(
            Share(
                id=sid,
                name=name,
                path=Path(path),
                access=cast(Access, access),
                scope=scope,
                added_ms=added,
            )
        )
    # Two of one id, name or path: which one is meant is a guess, so none applies.
    for key in (
        lambda s: s.id,
        lambda s: _name_key(s.name),
        lambda s: _key(s.path),
    ):
        if len({key(s) for s in out}) != len(out):
            return None
    return out


class Shares:
    def __init__(
        self,
        path: Path | None = None,
        *,
        rules: Callable[[Path], str | None] = why_not,
        clock: Callable[[], int] = lambda: int(time.time() * 1000),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path if path is not None else paths.folders_config_path()
        self._rules = rules
        self._clock = clock
        self._run_only: list[Share] = []
        # Every change reads, edits and writes the whole file: one at a time.
        self._lock = threading.RLock()
        self._monotonic = monotonic
        # Read without the lock by code that must not wait (`known`): replaced whole.
        self._view: _View | None = None
        # Counts changes, so a slow look begun before one never replaces its view.
        self._changes = 0
        # Told after every change, and when a look finds the file changed on disk, so
        # open pages show the shares as they now are. Called from whatever thread made
        # the change: it must only hand the news on, never wait.
        self.on_change: Callable[[], None] | None = None

    # ── The file ──────────────────────────────────────────────────────

    def _load(self) -> tuple[list[Share], str | None]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return [], None
        except OSError:
            return [], self._damaged()
        try:
            # Bytes that aren't UTF-8 are a ValueError; nesting deep enough to overflow
            # the parser is a RecursionError. Both are damage, never a crash of the turn.
            shares = _parse(json.loads(raw.decode("utf-8")))
        except (ValueError, RecursionError):
            shares = None
        return (shares, None) if shares is not None else ([], self._damaged())

    def _damaged(self) -> str:
        return (
            f"Your shared folders file is damaged, so no folders are shared. It is at {self.path}."
        )

    def _saved(self) -> list[Share]:
        """The shares in the file, for a change; a damaged file refuses every change."""
        shares, problem = self._load()
        if problem is not None:
            raise ShareError(problem)
        return shares

    def _write(self, shares: list[Share]) -> None:
        """Write the file whole, synced, and confirm it reads back as written."""
        data = {"version": VERSION, "shares": [_row(s) for s in shares]}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=".folders-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            for attempt in range(_REPLACE_TRIES):
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:
                    if attempt == _REPLACE_TRIES - 1:
                        raise
                    time.sleep(_REPLACE_WAIT)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        if json.loads(self.path.read_text(encoding="utf-8")) != data:
            raise ShareError("your shared folders did not save; try again")

    # ── Reading ───────────────────────────────────────────────────────

    def problem(self) -> str | None:
        """Why no shares apply, naming the file, or None when the file is fine."""
        return self._load()[1]

    def all(self) -> list[Share]:
        """Saved shares, then this run's. A saved share of a granted folder wins, and a
        granted folder whose name a saved share has since taken is numbered. That
        renaming sticks: reading the list can change a run-only share's name."""
        saved = self._load()[0]
        held = {_key(s.path) for s in saved}
        with self._lock:
            named = list(saved)
            for i, grant in enumerate(self._run_only):
                if _key(grant.path) in held:
                    continue
                if any(_name_key(s.name) == _name_key(grant.name) for s in named):
                    grant = replace(grant, name=self._free_name(grant.path.name, named))
                    self._run_only[i] = grant
                named.append(grant)
            return named

    def get(self, ref: str) -> Share | None:
        """A share by its id, or by its name in any case."""
        shares = self.all()
        by_id = [s for s in shares if s.id == ref]
        if by_id:
            return by_id[0]
        folded = _name_key(ref.strip())
        return next((s for s in shares if _name_key(s.name) == folded), None)

    def state(self, share: Share) -> str | None:
        """Why a share reaches nothing right now, or None when it reaches its folder.
        A missing folder is only "not found": nothing is removed for being missing."""
        # A folder swapped for a link would carry the share wherever the link leads.
        try:
            moved = _key(resolved(share.path)) != _key(share.path)
        except (OSError, RuntimeError, ValueError):
            moved = True
        if moved:
            return "moved or replaced by a link"
        if not share.path.is_dir():
            return "not found"
        return self._rules(share.path)

    def _stamp(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        except OSError:
            return (-1, -1)
        return st.st_mtime_ns, st.st_size

    def _look(self) -> _View:
        """Look at the file and every share's folder now, and keep what was seen unless
        a change came in meanwhile (its own view is newer). Blocking."""
        with self._lock:
            changes = self._changes
            began = self._monotonic()
        stamp = self._stamp()
        shares = tuple(self.all())
        view = _View(
            stamp=stamp,
            at=began,
            shares=shares,
            reaching=tuple(s for s in shares if self.state(s) is None),
        )
        with self._lock:
            # Two looks can end out of order: only the one begun later is kept.
            newer = self._view is None or began >= self._view.at
            if changes == self._changes and newer:
                self._view = view
        return view

    def _tell(self) -> None:
        told = self.on_change
        if told is None:
            return
        try:
            told()
        except Exception as e:
            # The change is made whatever a listener does with the news.
            logging.getLogger(__name__).warning("shares listener: %s", type(e).__name__)

    def _changed(self) -> None:
        """After a change: the view is made again before the change returns."""
        with self._lock:
            self._changes += 1
            self._look()
        self._tell()

    def refresh(self) -> None:
        """Look again if the file changed or the view is a moment old. Blocking: code on
        the event loop runs it in a thread, then reads `known`."""
        view = self._view
        stamp = self._stamp()
        if view is not None and view.stamp == stamp and self._monotonic() - view.at < VIEW_FOR_S:
            return
        self._look()
        # Changed on disk by someone else (a hand edit): our own writes made a view
        # with the new stamp already.
        if view is not None and view.stamp != stamp:
            self._tell()

    def reached(self, scope: str) -> list[Share]:
        """The shares a turn reaches, looked at now. Her own turns pass `everyone`; a
        routine's run passes its own scope and gets everyone's too. Blocking."""
        return [s for s in self._look().reaching if _in(scope, s)]

    def in_scope(self, scope: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        """The folders a turn reaches, and those of them shared read only. Blocking."""
        return _paths(self.reached(scope))

    def known_all(self) -> list[Share]:
        """Every share as last looked at, with no disk work. Empty before any look."""
        view = self._view
        return list(view.shares) if view is not None else []

    def known_reached(self, scope: str) -> list[Share]:
        """`reached` as last looked at, with no disk work: for the event loop. Before
        any look it is empty, so it never reaches more than it should."""
        view = self._view
        return [s for s in view.reaching if _in(scope, s)] if view is not None else []

    def known(self, scope: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        """`in_scope` as last looked at, with no disk work."""
        return _paths(self.known_reached(scope))

    # ── Changing ──────────────────────────────────────────────────────

    def _folder(self, raw: Path) -> Path:
        given = raw.expanduser()
        if not given.is_absolute():
            raise ShareError("a folder must be given as a full path")
        try:
            path = resolved(given)
        except (OSError, RuntimeError, ValueError):
            raise ShareError("that folder was not found") from None
        if not path.exists():
            raise ShareError("that folder was not found")
        if not path.is_dir():
            raise ShareError("that is a file, not a folder")
        reason = self._rules(path)
        if reason is not None:
            raise ShareError(reason)
        return path

    def folder(self, raw_path: Path) -> Path:
        """The folder a share of `raw_path` would be of, checked as `put` checks it, or
        `ShareError` saying why not. Nothing is written: her card asks this when it is
        raised and again when it is approved. Blocking."""
        return self._folder(raw_path)

    def check_name(self, name: str, folder: Path) -> str:
        """`name` as a share of `folder` would get it, cleaned, or `ShareError` when it
        is not a name or another share has it. The share already of that folder, if
        any, does not count. Nothing is written: her card asks this before it is shown,
        so a bad name fails then, not after the tap. Blocking."""
        key = _key(folder)
        others = [s for s in self.all() if _key(s.path) != key]
        return self._unique(name, others)

    @staticmethod
    def _check(access: str, scope: str) -> None:
        if access not in ACCESS:
            raise ShareError("access must be read or change")
        if not _valid_scope(scope):
            raise ShareError("the scope must be everyone or one routine")

    def _new_id(self, taken: set[str]) -> str:
        while True:
            sid = "f_" + "".join(secrets.choice(_ID_LETTERS) for _ in range(6))
            if sid not in taken:
                return sid

    @staticmethod
    def _free_name(base: str, others: list[Share]) -> str:
        """The folder's own name, numbered when another share already has it."""
        taken = {_name_key(s.name) for s in others}
        base = _clean_name(base[:MAX_NAME])
        name, n = base, 2
        while _name_key(name) in taken:
            suffix = f" {n}"
            name, n = base[: MAX_NAME - len(suffix)] + suffix, n + 1
        return name

    @staticmethod
    def _unique(name: str, others: list[Share]) -> str:
        name = _clean_name(name)
        if any(_name_key(s.name) == _name_key(name) for s in others):
            raise ShareError(f"another shared folder is already named {name}")
        return name

    def put(
        self,
        raw_path: Path,
        *,
        name: str | None = None,
        access: Access = "read",
        scope: str = EVERYONE,
        expect: Path | None = None,
    ) -> Share:
        """Share a folder, or change its share: one path is one share, and the later
        request decides. A granted folder shared here is saved from then on. With
        `expect`, the folder must still be that one (the one her card named)."""
        self._check(access, scope)
        path = self._folder(raw_path)
        if expect is not None and _key(path) != _key(expect):
            raise ShareError("that folder changed since the card was shown, so nothing was shared")
        key = _key(path)
        with self._lock:
            saved = self._saved()
            old = next((s for s in saved if _key(s.path) == key), None)
            grant = next((s for s in self._run_only if _key(s.path) == key), None)
            mine = {x.id for x in (old, grant) if x is not None}
            others = [s for s in self.all() if s.id not in mine]
            if old is not None:
                new = replace(
                    old,
                    name=self._unique(name, others) if name is not None else old.name,
                    access=access,
                    scope=scope,
                )
                shares = [new if s is old else s for s in saved]
            else:
                if name is not None:
                    chosen = self._unique(name, others)
                elif grant is not None:
                    chosen = grant.name
                else:
                    chosen = self._free_name(path.name, others)
                new = Share(
                    id=grant.id if grant else self._new_id({s.id for s in self.all()}),
                    name=chosen,
                    path=path,
                    access=access,
                    scope=scope,
                    added_ms=self._clock(),
                )
                shares = [*saved, new]
            self._write(shares)
            if grant is not None:
                self._run_only.remove(grant)
            self._changed()
            return new

    def change(
        self,
        share_id: str,
        *,
        name: str | None = None,
        access: Access | None = None,
        scope: str | None = None,
    ) -> Share:
        """Rename a share, or change its access or who it is for. Whether the change
        widens it is the caller's question (`widens`), asked before this."""
        with self._lock:
            old = self._find(share_id)
            self._check(access or old.access, scope or old.scope)
            others = [s for s in self.all() if s.id != share_id]
            new = replace(
                old,
                name=self._unique(name, others) if name is not None else old.name,
                access=access or old.access,
                scope=scope or old.scope,
            )
            if old.run_only:
                self._run_only = [new if s is old else s for s in self._run_only]
            else:
                self._write([new if s.id == share_id else s for s in self._saved()])
            self._changed()
            return new

    def narrow(
        self, share_id: str, *, access: Access | None = None, scope: str | None = None
    ) -> Share:
        """Lower a share to `access` or give it to `scope` only, judged against the share
        as it is now, under the lock: a change made meanwhile (in Settings) is never
        widened back. Only what is asked is written; anything that would reach more
        raises `ShareError`. A `--grant` share stays this run's."""
        with self._lock:
            old = self._find(share_id)
            to_access, to_scope = access or old.access, scope or old.scope
            if widens(replace(old, run_only=False), access=to_access, scope=to_scope):
                raise WidensError(
                    "that would let Trillion reach more, so it goes on a card the user taps"
                )
            return self.change(share_id, access=access, scope=scope)

    def _find(self, share_id: str) -> Share:
        """A share to change. This run's are found even when the file is damaged; a
        saved one is not, since the file can't be written then."""
        found = next((s for s in self._run_only if s.id == share_id), None)
        if found is None:
            found = next((s for s in self._saved() if s.id == share_id), None)
        if found is None:
            raise NoSuchShare("that shared folder was not found")
        return found

    def remove(self, share_id: str) -> Share:
        with self._lock:
            old = self._find(share_id)
            if old.run_only:
                self._run_only.remove(old)
            else:
                self._write([s for s in self._saved() if s.id != share_id])
            self._changed()
            return old

    def remove_routine(self, routine_id: str) -> int:
        """Remove every share for one routine, as the routine itself is removed."""
        scope = routine_scope(routine_id)
        with self._lock:
            saved = self._saved()
            kept = [s for s in saved if s.scope != scope]
            if len(kept) != len(saved):
                self._write(kept)
                self._changed()
            return len(saved) - len(kept)

    def add_run_only(self, raw_path: Path) -> Share:
        """A `--grant` folder (D10): named after itself, `change` for everyone, for this
        run only. It obeys the same rule as any share."""
        path = self._folder(raw_path)
        key = _key(path)
        with self._lock:
            current = self.all()
            same = next((s for s in current if _key(s.path) == key), None)
            if same is not None:
                return same
            share = Share(
                id=self._new_id({s.id for s in current}),
                name=self._free_name(path.name, current),
                path=path,
                access="change",
                scope=EVERYONE,
                added_ms=self._clock(),
                run_only=True,
            )
            self._run_only.append(share)
            self._changed()
            return share


def _paths(shares: list[Share]) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    return tuple(s.path for s in shares), tuple(s.path for s in shares if s.access == "read")
