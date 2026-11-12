"""SPN-2: the pending-action lifecycle, as a pure decision function.

The router persists what `decide` returns. Nothing here does I/O, so every
combination of state, event and context can be tested exhaustively.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from trillion.spine.contract import Risk, ToolContext, ToolDefinition

EXPIRY_MS = 10 * 60 * 1000

# What the model is told when a spoken yes is not the owner's (§5.3a). SPN-15a: the
# first unsure yes asks for a fresh one; the next falls back to the card's tap.
UNSURE_AGAIN = (
    "the voice wasn't clear enough to be sure it was the owner's; ask them to say the yes "
    "again, a little longer, or tap Approve on the card"
)
UNSURE_TAP = "still not sure it was the owner's voice; ask them to tap Approve on the card"
NOT_OWNER_DETAIL = (
    "that voice isn't the owner's, so it can't approve; only the owner can, by voice or a tap"
)
REPLAY_DETAIL = (
    "that yes matched one already heard exactly, like a recording played back; "
    "ask the owner to tap Approve on the card"
)
# A yes on a turn that read something from outside may be that text's, not the user's.
AFTER_READING_DETAIL = (
    "this turn read something from outside (a page, an email, a file), so a yes now "
    "can't approve a card; ask the user to tap Approve on the card"
)
# With more than one card waiting, a yes can only be for the one shown last.
NOT_NEWEST_DETAIL = (
    "more than one card is waiting and a yes can only approve the newest one; ask the "
    "user to tap Approve on the card they mean"
)

Event = Literal["confirm", "cancel"]


class Status(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


FINAL: frozenset[Status] = frozenset(
    {Status.SUCCEEDED, Status.FAILED, Status.UNKNOWN, Status.REJECTED}
)
REPRESENTABLE: frozenset[Status] = frozenset({Status.EXPIRED, Status.INVALIDATED})


class Refusal(StrEnum):
    NOT_FOUND = "not_found"
    WRONG_SESSION = "wrong_session"
    SAME_TURN = "same_turn"
    CHANNEL = "channel"
    TAP_REQUIRED = "tap_required"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    ALREADY_RESOLVED = "already_resolved"
    NOT_REPRESENTABLE = "not_representable"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGS = "invalid_args"
    DENIED_BY_FLOOR = "denied_by_floor"
    LOOP_GUARD = "loop_guard"
    PRECONDITION_FAILED = "precondition_failed"
    PRECONDITION_CHANGED = "precondition_changed"
    # The card was never shown on a screen, so the user cannot have seen what it says.
    NOT_SHOWN = "not_shown"
    # Nobody by that owner id exists here. Refused before anything else happens (#4).
    UNKNOWN_OWNER = "unknown_owner"
    # A timed ask running unattended: nobody is there to see a card or answer one (D5).
    UNATTENDED = "unattended"
    # A helper's chat or run: only she makes helpers or hands them work (AGT-2).
    NOT_FOR_HELPERS = "not_for_helpers"
    # A tap on a widget's ask: she wrote its words, maybe from a page she read.
    ASK = "ask"
    # A spoken yes in a voice that is not the enrolled owner's, or a recording (SPN-18).
    NOT_OWNER = "not_owner"
    # A spoken yes that could not be told for sure to be the owner's (SPN-15a).
    VOICE_UNSURE = "voice_unsure"
    # The voice this turn is not the owner's: nothing personal runs (PER-13).
    PRIVATE = "private"
    # Why the owner's own words were not the yes (SPN-20). Recorded, never said: the
    # answer in every one of these cases is the ordinary card.
    OWN_WORDS_OFF = "own_words_off"
    NOT_SPOKEN = "not_spoken"
    READ_OUTSIDE = "read_outside"
    NOT_ELIGIBLE = "not_eligible"
    NOT_VERBATIM = "not_verbatim"
    # A yes, not a tap, on a turn that read something from outside: it may be that
    # text's yes, not the user's. Only the card's tap approves then.
    AFTER_READING = "after_reading"
    # A yes, not a tap, for a card other than the newest one waiting: only its tap can.
    NOT_NEWEST = "not_newest"


@dataclass(frozen=True)
class Rejection:
    reason: Refusal
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class PendingAction:
    id: str
    owner_id: str
    tool: str
    args: dict[str, Any]
    args_hash: str
    summary: str
    target: str
    risk: Risk
    recoverable: bool
    session_id: str
    created_turn: int
    status: Status
    created_at: int
    expires_at: int
    represented_from: str | None
    effect_status: str | None = None
    outcome_detail: str | None = None
    precondition_token: str | None = None
    # When the row last changed. None for an action not yet saved.
    updated_at: int | None = None
    # True once retention has cleared the full arguments (#20): `args` is then the slim
    # copy, with long values replaced by their fingerprints, and the action cannot run.
    args_cleared: bool = False
    # Everything the card shows besides its summary, exactly as it will go (SPN-2a,
    # spine/card.py). Kept with the full arguments and cleared with them. Empty when
    # the summary says all of it.
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class Waiting:
    """A card still waiting in the session, as `decide` weighs a yes against it."""

    id: str
    created_at: int
    expires_at: int
    # When it was first shown on a screen (SPN-17's `delivered_at`), or None.
    shown_at: int | None


def newest(waiting: Sequence[Waiting], now_ms: int) -> str | None:
    """The one card a yes can be for: of the cards still waiting, the one shown on a
    screen last (ties: raised last). A card not yet shown is not one the user can be
    answering, so it is never the newest. None when no waiting card has been shown:
    then there is nothing on screen for a yes to be about, and the router's delivery
    rule refuses every card alike (#4)."""
    shown = [w for w in waiting if w.expires_at > now_ms and w.shown_at is not None]
    if not shown:
        return None
    return max(shown, key=lambda w: (w.shown_at or 0, w.created_at, w.id)).id


_STATUS_REFUSAL = {
    Status.EXPIRED: Refusal.EXPIRED,
    Status.SUPERSEDED: Refusal.SUPERSEDED,
    Status.INVALIDATED: Refusal.PRECONDITION_CHANGED,
}


def decide(
    a: PendingAction,
    event: Event,
    ctx: ToolContext,
    now_ms: int,
    *,
    waiting: Sequence[Waiting] = (),
) -> Status | Rejection:
    """Return the next status for `a`, or why the event is refused.

    `waiting` is every card still waiting in `a`'s session, `a` among them: a yes that
    is not a tap can only approve the newest (`newest`)."""
    if a.owner_id != ctx.owner_id:
        return Rejection(Refusal.NOT_FOUND)
    if a.session_id != ctx.session_id:
        return Rejection(Refusal.WRONG_SESSION, "only the session that raised it can resolve it")
    if a.status is not Status.PROPOSED:
        return Rejection(_STATUS_REFUSAL.get(a.status, Refusal.ALREADY_RESOLVED), a.status.value)
    if now_ms >= a.expires_at:
        return Rejection(Refusal.EXPIRED, "re-present it to ask again")
    if ctx.via in ("schedule", "refresh", "routine"):
        # Nobody is there to say yes or no. The router refuses these first; this is the
        # same rule where a decision is made, should another caller ever reach it.
        return Rejection(Refusal.UNATTENDED, "nobody is here to answer it")
    if event == "cancel":
        return Status.REJECTED
    if ctx.via == "ask":
        return Rejection(Refusal.ASK, "a tap on a widget can't approve; use the card")
    if ctx.via == "channel":
        return Rejection(Refusal.CHANNEL, "a channel message can never close a gate")
    if ctx.turn <= a.created_turn:
        return Rejection(Refusal.SAME_TURN, "ask the user; confirm on their next turn")
    if (a.risk is Risk.SPEND or not a.recoverable) and ctx.via != "tap":
        return Rejection(Refusal.TAP_REQUIRED, "this action needs a tap on the confirmation card")
    if ctx.via != "tap" and ctx.private:
        # Speaker-scoped, not session-scoped (SPN-14a, SPN-18): a spoken yes counts only
        # in the enrolled owner's voice. A tap is a physical act, judged as it always was.
        if ctx.speaker == "unsure":
            return Rejection(Refusal.VOICE_UNSURE, UNSURE_AGAIN)
        if ctx.speaker == "replay":
            return Rejection(Refusal.NOT_OWNER, REPLAY_DETAIL)
        return Rejection(Refusal.NOT_OWNER, NOT_OWNER_DETAIL)
    if ctx.via != "tap" and ctx.read_outside:
        # Words she read this turn (a page, an email) could have said the yes. A tap is
        # the user's own act on the card, whatever was read.
        return Rejection(Refusal.AFTER_READING, AFTER_READING_DETAIL)
    if ctx.via != "tap" and newest(_with(waiting, a), now_ms) not in (None, a.id):
        # A yes is for the card the user is looking at. Planted text naming an older
        # card's id can't move it there; that card's own tap can.
        return Rejection(Refusal.NOT_NEWEST, NOT_NEWEST_DETAIL)
    return Status.APPROVED


# ── SPN-20: the owner's own words as the yes ─────────────────────────────

# What `decided_by` says on a decision made by the owner's own words.
OWN_WORDS = "own_words"
# The shortest argument that may be its own yes: a single word, or a few letters, is
# too easily found in anything anyone says.
MIN_OWN_WORDS = 2
MIN_OWN_CHARS = 8
_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u02bc": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
    }
)
# A run of anything that is not a letter, a digit or an apostrophe.
_SPLIT = re.compile(r"[^\w']+|_+")


