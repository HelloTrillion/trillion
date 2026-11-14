"""The only caller of `Tool.execute`. Every guarantee in the spine is enforced here or below.

Responses to the model are built from what was recorded, never from what a
tool claims about itself (SPN-19). Every call is audited, refusals included.
Logs carry names, ids, statuses and exception class names only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from trillion.spine import deny_floor
from trillion.spine.audit import AuditRecord
from trillion.spine.card import card_detail, visible
from trillion.spine.clock import Clock, system_clock
from trillion.spine.confirm import (
    CANCEL_TOOL,
    CONFIRM_TOOL,
    EXPIRY_MS,
    META_TOOLS,
    OWN_WORDS,
    REPRESENT_TOOL,
    REPRESENTABLE,
    UNSURE_TAP,
    PendingAction,
    Refusal,
    Rejection,
    Status,
    Waiting,
    decide,
    own_words,
)
from trillion.spine.contract import (
    GATED,
    JSON,
    UNREACHABLE,
    ActionSummary,
    Precondition,
    Risk,
    Tool,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.hashing import args_hash, canonical_json
from trillion.spine.loop_guard import LoopGuard, Verdict
from trillion.spine.lossless import encode
from trillion.spine.neutralize import Counts, as_json, cleaned
from trillion.spine.neutralize import shown as shown_counts
from trillion.spine.refusal import plain_no
from trillion.spine.registry import ROUTINE_BOARD, ToolRegistry, runs_in_routine, runs_unattended
from trillion.spine.retention import RETENTION_DAYS, RETENTION_MS
from trillion.spine.store import DecisionIn, SpineStore
from trillion.spine.untrusted import wrap_counted

logger = logging.getLogger(__name__)

ResponseStatus = Literal[
    "succeeded",
    "failed",
    "unknown",
    "refused",
    "confirmation_required",
    "cancelled",
    "in_progress",
    # A slow read handed to the background: its result arrives later as a host note.
    "started",
]

_DETAIL_LIMIT = 2000
_TOOL_DETAIL_LIMIT = 300
# The longest card summary: what a card can show. Public, so a tool can refuse a
# card that would be cut rather than show part of what it does.
SUMMARY_LIMIT = 200
_UNHASHABLE = "unhashable"
_TARGET_CHANGED = "the target changed since you were asked"
_NO_FINGERPRINT = "\x00fingerprint-failed"  # never equal to a recorded token
_UNATTENDED = "nobody is here to approve it; say what you would do instead"
# Runs nobody is watching: a timed ask, a widget's refresh (the live widgets spec), and
# a routine's run (the routines spec). None can raise, settle or re-present a card.
_UNWATCHED = frozenset({"schedule", "refresh", "routine"})
_NOT_HERE = "a routine shows its board only on its own conversation"
_REFRESH_READS_ONLY = "a refresh only reads"
# Never replayed by a refresh, whatever their risk: a page is chosen by what she read
# (SPN-5), and her own tools say nothing about a connector. Named here so the spine
# needs nothing from the tools (tests check the names still match).
REFRESH_NEVER = frozenset({"web_fetch", "describe_self", "browse"})
REFRESH_NEVER_CATEGORIES = frozenset({"memory"})
# Only she dispatches (AGT-2): never in a helper's chat or run, whatever the via.
# Named here so the spine needs nothing from the tools (tests check the names match).
HELPER_NEVER = frozenset({"helper_create", "helper_ask", "helper_suggest"})
_NOT_FOR_HELPERS = "only Trillion makes helpers or hands them work"
# All that runs for a voice that is not the enrolled owner's (PER-13): nothing that
# reads or changes what is theirs. Memory, files, folders, connected accounts, her
# board, reminders, rules, helpers and describe_self (which names the shared folders)
# are all outside it. Named here so the spine needs nothing from the tools.
GUEST_TOOLS = frozenset({"web_fetch", "stay_quiet"})
_PRIVATE = (
    "this voice isn't the owner's, so nothing personal is shared or changed for it; "
    "say so plainly, and that the owner can ask themselves"
)
# A spoken yes judged unsure this many times for one card falls back to the tap (SPN-15a).
UNSURE_TRIES = 1
# OPS-1: a check that can't reach the provider is tried again for this long after the
# action was proposed, then the action is `unknown`.
CHECK_FOR_MS = 24 * 60 * 60 * 1000
# A "never went" answer about an action already awaiting a check counts only once it
# has been quiet this long: a request whose answer was lost may still be landing.
SETTLE_NONE_AFTER_MS = 5 * 60 * 1000
UNCHECKED = "unchecked"
NOT_SENT = "interrupted before it went out"
_STILL_CHECKING = (
    "No answer came back, so it may or may not have gone. Checking with the provider"
    " and will say when it's clear."
)

# A re-presented copy in one of these is open or has run (`_proved_not_sent`).
_TAKEN = frozenset(
    {
        Status.PROPOSED,
        Status.APPROVED,
        Status.EXECUTING,
        Status.SUCCEEDED,
        Status.FAILED,
        Status.UNKNOWN,
    }
)

Outcome = Literal["succeeded", "not_sent", "unknown", "interrupted"]


# The decisions a check records when it left a send in doubt, and how each is told.
_IN_DOUBT: dict[str, Outcome] = {"recovered:not_sent": "not_sent", "recovered:unknown": "unknown"}


@dataclass(frozen=True)
class Settled:
    """An interrupted action that a check settled, for telling the user (OPS-1).
    `interrupted` is an approved action that never started: nothing left the computer."""

    id: str
    outcome: Outcome


_OUTCOME: dict[Status, ResponseStatus] = {
    Status.SUCCEEDED: "succeeded",
    Status.UNKNOWN: "unknown",
    Status.FAILED: "failed",
}


@dataclass(frozen=True, kw_only=True)
class RouterResponse:
    status: ResponseStatus
    content: str = ""
    pending_id: str | None = None
    summary: str | None = None
    reason: Refusal | None = None
    detail: str = ""
    error_kind: str | None = None
    warning: str | None = None
    # The tool a confirmed or cancelled card belongs to: an approved read is someone
    # else's words like any read (MEM-5), whichever way it was approved. Never sent to
    # the model.
    tool: str | None = None
    # The background job a "started" read runs as, so she can say which is on its way.
    job: str | None = None
    # The same call was proposed again while its card waited: no new card. Never sent
    # to the model; `detail` tells it.
    already_asked: bool = False
    # Approved by the owner's own words this turn (SPN-20): its card is shown already
    # settled. Never sent to the model; `detail` tells it.
    own_words: bool = False
    # What the neutralizer removed from `content`, as counts by kind (SPN-6), for the
    # audit. Never sent to the model, and never the removed text.
    neutralized: Counts = ()

    def to_model(self) -> str:
        """The JSON the model sees. Only fields that carry information."""
        body: dict[str, Any] = {"status": self.status}
        for key in ("content", "pending_id", "job", "summary", "detail", "error_kind", "warning"):
            value = getattr(self, key)
            if value:
                body[key] = value
        if self.reason is not None:
            body["reason"] = self.reason.value
        return json.dumps(body, ensure_ascii=False)


def _routine_may(d: ToolDefinition, ctx: ToolContext) -> bool:
    """Whether a run nobody is watching may call this tool. A routine's run gets what
    a timed ask gets, and besides its board tools only on the conversation it was
    started for, and `routine_nothing` (the routines spec, D5)."""
    if runs_unattended(d):
        return True
    if ctx.via != "routine" or not runs_in_routine(d):
        return False
    if d.name in ROUTINE_BOARD:
        return ctx.routine_target is not None and ctx.session_id == ctx.routine_target
    return True


def _refused(reason: Refusal, detail: str = "") -> RouterResponse:
    return RouterResponse(status="refused", reason=reason, detail=detail)


def _tool_detail(text: str) -> str:
    """Text a tool wrote (a precheck reason, say) is defanged and cut before the model sees it."""
    return cleaned(text, "a tool's detail")[:_TOOL_DETAIL_LIMIT]


def safe_summary(text: str) -> str:
    """The approval card is what the user consents to (SPN-2a). Whatever a tool put in it
    (a model-chosen file name, say) is neutralized, kept to one line with no control or
    format characters (no bidi overrides), and cut to a length a card can show."""
    clean = cleaned(text, "a card's summary")
    for ws in ("\r\n", "\n", "\r", "\t"):
        clean = clean.replace(ws, " ")
    clean = "".join(c for c in clean if unicodedata.category(c) not in ("Cc", "Cf"))
    return clean[:SUMMARY_LIMIT]


def _detail(described: ActionSummary, summary: str) -> tuple[str, str | None]:
    """What the card shows besides its summary, and None; or why there can be no card.

    The tool's own detail when it gives one: exactly as given, or refused if anything
    in it could not be seen. Otherwise its text, whenever the summary is not all of it
    (cut, or put on one line): a card never shows part of what it does (SPN-2a). Either
    way line breaks are kept, and it is bounded (spine/card.py)."""
    if described.detail:
        return card_detail(described.detail)
    if summary == described.text:
        return "", None
    # Text the tool did not mean to go anywhere as it stands (a model-chosen file name,
    # say): shown whole, anything unseen in it as a visible token rather than refused.
    return card_detail(visible(described.text))


def _lossless_note(text: str) -> str | None:
    """For lossless output: tell the model, in host words, that some characters appear as
    tokens and come back exactly when written unchanged. Only a count: never a sample."""
    n = encode(text).escaped
    if n == 0:
        return None
    return (
        f"{n} character{'' if n == 1 else 's'} that {'is' if n == 1 else 'are'} invisible or"
        " could pose as structure appear as ⟦U+XXXX⟧ tokens; keep them exactly as shown in"
        " anything you write back and the original characters are restored"
    )


def _safe_hash(args: JSON) -> str | None:
    try:
        return args_hash(dict(args))
    except (TypeError, ValueError, RecursionError):  # NaN, non-JSON values, absurd nesting
        return None


def _fingerprint(tool: Tool, args: JSON) -> str | None:
    """`Tool.fingerprint`, or None for a tool that does not implement the hook."""
    hook = getattr(tool, "fingerprint", None)
    return None if hook is None else hook(args)


class _Registered:
    """A registered tool seen through the definition validated when it was registered.

    The router reads risk, path_args, schema, timeout and trust only from here, so a
    plugin that reassigns its own `definition` later cannot ungate or untrust itself."""

    def __init__(self, tool: Tool, definition: ToolDefinition) -> None:
        self._tool = tool
        self.definition = definition

    def describe(self, args: JSON) -> ActionSummary:
        return self._tool.describe(args)

    def target(self, args: JSON) -> str:
        return self._tool.target(args)

    def fingerprint(self, args: JSON) -> str | None:
        return _fingerprint(self._tool, args)

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        return await self._tool.precheck(args, ctx)

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        return await self._tool.execute(args, ctx)

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        return await self._tool.reconcile(args, ctx)


# What she is told when the owner's own words approved a call (SPN-20).
_ON_THEIR_WORD = (
    "approved by the user's own spoken words this turn, so no card is waiting; say in one "
    "sentence what was done"
)


def _by(ctx: ToolContext, event: str) -> DecisionIn:
    return DecisionIn(event=event, decided_by=ctx.session_id, via=ctx.via)


def _system(event: str, via: str = "text") -> DecisionIn:
    return DecisionIn(event=event, decided_by="system", via=via)


def final_status(result: ToolResult) -> Status:
    """Success needs evidence (SPN-17a). An unverifiable claim is `unknown`, never `succeeded`."""
    if result.ok:
        if result.effect == "confirmed":
            return Status.SUCCEEDED
        if result.effect == "unknown":
            return Status.UNKNOWN
        return Status.FAILED
    return Status.UNKNOWN if result.effect == "unknown" else Status.FAILED


async def _see_through[T](work: Awaitable[T]) -> T:
    """Run `work` to the end even if the caller is cancelled meanwhile, however often,
    then honour the cancellation. If the work itself is cancelled (the event loop is
    shutting down), that is raised at once: `recover()` settles what was left."""
    task = asyncio.ensure_future(work)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class Router:
    def __init__(
        self,
        registry: ToolRegistry,
        store: SpineStore,
        *,
        policy: deny_floor.DenyPolicy,
        clock: Clock = system_clock,
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
        audit_key: bytes | None = None,
        require_delivery: bool = False,
        own_words_on: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._policy = policy
        self._clock = clock
        self._new_id = new_id
        # Audit rows keep a keyed hash of each call's arguments, never a bare one: an
        # unsalted hash of a short value such as a path can be guessed back (#4).
        self._audit_key = audit_key or os.urandom(32)
        # When set, a card must have been shown on a screen before it can be approved,
        # so nobody approves what they never saw (#4). The running app sets it.
        self._require_delivery = require_delivery
        # Whether the owner turned on their own words as the yes (SPN-20): a setting only
        # the owner changes, read for each call that could take that path. None: off.
        self._own_words_on = own_words_on
        # The turn, per session, whose one own-words yes is spent (SPN-20, D10).
        self._own_words_turn: dict[str, int] = {}
        self._owners: set[str] = set()
        # Spoken yeses judged unsure, per card (SPN-15a). In memory: a restart asks again.
        self._unsure: dict[str, int] = {}
        # The roots `recover` was given, for the later checks that have none of their own.
        self._granted: tuple[Path, ...] = ()

    # ── The normal path ──────────────────────────────────────────────────

    async def dispatch(
        self, call: ToolCall, ctx: ToolContext, guard: LoopGuard | None = None
    ) -> RouterResponse:
        """The door for callers outside the spine: the agent loop, and later sub-agents.

        It is `execute` under a name of its own, so the structural test can keep its
        simplest form: outside this file, nothing in the source says `.execute` at all."""
        return await self.execute(call, ctx, guard)

    async def execute(
        self, call: ToolCall, ctx: ToolContext, guard: LoopGuard | None = None
    ) -> RouterResponse:
        if not await self._known(ctx.owner_id):
            return _refused(Refusal.UNKNOWN_OWNER)
        start = self._clock()
        h = _safe_hash(call.args)

        async def audited(
            risk: str, r: RouterResponse, decision: str = "refused"
        ) -> RouterResponse:
            return await self._audit(ctx, start, call.name, h or _UNHASHABLE, risk, decision, r)

        if call.name in META_TOOLS:
            pid = call.args.get("pending_id")
            extra = set(call.args) - {"pending_id"}
            if not isinstance(pid, str) or not pid or extra:
                return await audited("meta", _refused(Refusal.INVALID_ARGS, "pending_id only"))
            if ctx.via in _UNWATCHED:
                # Nobody said yes or no: an unattended run settles no card (D5).
                return await audited("meta", _refused(Refusal.UNATTENDED, _UNATTENDED))
            # A tap belongs to one action, not to the turn it happened in: the model can
            # never pass one on. Only the UI's direct `resolve` call carries `via="tap"`.
            if ctx.via == "tap":
                ctx = dataclasses.replace(ctx, via="text")
            if call.name == REPRESENT_TOOL.name:
                # Only ever a new card, waiting for its own yes. It audits itself.
                return await self.represent(pid, ctx)
            decision: Literal["confirm", "cancel"] = (
                "confirm" if call.name == CONFIRM_TOOL.name else "cancel"
            )
            resolved = await self.resolve(pid, decision, ctx)
            # An approved action that then fails is a failure of its tool, counted like
            # any other (SPN-7): otherwise a proposal that fails every time could be
            # proposed, approved and retried forever.
            if (
                guard is not None
                and decision == "confirm"
                and resolved.status
                in (
                    "failed",
                    "unknown",
                )
            ):
                a = await self._store.get_pending(pid)
                if a is not None:
                    guard.record_result(a.tool, ok=False)
            return resolved

        tool = self._get(call.name)
        if ctx.helper_id is not None and call.name in HELPER_NEVER:
            # Before any of the tool's code runs, whether or not it was offered.
            risk = tool.definition.risk.value if tool is not None else "unknown"
            return await audited(risk, _refused(Refusal.NOT_FOR_HELPERS, _NOT_FOR_HELPERS))
        if tool is None:
            return await audited("unknown", _refused(Refusal.UNKNOWN_TOOL))
        risk = tool.definition.risk.value
        if ctx.private and call.name not in GUEST_TOOLS:
            # Default-private to a voice that isn't the owner's (PER-13): refused before
            # any of the tool's code runs, whether or not it was offered.
            return await audited(risk, _refused(Refusal.PRIVATE, _PRIVATE))
        if ctx.via in _UNWATCHED and not _routine_may(tool.definition, ctx):
            if ctx.via == "routine" and tool.definition.name in ROUTINE_BOARD:
                return await audited(risk, _refused(Refusal.UNATTENDED, _NOT_HERE))
            # A card belongs to a session someone is watching (D5). Refused before any of
            # the tool's own code runs, whether or not it was offered to this run.
            return await audited(risk, _refused(Refusal.UNATTENDED, _UNATTENDED))
        if ctx.via == "refresh" and (
            tool.definition.risk is not Risk.READ
            or tool.definition.name in REFRESH_NEVER
            or tool.definition.category in REFRESH_NEVER_CATEGORIES
        ):
            # A refresh replays what she read for a widget, with nobody deciding (D4): a
            # freed write is still a write.
            return await audited(risk, _refused(Refusal.UNATTENDED, _REFRESH_READS_ONLY))
        if h is None:
            return await audited(risk, _refused(Refusal.INVALID_ARGS, "arguments must be JSON"))

        validator = Draft202012Validator(dict(tool.definition.input_schema))
        try:
            error = best_match(validator.iter_errors(dict(call.args)))
        except (RecursionError, TypeError, ValueError) as e:
            logger.warning("validating arguments for %s raised %s", call.name, type(e).__name__)
            return await audited(risk, _refused(Refusal.INVALID_ARGS, "arguments too complex"))
        if error is not None:
            return await audited(risk, _refused(Refusal.INVALID_ARGS, error.message[:300]))

        denial = deny_floor.check(
            tool.definition,
            call.args,
            self._policy,
            ctx.granted_roots,
            read_only_roots=ctx.read_only_roots,
        )
        if denial is not None:
            return await audited(risk, _refused(Refusal.DENIED_BY_FLOOR, denial.reason))

        warning: str | None = None
        if guard is not None:
            g = guard.check_call(call.name, h)
            if g.verdict is Verdict.STOP:
                stop = g.reason.value if g.reason else ""
                return await audited(risk, _refused(Refusal.LOOP_GUARD, stop))
            if g.verdict is Verdict.WARN and g.reason is not None:
                warning = f"loop guard: {g.reason.value}"

        pre = await self._precheck(tool, call.args, ctx)
        if not pre.ok:
            # A refused precheck is a call that made no progress, the same as a failed
            # run: twenty reads of files that do not exist are one loop (SPN-7).
            if guard is not None:
                guard.record_result(call.name, ok=False)
            return await audited(
                risk, _refused(Refusal.PRECONDITION_FAILED, _tool_detail(pre.reason))
            )

        if tool.definition.risk in GATED:
            response = await self._propose(tool, call.args, ctx, represented_from=None)
            proposed = response.status == "confirmation_required"
            asked = "duplicate" if response.already_asked else "proposed"
            response = await audited(risk, response, asked if proposed else "refused")
            if (
                proposed
                and not response.already_asked
                and response.pending_id is not None
                and await self._by_own_words(tool.definition, call.args, ctx)
            ):
                # The owner's words this turn are the yes (SPN-20): recorded and run
                # through the confirmed path, exactly as a yes on a later turn would be.
                return await self._approve_own_words(response.pending_id, ctx, guard)
            return response

        result = await self._run(tool, call.args, ctx)
        if guard is not None:
            g = guard.record_result(call.name, ok=result.ok)
            if g.verdict is not Verdict.OK and g.reason is not None and warning is None:
                warning = f"loop guard: {g.reason.value}"
        # Only a read has no effect to evidence, so only a read may call `ok` a success.
        # A write needs the same evidence as a gated action (SPN-17a).
        is_read = tool.definition.risk is Risk.READ
        status = Status.SUCCEEDED if is_read and result.ok else final_status(result)
        response = self._result_response(tool, status, result.content, result.error_kind, warning)
        return await audited(risk, response, "executed")

    # ── The gated path ───────────────────────────────────────────────────

    async def _propose(
        self, tool: Tool, args: JSON, ctx: ToolContext, *, represented_from: str | None
    ) -> RouterResponse:
        now = self._clock()
        name = tool.definition.name
        frozen: dict[str, Any] = json.loads(canonical_json(dict(args)))
        try:
            target = tool.target(frozen)
            described = tool.describe(frozen)
            summary = safe_summary(described.text)
            token = _fingerprint(tool, frozen)
        except Exception as e:
            logger.warning("describing %s raised %s", name, type(e).__name__)
            return _refused(Refusal.PRECONDITION_FAILED, "the action could not be described")
        detail, unshowable = _detail(described, summary)
        if unshowable is not None:
            # The card shows all of it or there is no card (SPN-2a): never part of it.
            return _refused(Refusal.PRECONDITION_FAILED, unshowable)
        if represented_from is None:
            waiting = await self._waiting(ctx, name, target, args_hash(frozen), token, now)
            if waiting is not None:
                # A model can repeat a call in one turn. Its card already waits: a second
                # would leave one behind, waiting forever, once the other ran.
                logger.info("pending action %s proposed again for %s", waiting.id, name)
                return RouterResponse(
                    status="confirmation_required",
                    pending_id=waiting.id,
                    summary=waiting.summary,
                    detail=(
                        "already proposed; no new card was opened, the first one is still"
                        " waiting for the user's decision"
                    ),
                    already_asked=True,
                )
        action = PendingAction(
            id=self._new_id(),
            owner_id=ctx.owner_id,
            tool=name,
            args=frozen,
            args_hash=args_hash(frozen),
            summary=summary,
            target=target,
            risk=tool.definition.risk,
            recoverable=tool.definition.recoverable,
            session_id=ctx.session_id,
            created_turn=ctx.turn,
            status=Status.PROPOSED,
            created_at=now,
            expires_at=now + EXPIRY_MS,
            represented_from=represented_from,
            precondition_token=token,
            detail=detail,
        )
        # Any open proposal for the same target is retired in the same transaction.
        await self._store.insert_pending(
            action, _by(ctx, "proposed"), supersede=_by(ctx, "superseded")
        )
        logger.info("pending action %s proposed for %s", action.id, name)
        return RouterResponse(
            status="confirmation_required", pending_id=action.id, summary=action.summary
        )

    async def _waiting(
        self, ctx: ToolContext, tool: str, target: str, h: str, token: str | None, now: int
    ) -> PendingAction | None:
        """The open, unexpired card proposed this turn in this session for exactly this
        call, if there is one. A changed target (another precondition token) is a new
        proposal. So is a repeat on a later turn: handing back the older card would let it
        be confirmed on the turn it was proposed again (SPN-2), so that one supersedes."""
        for a in await self._store.open_for_target(ctx.owner_id, ctx.session_id, tool, target):
            if (
                a.created_turn == ctx.turn
                and a.args_hash == h
                and a.precondition_token == token
                and a.expires_at > now
            ):
                return a
        return None

    async def resolve(
        self, pending_id: str, decision: Literal["confirm", "cancel"], ctx: ToolContext
    ) -> RouterResponse:
        """Confirm or cancel one pending action.

        A tap belongs to an action, not to a turn. `ctx.via == "tap"` is honoured only
        here, when the UI calls `resolve` directly with the id of the card the user
        tapped. A model-issued `confirm_pending_action` goes through `execute`, which
        downgrades any `via="tap"` to `"text"` before it reaches this method, so a tap
        on card A can never close the gate on card B.
        """
        if not await self._known(ctx.owner_id):
            return _refused(Refusal.UNKNOWN_OWNER)
        start = self._clock()
        if ctx.via in _UNWATCHED:
            meta = CONFIRM_TOOL.name if decision == "confirm" else CANCEL_TOOL.name
            h = args_hash({"pending_id": pending_id})
            refusal = _refused(Refusal.UNATTENDED, _UNATTENDED)
            return await self._audit(ctx, start, meta, h, "meta", "resolve", refusal)
        a = await self._store.get_pending(pending_id)
        if a is None or a.owner_id != ctx.owner_id:
            meta = CONFIRM_TOOL.name if decision == "confirm" else CANCEL_TOOL.name
            h = args_hash({"pending_id": pending_id})
            return await self._audit(
                ctx, start, meta, h, "meta", "resolve", _refused(Refusal.NOT_FOUND)
            )

        if ctx.helper_id is not None and a.tool in HELPER_NEVER:
            # A backstop: her card is never settled from a helper's chat or run.
            response = _refused(Refusal.NOT_FOR_HELPERS, _NOT_FOR_HELPERS)
            return await self._audit_resolution(a, ctx, start, response)
        a = await self._expire_if_due(a, start, ctx)
        # A tap is for its own card, whichever is newest: only a yes is weighed.
        waiting = await self._waiting_cards(a) if decision == "confirm" and ctx.via != "tap" else ()
        verdict = decide(a, decision, ctx, start, waiting=waiting)
        if isinstance(verdict, Rejection):
            if verdict.reason is Refusal.ALREADY_RESOLVED:
                # The settled outcome, said to be one: a second "confirm" after a tap ran
                # nothing, and must not read as a second success (#32).
                settled = dataclasses.replace(
                    self._from_record(a),
                    reason=Refusal.ALREADY_RESOLVED,
                    detail="already settled earlier; nothing ran again",
                )
                return await self._audit_resolution(a, ctx, start, settled)
            detail = verdict.detail
            if verdict.reason is Refusal.VOICE_UNSURE:
                # A fresh sample first, then the tap: never a dead end (SPN-15a).
                tries = self._unsure.get(a.id, 0)
                self._unsure[a.id] = tries + 1
                if tries >= UNSURE_TRIES:
                    detail = UNSURE_TAP
            response = _refused(verdict.reason, detail)
            return await self._audit_resolution(a, ctx, start, response)
        self._unsure.pop(a.id, None)
        if (
            verdict is Status.APPROVED
            and self._require_delivery
            and await self._store.get_delivery(a.id) is None
        ):
            # Whatever she said about it, the card is what the user approves (SPN-2a), and
            # it has not been on a screen. Declining needs no such proof.
            response = _refused(Refusal.NOT_SHOWN, "its card has not been shown yet")
            return await self._audit_resolution(a, ctx, start, response)

        # From the user's answer to the recorded outcome and its audit entry, nothing
        # that stops her stops the work: a barge-in or a closed tab ends her reply,
        # never a "yes" half-carried-out (#83, #84, VOI-15c). The tool's own timeout
        # still bounds it, and the cancellation is honoured once all of it is written.
        return await _see_through(self._decide_and_run(a, verdict, ctx, start))

    async def _waiting_cards(self, a: PendingAction) -> list[Waiting]:
        """Every card still waiting in `a`'s session, with when each was first shown:
        what `decide` weighs a yes that is not a tap against."""
        out = []
        for w in await self._store.list_by_status(Status.PROPOSED):
            if w.owner_id != a.owner_id or w.session_id != a.session_id:
                continue
            shown = await self._store.get_delivery(w.id)
            out.append(
                Waiting(
                    id=w.id,
                    created_at=w.created_at,
                    expires_at=w.expires_at,
                    shown_at=shown.delivered_at if shown is not None else None,
                )
            )
        return out

    async def cancel_on_no(
        self, words: str, ctx: ToolContext, *, asked: Collection[str] | None = None
    ) -> RouterResponse | None:
        """An explicit no ends a pending action (SPN-2f), whether or not the model acts.

        Only when `words`, the user's whole turn, spoken or typed, is a plain refusal,
        and exactly one card is open in the session that raised it (SPN-14), and that
        card is what she just asked about: one her last reply raised or asked about
        again (`asked`, from the caller), or without that, one raised in the turn
        before this one. A no
        to some later question of hers is not a no to an older card. It is then
        cancelled as `cancel_pending_action` would, recorded with the turn's `via`.
        Otherwise None, and nothing is touched: the model reads the words. None too
        when the card was settled some other way meanwhile, by a tap say."""
        if ctx.via not in ("voice", "text") or not plain_no(words):
            return None
        if not await self._known(ctx.owner_id):
            return None
        now = self._clock()
        waiting = [
            a
            for a in await self._store.list_by_status(Status.PROPOSED)
            if a.owner_id == ctx.owner_id and a.session_id == ctx.session_id and a.expires_at > now
        ]
        if len(waiting) != 1:
            # None to cancel, or more than one: which one is for the model to ask.
            return None
        a = waiting[0]
        if (a.id not in asked) if asked is not None else (a.created_turn != ctx.turn - 1):
            # Not what her last words asked about. Without `asked`, only a card raised
            # in the turn before counts.
            return None
        r = await self.resolve(a.id, "cancel", ctx)
        # Only a cancel of its own: an earlier answer reads "cancelled" too.
        return r if r.status == "cancelled" and r.reason is None else None

    async def _by_own_words(self, d: ToolDefinition, args: JSON, ctx: ToolContext) -> bool:
        """Whether the owner's words this turn are the yes for this call (SPN-20). One
        per turn: a second gated call in the same turn gets a card."""
        if self._own_words_on is None or self._own_words_turn.get(ctx.session_id) == ctx.turn:
            return False
        try:
            enabled = await self._own_words_on() is True
        except Exception as e:
            logger.warning("reading the own-words setting raised %s", type(e).__name__)
            enabled = False
        verdict = own_words(d, args, ctx, enabled)
        if verdict is not None:
            # Why, by its code only, never the words: the answer is the card.
            logger.info("own words not the yes for %s: %s", d.name, verdict.reason.value)
            return False
        return True

    async def _approve_own_words(
        self, pending_id: str, ctx: ToolContext, guard: LoopGuard | None
    ) -> RouterResponse:
        """Approve a card just proposed, on the owner's own words (SPN-20): a decision
        `approved` by `own_words` via voice, then the confirmed path as after any yes:
        the deny floor, the precheck and fingerprint (SPN-2c), the one execution claim
        (SPN-17b) and the recorded outcome (SPN-17a). Only the later turn is skipped,
        and with it the card on a screen: the owner said what it does."""
        start = self._clock()
        self._own_words_turn[ctx.session_id] = ctx.turn
        a = await self._store.get_pending(pending_id)
        if a is None or a.status is not Status.PROPOSED:  # pragma: no cover - just written
            return _refused(Refusal.NOT_FOUND)
        by = DecisionIn(event="approved", decided_by=OWN_WORDS, via="voice")
        logger.info("pending action %s approved by the owner's own words", a.id)
        response = await _see_through(self._decide_and_run(a, Status.APPROVED, ctx, start, by=by))
        if guard is not None and response.status in ("failed", "unknown"):
            guard.record_result(a.tool, ok=False)
        return dataclasses.replace(
            response,
            own_words=True,
            pending_id=a.id,
            detail=_ON_THEIR_WORD if response.status == "succeeded" else response.detail,
        )

    async def _decide_and_run(
        self,
        a: PendingAction,
        verdict: Status,
        ctx: ToolContext,
        start: int,
        *,
        by: DecisionIn | None = None,
    ) -> RouterResponse:
        event = "rejected" if verdict is Status.REJECTED else "approved"
        decision = by if by is not None else _by(ctx, event)
        won = await self._move(a.id, Status.PROPOSED, verdict, decision, now=start)
        if not won or verdict is Status.REJECTED:
            # Lost a race, or cancelled: either way the stored row is the answer.
            return await self._audit_resolution(
                a, ctx, start, self._from_record(await self._reload(a))
            )
        return await self._audit_resolution(a, ctx, start, await self._execute_approved(a, ctx))

    async def _execute_approved(self, a: PendingAction, ctx: ToolContext) -> RouterResponse:
        approved = Status.APPROVED
        tool = self._get(a.tool)
        if a.args_cleared:  # pragma: no cover - retention clears settled actions only
            await self._move(
                a.id,
                approved,
                Status.FAILED,
                _by(ctx, "failed"),
                effect="none",
                detail="its details were cleared",
            )
            return _refused(Refusal.PRECONDITION_CHANGED, "its details were cleared")
        if tool is None:
            await self._move(
                a.id,
                approved,
                Status.FAILED,
                _by(ctx, "failed"),
                effect="none",
                detail="tool unavailable",
            )
            return _refused(Refusal.UNKNOWN_TOOL)

        denial = deny_floor.check(
            tool.definition,
            a.args,
            self._policy,
            ctx.granted_roots,
            read_only_roots=ctx.read_only_roots,
        )
        if denial is not None:
            await self._move(
                a.id,
                approved,
                Status.FAILED,
                _by(ctx, "denied_by_floor"),
                effect="none",
                detail=denial.reason,
            )
            return _refused(Refusal.DENIED_BY_FLOOR, denial.reason)

        pre = await self._precheck(tool, a.args, ctx)
        if not pre.ok:
            invalidated = _by(ctx, "invalidated")
            await self._move(
                a.id, approved, Status.INVALIDATED, invalidated, detail=pre.reason[:_DETAIL_LIMIT]
            )
            return _refused(Refusal.PRECONDITION_CHANGED, _tool_detail(pre.reason))

        # The precheck says the action can still run; the fingerprint says whether the
        # target is still what the user saw (an edited file must not be clobbered).
        try:
            token = _fingerprint(tool, a.args)
        except Exception as e:
            logger.warning("fingerprint of %s raised %s", a.tool, type(e).__name__)
            token = _NO_FINGERPRINT
        if token != a.precondition_token:
            invalidated = _by(ctx, "invalidated")
            await self._move(
                a.id, approved, Status.INVALIDATED, invalidated, detail=_TARGET_CHANGED
            )
            return _refused(Refusal.PRECONDITION_CHANGED, _TARGET_CHANGED)

        claimed = await self._move(a.id, approved, Status.EXECUTING, _by(ctx, "executing"))
        if not claimed:
            return self._from_record(await self._reload(a))
        # The caller sees this through (#3): see `resolve`.
        return await self._execute_claimed(tool, a, ctx)

    async def _execute_claimed(
        self, tool: _Registered, a: PendingAction, ctx: ToolContext
    ) -> RouterResponse:
        ctx = dataclasses.replace(ctx, action_id=a.id, proposed_ms=a.created_at)
        result = await self._run(tool, a.args, ctx)
        if result.effect == "unknown":
            # No answer from the provider: it may have gone, or may still be landing.
            # Only proof that it landed settles it now; anything else waits for a recheck.
            found, retry = await self._check(a, ctx.granted_roots)
            if not retry and found is not None and found.effect == "confirmed":
                await self._settled(a, Status.SUCCEEDED, "succeeded", found)
                return self._from_record(await self._reload(a))
            if retry or (found is not None and found.effect == "none"):
                await self._mark_unchecked(a)
                response = self._result_response(
                    tool, Status.UNKNOWN, result.content, result.error_kind
                )
                return dataclasses.replace(response, pending_id=a.id, detail=_STILL_CHECKING)
            # No check, or one that can never tell: recorded as before.
        status = final_status(result)
        won = await self._move(
            a.id,
            Status.EXECUTING,
            status,
            _by(ctx, status.value),
            effect=result.effect,
            detail=result.content[:_DETAIL_LIMIT],
        )
        logger.info("pending action %s finished: %s", a.id, status.value)
        if not won:
            return self._from_record(await self._reload(a))
        # She is shown the whole result, as on the direct path. The record keeps only its
        # start: handing her that copy cut a list of records to its first, mid-record.
        response = self._result_response(tool, status, result.content, result.error_kind)
        return dataclasses.replace(response, pending_id=a.id)

    async def represent(self, pending_id: str, ctx: ToolContext) -> RouterResponse:
        """Ask again for an expired or invalidated proposal (SPN-2b). Never automatic."""
        if not await self._known(ctx.owner_id):
            return _refused(Refusal.UNKNOWN_OWNER)
        start = self._clock()
        h = args_hash({"pending_id": pending_id})
        if ctx.via in _UNWATCHED:
            refusal = _refused(Refusal.UNATTENDED, _UNATTENDED)
            return await self._audit(ctx, start, "represent", h, "meta", "represent", refusal)
        a = await self._store.get_pending(pending_id)
        if a is None or a.owner_id != ctx.owner_id:
            return await self._audit(
                ctx, start, "represent", h, "meta", "represent", _refused(Refusal.NOT_FOUND)
            )
        response = await self._represent(a, ctx, start)
        return await self._audit_resolution(a, ctx, start, response, decision="represent")

    async def _represent(self, a: PendingAction, ctx: ToolContext, now: int) -> RouterResponse:
        if ctx.helper_id is not None and a.tool in HELPER_NEVER:
            # Her card, raised again in a helper's chat, would be a helper dispatching.
            return _refused(Refusal.NOT_FOR_HELPERS, _NOT_FOR_HELPERS)
        if a.session_id != ctx.session_id and not await self._proved_not_sent(a):
            return _refused(Refusal.WRONG_SESSION)
        a = await self._expire_if_due(a, now, ctx)
        if a.status not in REPRESENTABLE:
            return _refused(Refusal.NOT_REPRESENTABLE, a.status.value)
        # One card, one send, in every session: a resumed conversation keeps its id.
        taken = await self._taken_copy(a, now)
        if taken is not None:
            return _refused(
                Refusal.NOT_REPRESENTABLE,
                f"already asked for again as {taken.id}, which is {taken.status.value}",
            )
        if a.args_cleared:
            return _refused(
                Refusal.NOT_REPRESENTABLE,
                f"its details are kept for {RETENTION_DAYS} days after it settles; ask afresh",
            )
        tool = self._get(a.tool)
        if tool is None:
            return _refused(Refusal.UNKNOWN_TOOL)
        denial = deny_floor.check(
            tool.definition,
            a.args,
            self._policy,
            ctx.granted_roots,
            read_only_roots=ctx.read_only_roots,
        )
        if denial is not None:
            return _refused(Refusal.DENIED_BY_FLOOR, denial.reason)
        pre = await self._precheck(tool, a.args, ctx)
        if not pre.ok:
            return _refused(Refusal.PRECONDITION_FAILED, _tool_detail(pre.reason))
        return await self._propose(tool, a.args, ctx, represented_from=a.id)

    async def forget_settled_arguments(self) -> int:
        """Clear the full arguments of actions settled more than 30 days ago (#20).

        Run at startup and daily. The frozen record keeps a slim copy and its hash, so
        what was approved stays provable; the outcome, decisions and audit are kept."""
        now = self._clock()
        cleared = await self._store.clear_settled_args(
            settled_before_ms=now - RETENTION_MS, now_ms=now
        )
        if cleared:
            logger.info("cleared the arguments of %d settled action(s)", cleared)
        return cleared

    async def mark_delivered(self, pending_id: str, owner_id: str, surface: str) -> bool:
        """Record the first render of a pending action on a surface. Later calls do nothing."""
        a = await self._store.get_pending(pending_id)
        if a is None or a.owner_id != owner_id:
            return False
        return await self._store.mark_delivered(pending_id, owner_id, surface, self._clock())

    async def _proved_not_sent(self, a: PendingAction) -> bool:
        """A check proved it never went out (OPS-1). The session that raised it ended with
        the crash, so any of the owner's sessions may ask for a new card."""
        if a.status is not Status.INVALIDATED:
            return False
        decisions = await self._store.list_decisions(a.id)
        return bool(decisions) and decisions[-1].event == "recovered:not_sent"

    async def _taken_copy(self, a: PendingAction, now: int) -> PendingAction | None:
        """Another action in `a`'s family (the one it was asked from, up to the first,
        and every copy asked from any of them) that is open or has run, if any. A card
        that expired unanswered no longer counts, whatever its row still says."""
        root, seen = a, {a.id}
        while root.represented_from is not None and root.represented_from not in seen:
            up = await self._store.get_pending(root.represented_from)
            if up is None:
                break
            root = up
            seen.add(root.id)
        queue, family = [root], {root.id}
        while queue:
            m = queue.pop()
            if m.id != a.id and m.status in _TAKEN:
                if not (m.status is Status.PROPOSED and m.expires_at <= now):
                    return m
            for c in await self._store.list_represented_from(m.id):
                if c.id not in family:
                    family.add(c.id)
                    queue.append(c)
        return None

    async def recover(self, granted_roots: tuple[Path, ...]) -> list[Settled]:
        """At startup: settle interrupted actions. Never re-runs one (SPN-17a, OPS-1)."""
        self._granted = granted_roots
        settled: list[Settled] = []
        for a in await self._store.list_by_status(Status.APPROVED):
            await self._move(
                a.id,
                Status.APPROVED,
                Status.INVALIDATED,
                _system("interrupted"),
                detail="interrupted before it ran; ask again",
            )
            settled.append(Settled(id=a.id, outcome="interrupted"))
        for a in await self._store.list_by_status(Status.EXECUTING):
            done = await self._settle(a, granted_roots)
            if done is not None:
                settled.append(done)
        return settled

    async def in_doubt(self, since_ms: int) -> list[Settled]:
        """Each send settled since `since_ms` as never sent or unknown, and still so: not
        asked for again since. For telling the user when a notice may have been lost."""
        found: list[Settled] = []
        rows = await self._store.last_decisions_since(list(_IN_DOUBT), since_ms)
        for d in rows:
            if await self._store.list_represented_from(d.pending_id):
                continue  # asked for again: the user knows
            found.append(Settled(id=d.pending_id, outcome=_IN_DOUBT[d.event]))
        return found

    async def check_unchecked(self) -> list[Settled]:
        """Check again each action whose last check could not reach the provider."""
        settled: list[Settled] = []
        for a in await self._store.list_by_status(Status.EXECUTING):
            if a.effect_status != UNCHECKED:
                continue  # running now
            done = await self._settle(a, self._granted)
            if done is not None:
                settled.append(done)
        return settled

    async def _check(
        self, a: PendingAction, granted: tuple[Path, ...]
    ) -> tuple[ToolResult | None, bool]:
        """Ask the tool what happened to `a`. The flag says to try again later: the check
        could not reach the provider (it said so, raised, or timed out)."""
        ctx = ToolContext(
            owner_id=a.owner_id,
            session_id=a.session_id,
            turn=a.created_turn,
            call_id=f"recover-{a.id}",
            via="text",
            granted_roots=granted,
            action_id=a.id,
            proposed_ms=a.created_at,
        )
        tool = self._get(a.tool)
        if tool is None:
            return None, False
        try:
            # Bounded like a run: a confirm waits on this check when the answer was lost.
            limit = tool.definition.timeout_s
            result = await asyncio.wait_for(tool.reconcile(a.args, ctx), limit)
        except Exception as e:  # a check is best-effort by contract; a timeout is "can't tell"
            logger.warning("reconcile of %s raised %s", a.id, type(e).__name__)
            return None, True
        unreachable = result is not None and result.error_kind == UNREACHABLE
        return result, unreachable

    async def _settle(self, a: PendingAction, granted: tuple[Path, ...]) -> Settled | None:
        """Record what a check found. None: still can't tell, or another caller won."""
        result, retry = await self._check(a, granted)
        now = self._clock()
        if retry:
            pass  # could not reach the provider: whatever the result says, can't tell
        elif result is not None and result.effect == "confirmed":
            return await self._settled(a, Status.SUCCEEDED, "succeeded", result)
        elif result is not None and result.effect == "none":
            # The provider may not show a send at once (a search index catching up), so
            # "never went" counts only once the action has been quiet for a while.
            if now - (a.updated_at or 0) >= SETTLE_NONE_AFTER_MS:
                return await self._settled(a, Status.INVALIDATED, "not_sent", result, NOT_SENT)
            retry = True
        if retry and now < a.created_at + CHECK_FOR_MS:
            await self._mark_unchecked(a)
            return None
        return await self._settled(a, Status.UNKNOWN, "unknown", result)

    async def _mark_unchecked(self, a: PendingAction) -> None:
        """Leave `a` executing, to be checked again. Through the one writer, once."""
        if a.effect_status == UNCHECKED:
            return
        await self._move(
            a.id, Status.EXECUTING, Status.EXECUTING, _system(UNCHECKED), effect=UNCHECKED
        )

    async def _settled(
        self,
        a: PendingAction,
        status: Status,
        outcome: Outcome,
        result: ToolResult | None,
        detail: str | None = None,
    ) -> Settled | None:
        won = await self._move(
            a.id,
            Status.EXECUTING,
            status,
            _system(f"recovered:{outcome}"),
            effect=result.effect if result else "unknown",
            detail=detail or (result.content[:_DETAIL_LIMIT] if result else "interrupted mid-run"),
        )
        return Settled(id=a.id, outcome=outcome) if won else None

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _known(self, owner_id: str) -> bool:
        """Whether this owner exists. An unknown one is refused before anything is done or
        recorded: the audit row could not name them, and must not fail in its place (#4)."""
        if owner_id in self._owners:
            return True
        if await self._store.owner_known(owner_id):
            self._owners.add(owner_id)
            return True
        logger.warning("refused a call from an owner that does not exist")
        return False

    def _get(self, name: str) -> _Registered | None:
        tool, definition = self._registry.get(name), self._registry.definition(name)
        if tool is None or definition is None:
            return None
        return _Registered(tool, definition)

    async def _expire_if_due(self, a: PendingAction, now: int, ctx: ToolContext) -> PendingAction:
        if a.status is not Status.PROPOSED or now < a.expires_at:
            return a
        await self._move(
            a.id, Status.PROPOSED, Status.EXPIRED, _system("expired", ctx.via), now=now
        )
        return await self._reload(a)

    async def _move(
        self,
        pending_id: str,
        expected: Status,
        new: Status,
        decision: DecisionIn,
        *,
        now: int | None = None,
        effect: str | None = None,
        detail: str | None = None,
    ) -> bool:
        """Compare-and-set `expected` → `new`. False means another caller moved it first."""
        return await self._store.transition(
            pending_id,
            expected,
            new,
            now_ms=self._clock() if now is None else now,
            decision=decision,
            effect_status=effect,
            outcome_detail=detail,
        )

    async def _precheck(self, tool: Tool, args: JSON, ctx: ToolContext) -> Precondition:
        try:
            return await tool.precheck(args, ctx)
        except Exception as e:
            logger.warning("precheck of %s raised %s", tool.definition.name, type(e).__name__)
            return Precondition.failed(f"the check could not run ({type(e).__name__})")

    async def _run(self, tool: Tool, args: JSON, ctx: ToolContext) -> ToolResult:
        name = tool.definition.name
        try:
            return await asyncio.wait_for(tool.execute(args, ctx), tool.definition.timeout_s)
        except TimeoutError:
            logger.warning("tool %s timed out", name)
            return ToolResult(
                ok=False,
                content=f"timed out after {tool.definition.timeout_s:g}s",
                effect="unknown",
                error_kind="timeout",
            )
        except Exception as e:
            # The message can contain paths or content, so it is returned, never logged.
            logger.warning("tool %s raised %s", name, type(e).__name__)
            return ToolResult(
                ok=False,
                content=cleaned(str(e), "a tool's error")[:500],
                effect="unknown",
                error_kind=type(e).__name__,
            )

    def _result_response(
        self,
        tool: Tool,
        status: Status,
        content: str,
        error_kind: str | None,
        warning: str | None = None,
    ) -> RouterResponse:
        d = tool.definition
        found: Counts = ()
        if d.trusted_output:
            text = content
        else:
            text, found = wrap_counted(d.name, content, lossless=d.lossless_output)
        if d.lossless_output and content:
            note = _lossless_note(content)
            warning = "; ".join(w for w in (warning, note) if w) or None
        kind = _OUTCOME.get(status, "failed")
        return RouterResponse(
            status=kind, content=text, error_kind=error_kind, warning=warning, neutralized=found
        )

    def _from_record(self, a: PendingAction) -> RouterResponse:
        detail = a.outcome_detail or ""
        tool = self._get(a.tool)
        trusted = tool is not None and tool.definition.trusted_output
        found: Counts = ()
        if trusted or not detail:
            text = detail
        else:
            text, found = wrap_counted(a.tool, detail)
        match a.status:
            case Status.SUCCEEDED:
                return RouterResponse(
                    status="succeeded", pending_id=a.id, content=text, neutralized=found
                )
            case Status.FAILED:
                return RouterResponse(
                    status="failed", pending_id=a.id, content=text, neutralized=found
                )
            case Status.UNKNOWN:
                return RouterResponse(
                    status="unknown", pending_id=a.id, content=text, neutralized=found
                )
            case Status.REJECTED:
                return RouterResponse(status="cancelled", pending_id=a.id)
            case Status.APPROVED | Status.EXECUTING:
                return RouterResponse(status="in_progress", pending_id=a.id)
            case Status.EXPIRED:
                return _refused(Refusal.EXPIRED, "re-present it to ask again")
            case Status.SUPERSEDED:
                return _refused(Refusal.SUPERSEDED)
            case Status.INVALIDATED:
                return _refused(Refusal.PRECONDITION_CHANGED, _tool_detail(detail))
            case Status.PROPOSED:
                return RouterResponse(
                    status="confirmation_required", pending_id=a.id, summary=a.summary
                )
            case _:  # pragma: no cover - every Status is handled above
                raise RuntimeError(f"unhandled pending status {a.status.value}")

    async def _reload(self, a: PendingAction) -> PendingAction:
        fresh = await self._store.get_pending(a.id)
        if fresh is None:  # rows are never deleted
            raise RuntimeError(f"pending action {a.id} vanished")
        return fresh

    async def _audit(
        self,
        ctx: ToolContext,
        start: int,
        tool: str,
        h: str,
        risk: str,
        decision: str,
        response: RouterResponse,
    ) -> RouterResponse:
        now = self._clock()
        keyed = (
            h
            if h == _UNHASHABLE
            else "hmac:" + hmac.new(self._audit_key, h.encode(), hashlib.sha256).hexdigest()
        )
        await self._store.append_audit(
            AuditRecord(
                owner_id=ctx.owner_id,
                call_id=ctx.call_id,
                tool=tool,
                args_hash=keyed,
                risk=risk,
                decision=decision,
                outcome=response.reason.value if response.reason else response.status,
                duration_ms=max(0, now - start),
                session_id=ctx.session_id,
                at=now,
                neutralized=as_json(response.neutralized),
            )
        )
        if response.neutralized:
            # Counts only: what was removed is someone else's hostile words (SPN-6).
            # Not said to the user: a fence is common in harmless text.
            logger.info(
                "neutralized %s in the result of %s", shown_counts(response.neutralized), tool
            )
        return response

    async def _audit_resolution(
        self,
        a: PendingAction,
        ctx: ToolContext,
        start: int,
        response: RouterResponse,
        decision: str = "resolve",
    ) -> RouterResponse:
        response = dataclasses.replace(response, tool=a.tool)
        return await self._audit(ctx, start, a.tool, a.args_hash, a.risk.value, decision, response)
