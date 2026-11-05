"""The helper list (the helpers panel spec, The core), built fresh from what already
exists: the scheduler's schedules and runs, the watchers' state, and the spend desk's
paused kinds. It reads; it never writes a helper's state.

Routines and timed asks are schedules of kind ask, with a target or without. A plain
reminder runs no model and is no helper. A one-off ask that has fired is not active,
so it is not listed.

A helper's state, first that holds: needs you (something of it waits on the user),
working (a run or check under way), paused (the user paused a routine, or turned a
watcher off; a timed ask can't be paused, only cancelled), capped (its kind of work is
paused at the background model's spend cap), idle.

Reasons are told in plain words, never a class name.

The order: needs you, working by start, then the rest by next run, unknown last.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Protocol

from trillion.attention.runner import WatcherStatus
from trillion.attention.settings import TITLED
from trillion.helpers.model import Helper, HelperState, LastRun, Outcome
from trillion.scheduler.runner import BUSY_DETAIL
from trillion.scheduler.store import Run, RunStatus, Schedule
from trillion.scheduler.when import WhenError, summary
from trillion.spend.types import Work

# A routine's job line, from its ask: its first line, at most this long.
JOB_MAX = 80

# Each finished run status as the panel tells it. A blocked run waits to be tried
# again (no model, or a turn running in its conversation): it was not run, yet.
_OUTCOMES: dict[RunStatus, Outcome] = {
    "delivered": "succeeded",
    "late": "succeeded",
    "nothing": "nothing",
    "skipped": "skipped",
    "blocked": "skipped",
    "cancelled": "skipped",
    "failed": "failed",
    "missed": "failed",
    "interrupted": "failed",
}

# A blocked run waits to be tried again, so its helper stays idle; why, in plain words.
_BLOCKED = {"no model": "no model set up", BUSY_DETAIL: "busy, will try again"}
_BLOCKED_ELSE = "waiting to try again"

# What each watcher reads, for "couldn't reach ...".
_WATCHER_SOURCE = {"meetings": "the calendar", "email": "the inbox", "payments": "Stripe"}
# A watcher's failure kind (a Google call's kind, or a class name) in plain words.
_UNREACHABLE = frozenset({"unreachable", "ConnectionError", "OSError", "StripeError", "server"})
_ERROR_WORDS = {
    "signed_out": "signed out of Google",
    "not_allowed": "not given access to Google",
    "api_off": "that Google API is switched off",
    "busy": "Google is busy, will try again",
    "NotConnected": "Google isn't connected",
    "AsksFirst": "Stripe is set to ask first",
    "TimeoutError": "timed out",
}
_ERROR_ELSE = "something went wrong"

# What each watcher watches, and what it is doing while it checks.
_WATCHER_JOB = {
    "meetings": "Tells you before a meeting starts",
    "email": "Watches your inbox for mail that needs you",
    "payments": "Watches Stripe for failed charges, disputes and large payments",
}
_WATCHER_DOING = {
    "meetings": "Reading your calendar",
    "email": "Reading your inbox",
    "payments": "Reading Stripe",
}


class Schedules(Protocol):
    """What the list reads from the scheduler store."""

    async def active(self, owner_id: str) -> list[Schedule]: ...

    async def last_results(self, owner_id: str) -> dict[str, Run]: ...

    async def running(self, owner_id: str, now_ms: int) -> dict[str, Run]: ...


class Sources(Protocol):
    """Everything the list is built from, so tests can pass fakes."""

    @property
    def owner(self) -> str: ...

    @property
    def schedules(self) -> Schedules: ...

    def now_ms(self) -> int: ...

    async def watchers(self) -> list[WatcherStatus]:
        """Each watcher's state (`AttentionRunner.statuses`)."""
        ...

    async def capped(self) -> Collection[Work]:
        """The kinds of background work paused now at the background model's spend
        cap (`SpendDesk.paused` for the background provider)."""
        ...


async def helpers(sources: Sources) -> list[Helper]:
    owner = sources.owner
    capped = set(await sources.capped())
    active = await sources.schedules.active(owner)
    last = await sources.schedules.last_results(owner)
    now = await sources.schedules.running(owner, sources.now_ms())
    found = [_scheduled(s, last.get(s.id), now.get(s.id), capped) for s in active]
    found += [_watcher(w, capped) for w in await sources.watchers()]
    return _ordered(h for h in found if h is not None)