def said_words(text: str) -> tuple[str, ...]:
    """`text` as the words that are compared: NFKC, casefolded, quotes made straight,
    split on every run that is not a letter, digit or apostrophe. An apostrophe at
    either end of a word is a quotation mark, not part of it ("'nine'" is "nine")."""
    plain = unicodedata.normalize("NFKC", text).casefold().translate(_QUOTES)
    words = (w.strip("'") for w in _SPLIT.split(plain))
    return tuple(w for w in words if w)


def verbatim(arg: str, utterance: str) -> bool:
    """Whether `arg`'s words appear as one unbroken run, on word boundaries, in what
    was said. Long enough to mean something: at least two words and eight characters.
    Nothing is rewritten: "nine" is not "9"."""
    want, heard = said_words(arg), said_words(utterance)
    if len(want) < MIN_OWN_WORDS or len(" ".join(want)) < MIN_OWN_CHARS:
        return False
    n = len(want)
    return any(heard[i : i + n] == want for i in range(len(heard) - n + 1))


def own_words_eligible(d: ToolDefinition) -> bool:
    """A tool whose own words may approve it: it opted in, and it is irreversible (never
    spend) and recoverable underneath. The registry holds the rest (first-party, and
    every name a property of its schema); this is asked again where it is used."""
    return bool(d.own_words) and d.risk is Risk.IRREVERSIBLE and d.recoverable


