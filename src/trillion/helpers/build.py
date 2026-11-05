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

What waits on the user:
- An approval: a card still proposed, raised in the helper's own conversation (its
  session is that conversation). A run nobody watches can't raise a card at all (the
  router refuses one to `via="schedule"` and `via="routine"`, D5), so a card on a
  routine comes from someone talking to it there. A card from any other conversation
  is on no helper.
- A step: a watcher switched on whose source isn't connected, or is signed out.
- A question has no producer yet: it comes with step two, helpers she hands work to
  (AGT-1, AGT-2). Nor does a notice ask for a step today.

Reasons are told in plain words, never a class name.

The order: needs you by its oldest wait, working by start, then the rest by next run;
unknown times last.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Protocol

from trillion.attention.runner import WatcherStatus
from trillion.attention.settings import TITLED
from trillion.helpers.model import Helper, HelperState, LastRun, Outcome, Waiting
from trillion.scheduler.runner import BUSY_DETAIL
from trillion.scheduler.store import Run, RunStatus, Schedule
from trillion.scheduler.when import WhenError, summary
from trillion.spend.types import Work
from trillion.spine.confirm import PendingAction, Status

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

# Why a run was skipped (the scheduler's run log words) in plain words; others as they are.
_SKIPPED = {
    "capped": "paused at the spend cap",
    "paused": "paused",
    "already built": "already done today",
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

# The step a watcher switched on but not connected waits for, by what it reads.
_CONNECT = {
    "meetings": "Connect Google to use Meetings",
    "email": "Connect Google to use Urgent email",
    "payments": "Connect Stripe to use Payments",
}
_SIGN_IN = "Sign in to Google again"

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

    async def cards(self) -> Sequence[PendingAction]:
        """Cards that may still wait on the user (the spine store's proposed ones).
        Anyone else's, answered or lapsed are left out here."""
        ...


async def helpers(sources: Sources) -> list[Helper]:
    owner = sources.owner
    capped = set(await sources.capped())
    active = await sources.schedules.active(owner)
    last = await sources.schedules.last_results(owner)
    now_ms = sources.now_ms()
    now = await sources.schedules.running(owner, now_ms)
    cards = _open_cards(await sources.cards(), owner, now_ms)
    found = [_scheduled(s, last.get(s.id), now.get(s.id), capped, cards) for s in active]
    found += [_watcher(w, capped, cards) for w in await sources.watchers()]
    return _ordered(h for h in found if h is not None)


def _open_cards(
    cards: Sequence[PendingAction], owner: str, now_ms: int
) -> dict[str, list[Waiting]]:
    """Cards still waiting for a yes or no, by the conversation they were raised in,
    oldest first."""
    out: dict[str, list[Waiting]] = {}
    for a in sorted(cards, key=lambda a: a.created_at):
        if a.owner_id != owner or a.status != Status.PROPOSED or a.expires_at <= now_ms:
            continue
        out.setdefault(a.session_id, []).append(
            Waiting(type="approval", id=a.id, text=a.summary, at_ms=a.created_at)
        )
    return out


def _in(cards: dict[str, list[Waiting]], conversation_id: str | None) -> list[Waiting]:
    return list(cards.get(conversation_id, ())) if conversation_id is not None else []


def _scheduled(
    s: Schedule,
    last: Run | None,
    running: Run | None,
    capped: set[Work],
    cards: dict[str, list[Waiting]],
) -> Helper | None:
    if s.kind != "ask":
        return None
    routine = s.target is not None
    since = None
    if running is not None:
        since = running.started_ms if running.started_ms is not None else running.claimed_ms
    work: Work = "routines" if routine else "asks"
    paused = routine and s.paused_ms is not None
    # A routine built in today's day conversation has none of its own here.
    conversation = s.conversation_id if routine else None
    waiting = _in(cards, conversation)
    state = _state(
        waiting=bool(waiting),
        working=running is not None,
        paused=paused,
        capped=work in capped,
    )
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
        waiting=tuple(waiting),
        conversation_id=conversation,
    )


def _watcher(w: WatcherStatus, capped: set[Work], cards: dict[str, list[Waiting]]) -> Helper:
    # Its own conversation comes with "Talk to it" (step three of the core).
    conversation: str | None = None
    waiting = _step(w) + _in(cards, conversation)
    state = _state(
        waiting=bool(waiting),
        working=w.checking_since_ms is not None,
        paused=not w.switched_on,
        capped="watchers" in capped,
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
        waiting=tuple(waiting),
        conversation_id=conversation,
    )


def _step(w: WatcherStatus) -> list[Waiting]:
    """What only the user can do for a watcher they switched on: connect its source, or
    sign in again. Switched off, it asks nothing."""
    if not w.switched_on:
        return []
    step = f"connect:{w.name}"
    if w.last_error == "signed_out":
        at = w.last_error_ms if w.last_error_ms is not None else w.last_check_ms
        return [Waiting(type="step", id=step, text=_SIGN_IN, at_ms=at)]
    if not w.connected:
        # When it was disconnected is not kept anywhere.
        text = _CONNECT.get(w.name, f"Connect {TITLED.get(w.name, w.name)}")
        return [Waiting(type="step", id=step, text=text, at_ms=None)]
    return []


def _state(*, waiting: bool, working: bool, paused: bool, capped: bool) -> HelperState:
    if waiting:
        return "needs_you"
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
    if run.status == "skipped" and run.detail is not None:
        reason = _SKIPPED.get(run.detail, run.detail)
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
            known = [w.at_ms for w in h.waiting if w.at_ms is not None]
            return (0, min(known) if known else _NEVER)
        if h.state == "working":
            return (1, _NEVER if h.since_ms is None else h.since_ms)
        return (2, _NEVER if h.next_ms is None else h.next_ms)

    return sorted(found, key=key)
