"""The only caller of `Tool.execute`. Every guarantee in the spine is enforced here or below.

Responses to the model are built from what was recorded, never from what a
tool claims about itself (SPN-19). Every call is audited, refusals included.
Logs carry names, ids, statuses and exception class names only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from trillion.spine import deny_floor
from trillion.spine.audit import AuditRecord
from trillion.spine.clock import Clock, system_clock
from trillion.spine.confirm import (
    CANCEL_TOOL,
    CONFIRM_TOOL,
    EXPIRY_MS,
    REPRESENTABLE,
    PendingAction,
    Refusal,
    Rejection,
    Status,
    decide,
)
from trillion.spine.contract import (
    GATED,
    JSON,
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
from trillion.spine.neutralize import neutralize
from trillion.spine.registry import ToolRegistry
from trillion.spine.retention import RETENTION_DAYS, RETENTION_MS
from trillion.spine.store import DecisionIn, SpineStore
from trillion.spine.untrusted import wrap

logger = logging.getLogger(__name__)

ResponseStatus = Literal[
    "succeeded",
    "failed",
    "unknown",
    "refused",
    "confirmation_required",
    "cancelled",
    "in_progress",
]

_DETAIL_LIMIT = 2000
_TOOL_DETAIL_LIMIT = 300
_SUMMARY_LIMIT = 200
_UNHASHABLE = "unhashable"
_TARGET_CHANGED = "the target changed since you were asked"
_NO_FINGERPRINT = "\x00fingerprint-failed"  # never equal to a recorded token
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

    def to_model(self) -> str:
        """The JSON the model sees. Only fields that carry information."""
        body: dict[str, Any] = {"status": self.status}
        for key in ("content", "pending_id", "summary", "detail", "error_kind", "warning"):
            value = getattr(self, key)
            if value:
                body[key] = value
        if self.reason is not None:
            body["reason"] = self.reason.value
        return json.dumps(body, ensure_ascii=False)


def _refused(reason: Refusal, detail: str = "") -> RouterResponse:
    return RouterResponse(status="refused", reason=reason, detail=detail)


def _tool_detail(text: str) -> str:
    """Text a tool wrote (a precheck reason, say) is defanged and cut before the model sees it."""
    return neutralize(text)[0][:_TOOL_DETAIL_LIMIT]


def _safe_summary(text: str) -> str:
    """The approval card is what the user consents to (SPN-2a). Whatever a tool put in it
    (a model-chosen file name, say) is neutralized, kept to one line with no control or
    format characters (no bidi overrides), and cut to a length a card can show."""
    clean = neutralize(text)[0]
    for ws in ("\r\n", "\n", "\r", "\t"):
        clean = clean.replace(ws, " ")
    clean = "".join(c for c in clean if unicodedata.category(c) not in ("Cc", "Cf"))
    return clean[:_SUMMARY_LIMIT]


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


class Router:
    def __init__(
        self,
        registry: ToolRegistry,
        store: SpineStore,
        *,
        policy: deny_floor.DenyPolicy,
        clock: Clock = system_clock,
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._registry = registry
        self._store = store
        self._policy = policy
        self._clock = clock
        self._new_id = new_id

    # ── The normal path ──────────────────────────────────────────────────

    async def execute(
        self, call: ToolCall, ctx: ToolContext, guard: LoopGuard | None = None
    ) -> RouterResponse:
        start = self._clock()
        h = _safe_hash(call.args)

        async def audited(
            risk: str, r: RouterResponse, decision: str = "refused"
        ) -> RouterResponse:
            return await self._audit(ctx, start, call.name, h or _UNHASHABLE, risk, decision, r)

        if call.name in (CONFIRM_TOOL.name, CANCEL_TOOL.name):
            pid = call.args.get("pending_id")
            extra = set(call.args) - {"pending_id"}
            if not isinstance(pid, str) or not pid or extra:
                return await audited("meta", _refused(Refusal.INVALID_ARGS, "pending_id only"))
            decision: Literal["confirm", "cancel"] = (
                "confirm" if call.name == CONFIRM_TOOL.name else "cancel"
            )
            # A tap belongs to one action, not to the turn it happened in: the model can
            # never pass one on. Only the UI's direct `resolve` call carries `via="tap"`.
            if ctx.via == "tap":
                ctx = dataclasses.replace(ctx, via="text")
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
        if tool is None:
            return await audited("unknown", _refused(Refusal.UNKNOWN_TOOL))
        risk = tool.definition.risk.value
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

        denial = deny_floor.check(tool.definition, call.args, self._policy, ctx.granted_roots)
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
            return await audited(risk, response, "proposed" if proposed else "refused")

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
            summary = _safe_summary(tool.describe(frozen).text)
            token = _fingerprint(tool, frozen)
        except Exception as e:
            logger.warning("describing %s raised %s", name, type(e).__name__)
            return _refused(Refusal.PRECONDITION_FAILED, "the action could not be described")
        for old in await self._store.open_for_target(ctx.owner_id, ctx.session_id, name, target):
            await self._move(old.id, Status.PROPOSED, Status.SUPERSEDED, _by(ctx, "superseded"))
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
        )
        await self._store.insert_pending(action, _by(ctx, "proposed"))
        logger.info("pending action %s proposed for %s", action.id, name)
        return RouterResponse(
            status="confirmation_required", pending_id=action.id, summary=action.summary
        )

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
        start = self._clock()
        a = await self._store.get_pending(pending_id)
        if a is None or a.owner_id != ctx.owner_id:
            meta = CONFIRM_TOOL.name if decision == "confirm" else CANCEL_TOOL.name
            h = args_hash({"pending_id": pending_id})
            return await self._audit(
                ctx, start, meta, h, "meta", "resolve", _refused(Refusal.NOT_FOUND)
            )

        a = await self._expire_if_due(a, start, ctx)
        verdict = decide(a, decision, ctx, start)
        if isinstance(verdict, Rejection):
            if verdict.reason is Refusal.ALREADY_RESOLVED:
                return await self._audit_resolution(a, ctx, start, self._from_record(a))
            response = _refused(verdict.reason, verdict.detail)
            return await self._audit_resolution(a, ctx, start, response)

        event = "rejected" if verdict is Status.REJECTED else "approved"
        won = await self._move(a.id, Status.PROPOSED, verdict, _by(ctx, event), now=start)
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

        denial = deny_floor.check(tool.definition, a.args, self._policy, ctx.granted_roots)
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

        result = await self._run(tool, a.args, ctx)
        status = final_status(result)
        await self._move(
            a.id,
            Status.EXECUTING,
            status,
            _by(ctx, status.value),
            effect=result.effect,
            detail=result.content[:_DETAIL_LIMIT],
        )
        logger.info("pending action %s finished: %s", a.id, status.value)
        return self._from_record(await self._reload(a))

    async def represent(self, pending_id: str, ctx: ToolContext) -> RouterResponse:
        """Ask again for an expired or invalidated proposal (SPN-2b). Never automatic."""
        start = self._clock()
        a = await self._store.get_pending(pending_id)
        if a is None or a.owner_id != ctx.owner_id:
            h = args_hash({"pending_id": pending_id})
            return await self._audit(
                ctx, start, "represent", h, "meta", "represent", _refused(Refusal.NOT_FOUND)
            )
        response = await self._represent(a, ctx, start)
        return await self._audit_resolution(a, ctx, start, response, decision="represent")

    async def _represent(self, a: PendingAction, ctx: ToolContext, now: int) -> RouterResponse:
        if a.session_id != ctx.session_id:
            return _refused(Refusal.WRONG_SESSION)
        a = await self._expire_if_due(a, now, ctx)
        if a.status not in REPRESENTABLE:
            return _refused(Refusal.NOT_REPRESENTABLE, a.status.value)
        if a.args_cleared:
            return _refused(
                Refusal.NOT_REPRESENTABLE,
                f"its details are kept for {RETENTION_DAYS} days after it settles; ask afresh",
            )
        tool = self._get(a.tool)
        if tool is None:
            return _refused(Refusal.UNKNOWN_TOOL)
        denial = deny_floor.check(tool.definition, a.args, self._policy, ctx.granted_roots)
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

    async def recover(self, granted_roots: tuple[Path, ...]) -> list[str]:
        """At startup: reconcile interrupted actions. Never re-runs one (SPN-17a)."""
        touched: list[str] = []
        for a in await self._store.list_by_status(Status.APPROVED):
            await self._move(
                a.id,
                Status.APPROVED,
                Status.INVALIDATED,
                _system("interrupted"),
                detail="interrupted before it ran; ask again",
            )
            touched.append(a.id)
        for a in await self._store.list_by_status(Status.EXECUTING):
            ctx = ToolContext(
                owner_id=a.owner_id,
                session_id=a.session_id,
                turn=a.created_turn,
                call_id=f"recover-{a.id}",
                via="text",
                granted_roots=granted_roots,
            )
            tool = self._get(a.tool)
            result: ToolResult | None = None
            if tool is not None:
                try:
                    result = await tool.reconcile(a.args, ctx)
                except Exception as e:  # reconcile is best-effort by contract
                    logger.warning("reconcile of %s raised %s", a.id, type(e).__name__)
            status = final_status(result) if result is not None else Status.UNKNOWN
            await self._move(
                a.id,
                Status.EXECUTING,
                status,
                _system(f"recovered:{status.value}"),
                effect=result.effect if result else "unknown",
                detail=result.content[:_DETAIL_LIMIT] if result else "interrupted mid-run",
            )
            touched.append(a.id)
        return touched

    # ── Helpers ──────────────────────────────────────────────────────────

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
                content=neutralize(str(e))[0][:500],
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
        if d.trusted_output:
            shown = content
        else:
            shown = wrap(d.name, content, lossless=d.lossless_output)
        if d.lossless_output and content:
            note = _lossless_note(content)
            warning = "; ".join(w for w in (warning, note) if w) or None
        kind = _OUTCOME.get(status, "failed")
        return RouterResponse(status=kind, content=shown, error_kind=error_kind, warning=warning)

    def _from_record(self, a: PendingAction) -> RouterResponse:
        detail = a.outcome_detail or ""
        tool = self._get(a.tool)
        trusted = tool is not None and tool.definition.trusted_output
        shown = detail if trusted or not detail else wrap(a.tool, detail)
        match a.status:
            case Status.SUCCEEDED:
                return RouterResponse(status="succeeded", pending_id=a.id, content=shown)
            case Status.FAILED:
                return RouterResponse(status="failed", pending_id=a.id, content=shown)
            case Status.UNKNOWN:
                return RouterResponse(status="unknown", pending_id=a.id, content=shown)
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
        await self._store.append_audit(
            AuditRecord(
                owner_id=ctx.owner_id,
                call_id=ctx.call_id,
                tool=tool,
                args_hash=h,
                risk=risk,
                decision=decision,
                outcome=response.reason.value if response.reason else response.status,
                duration_ms=max(0, now - start),
                session_id=ctx.session_id,
                at=now,
            )
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
        return await self._audit(ctx, start, a.tool, a.args_hash, a.risk.value, decision, response)