def own_words(
    d: ToolDefinition, args: Mapping[str, Any], ctx: ToolContext, enabled: bool
) -> Rejection | None:
    """None when the owner's words this turn are themselves the yes for this call
    (SPN-20), or why not. Every one must hold: the owner turned it on; the turn was
    spoken, not a host turn or a helper's; the voice was the enrolled owner's; nothing
    from outside was read this turn; the tool may take this path; and every argument it
    lists, and no other, is in the owner's words. Pure, so every case is tested."""
    if not enabled:
        return Rejection(Refusal.OWN_WORDS_OFF)
    if ctx.via != "voice" or ctx.heard is None or ctx.host_turn:
        return Rejection(Refusal.NOT_SPOKEN)
    if ctx.speaker != "owner":
        return Rejection(Refusal.NOT_OWNER)
    if ctx.helper_id is not None or ctx.routine_target is not None:
        return Rejection(Refusal.NOT_ELIGIBLE, "a helper's chat or a routine's run")
    if ctx.read_outside:
        return Rejection(Refusal.READ_OUTSIDE)
    if not own_words_eligible(d):
        return Rejection(Refusal.NOT_ELIGIBLE)
    if set(args) != set(d.own_words):
        # A listed argument missing, or one present that nobody says (an id).
        return Rejection(Refusal.NOT_VERBATIM, "not every argument is the owner's words")
    for name in d.own_words:
        value = args[name]
        if not isinstance(value, str) or not verbatim(value, ctx.heard):
            return Rejection(Refusal.NOT_VERBATIM)
    return None


def _with(waiting: Sequence[Waiting], a: PendingAction) -> list[Waiting]:
    """The waiting cards, `a` among them: added, never shown, when the list lacks it."""
    if any(w.id == a.id for w in waiting):
        return list(waiting)
    return [
        *waiting,
        Waiting(id=a.id, created_at=a.created_at, expires_at=a.expires_at, shown_at=None),
    ]


_ID_ONLY = {
    "type": "object",
    "properties": {"pending_id": {"type": "string", "minLength": 1}},
    "required": ["pending_id"],
    "additionalProperties": False,
}

CONFIRM_TOOL = ToolDefinition(
    name="confirm_pending_action",
    description=(
        "Execute the pending action the user just approved, on a turn after you proposed it. "
        "It runs exactly what was proposed; it takes no other arguments."
    ),
    input_schema=_ID_ONLY,
    risk=Risk.WRITE,
    category="meta",
    timeout_s=60.0,
    trusted_output=True,
)

CANCEL_TOOL = ToolDefinition(
    name="cancel_pending_action",
    description="Discard a pending action the user declined.",
    input_schema=_ID_ONLY,
    risk=Risk.WRITE,
    category="meta",
    timeout_s=5.0,
    trusted_output=True,
)

REPRESENT_TOOL = ToolDefinition(
    name="represent_pending_action",
    description=(
        "Ask again for an action that expired, was invalidated, or that a check after a "
        "crash proved never went out. It shows a new card, which the user must approve like "
        "any other; it never runs anything. Call it only when the user asks for that action "
        "again, and find its pending_id with decision_record first."
    ),
    input_schema=_ID_ONLY,
    risk=Risk.WRITE,
    category="meta",
    timeout_s=30.0,
    trusted_output=True,
)

META_TOOLS: dict[str, ToolDefinition] = {
    CONFIRM_TOOL.name: CONFIRM_TOOL,
    CANCEL_TOOL.name: CANCEL_TOOL,
    REPRESENT_TOOL.name: REPRESENT_TOOL,
}