def _scheduled(
    s: Schedule, last: Run | None, running: Run | None, capped: set[Work]
) -> Helper | None:
    if s.kind != "ask":
        return None
    routine = s.target is not None
    since = None
    if running is not None:
        since = running.started_ms if running.started_ms is not None else running.claimed_ms
    work: Work = "routines" if routine else "asks"
    paused = routine and s.paused_ms is not None
    state = _state(working=running is not None, paused=paused, capped=work in capped)
    return Helper(
        id=f"{'routine' if routine else 'ask'}:{s.id}",
        kind="routine" if routine else "ask",
        name=s.title,
        job=_first_line(s.body) if routine else _when(s),
        state=state,
        doing=s.title if state == "working" else None,
        since_ms=since if state == "working" else None,
        last_run=_last_run(last),
        # A paused routine's due time stays put, but it won't run then.
        next_ms=None if paused else s.next_due_ms,
        waiting=(),
        # A routine built in today's day conversation has none of its own here.
        conversation_id=s.conversation_id if routine else None,
    )


def _watcher(w: WatcherStatus, capped: set[Work]) -> Helper:
    state = _state(
        working=w.checking_since_ms is not None, paused=not w.on, capped="watchers" in capped
    )
    return Helper(
        id=f"watcher:{w.name}",
        kind="watcher",
        name=TITLED.get(w.name, w.name),
        job=_WATCHER_JOB.get(w.name, ""),
        state=state,
        doing=_WATCHER_DOING.get(w.name, "Checking") if state == "working" else None,
        since_ms=w.checking_since_ms if state == "working" else None,
        last_run=_last_check(w),
        next_ms=w.next_check_ms if w.on else None,
        waiting=(),
        conversation_id=None,
    )


def _state(*, working: bool, paused: bool, capped: bool) -> HelperState:
    # Needs you outranks everything once waiting items are read (step two of the core).
    if working:
        return "working"
    if paused:
        return "paused"
    if capped:
        return "capped"
    return "idle"


def _last_run(run: Run | None) -> LastRun | None:
    if run is None:
        return None
    at = run.finished_ms or run.started_ms or run.claimed_ms or run.due_ms
    outcome = _OUTCOMES.get(run.status, "failed")
    reason = run.detail if outcome in ("failed", "skipped") else None
    if run.status == "blocked":
        reason = _BLOCKED.get(run.detail or "", _BLOCKED_ELSE)
    return LastRun(at_ms=at, outcome=outcome, reason=reason)


def _last_check(w: WatcherStatus) -> LastRun | None:
    if w.last_error is not None:
        at = w.last_error_ms if w.last_error_ms is not None else w.last_check_ms
        if at is None:
            return None
        return LastRun(at_ms=at, outcome="failed", reason=_error_words(w.name, w.last_error))
    if w.last_check_ms is None:
        return None
    return LastRun(at_ms=w.last_check_ms, outcome="succeeded", reason=None)


def _error_words(watcher: str, kind: str) -> str:
    if kind in _UNREACHABLE:
        return f"couldn't reach {_WATCHER_SOURCE.get(watcher, 'its source')}"
    return _ERROR_WORDS.get(kind, _ERROR_ELSE)


def _first_line(text: str) -> str:
    line = next((x for x in text.splitlines() if x.strip()), "")
    line = " ".join(line.split())
    return line if len(line) <= JOB_MAX else line[: JOB_MAX - 1] + "…"


def _when(s: Schedule) -> str:
    """When a timed ask fires: "Thu 5 Nov at 4:00 pm", or "Every day at 4:00 pm"."""
    try:
        return summary(s.at_local, s.repeat, s.zone)
    except (WhenError, ValueError):
        return ""


_NEVER = float("inf")


def _ordered(found: Iterable[Helper]) -> list[Helper]:
    """Needs you first, then working by start, then the rest by next run, unknown
    last. Ties keep the order they were read in."""

    def key(h: Helper) -> tuple[int, float]:
        if h.state == "needs_you":
            # Oldest first, once what each waits on carries its time.
            return (0, _NEVER if h.since_ms is None else h.since_ms)
        if h.state == "working":
            return (1, _NEVER if h.since_ms is None else h.since_ms)
        return (2, _NEVER if h.next_ms is None else h.next_ms)

    return sorted(found, key=key)
