"""One turn of the agent: stream the model, run its tool calls through the spine, repeat (BRN-3).

The loop never calls a tool. It hands each call to the router and gives the
model back exactly what the router recorded. Every message is saved as it
completes, so an interruption or a crash leaves a history that is still true.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass

from trillion.brain.types import (
    Block,
    Done,
    ModelEvent,
    ModelProvider,
    Msg,
    ProviderError,
    ProviderRaw,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSchema,
    ToolUse,
    ToolUseBlock,
    Usage,
)
from trillion.spine.confirm import Refusal
from trillion.spine.contract import Risk, ToolCall, ToolContext
from trillion.spine.loop_guard import Limits, LoopGuard, Verdict
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router, RouterResponse

logger = logging.getLogger(__name__)

MAX_TOKENS = 4096
INTERRUPTED = " [interrupted]"


@dataclass(frozen=True, kw_only=True)
class ReplyDelta:
    text: str


@dataclass(frozen=True, kw_only=True)
class ToolStarted:
    call_id: str
    name: str
    risk: str


@dataclass(frozen=True, kw_only=True)
class ToolEnded:
    call_id: str
    name: str
    response: RouterResponse


@dataclass(frozen=True, kw_only=True)
class CallTimed:
    """One model call is over: when it started, its first token and its end, in the
    event loop's clock, and what it spent (ONB-11)."""

    started: float
    first_token: float | None
    ended: float
    usage: Usage


LoopEvent = ReplyDelta | ToolStarted | ToolEnded | CallTimed


@dataclass(frozen=True, kw_only=True)
class TurnDone:
    # "end" and "max_tokens" come from the model; the rest are why the loop stopped it.
    stop: str
    usage: Usage
    cost_usd: float | None = None
    error_kind: str | None = None


def tool_schemas(registry: ToolRegistry) -> list[ToolSchema]:
    return [
        ToolSchema(name=d.name, description=d.description, input_schema=d.input_schema)
        for d in registry.definitions()
    ]


async def run_turn(
    *,
    provider: ModelProvider,
    router: Router,
    registry: ToolRegistry,
    system: Sequence[SystemBlock],
    history: Sequence[Msg],
    ctx: Callable[[str], ToolContext],
    emit: Callable[[LoopEvent], Awaitable[None]],
    save: Callable[[Msg], Awaitable[None]],
    limits: Limits | None = None,
    first: AsyncIterator[ModelEvent] | None = None,
    heard: Callable[[str], str] | None = None,
) -> TurnDone:
    """Run until the model stops asking for tools or a limit stops it.

    `history` ends with the user's message. `save` is called once for each message
    the turn adds; `emit` must not raise.

    `first`, when given, stands in for the first model call: a draft started while the
    user was finishing (VOI-12).

    `heard`, for a spoken turn, cuts the text to what the user heard when it is
    interrupted (D8): given what she wrote this round, it returns the part heard."""
    guard = LoopGuard(limits)
    tools = tool_schemas(registry)
    pricing = provider.pricing()
    messages = list(history)
    usage = Usage()
    cost: float | None = 0.0 if pricing is not None else None

    def done(stop: str, error_kind: str | None = None) -> TurnDone:
        return TurnDone(stop=stop, usage=usage, cost_usd=cost, error_kind=error_kind)

    async def add(msg: Msg) -> None:
        messages.append(msg)
        await save(msg)

    async def call(u: ToolUse, refuse: Refusal | None = None) -> ToolResultBlock:
        definition = registry.definition(u.name)
        risk = definition.risk if definition is not None else None
        await emit(
            ToolStarted(
                call_id=u.id, name=u.name, risk=risk.value if risk is not None else "unknown"
            )
        )
        if refuse is not None:
            response = RouterResponse(status="refused", reason=refuse, detail="cost ceiling")
        elif u.invalid is not None:
            response = RouterResponse(
                status="refused", reason=Refusal.INVALID_ARGS, detail=u.invalid
            )
        else:
            response = await router.dispatch(ToolCall(name=u.name, args=u.args), ctx(u.id), guard)
        await emit(ToolEnded(call_id=u.id, name=u.name, response=response))
        return ToolResultBlock(
            tool_use_id=u.id,
            content=response.to_model(),
            is_error=response.status in ("failed", "refused"),
        )

    async def run_all(batch: Sequence[ToolUse], refuse: Refusal | None) -> list[Block]:
        reads_only = all(
            (d := registry.definition(u.name)) is not None and d.risk is Risk.READ for u in batch
        )
        if reads_only and refuse is None:
            return list(await asyncio.gather(*(call(u) for u in batch)))
        # Anything that writes or proposes runs in the order the model asked.
        return [await call(u, refuse) for u in batch]

    while True:
        if guard.next_round().verdict is Verdict.STOP:
            return done("rounds")

        text: list[str] = []
        uses: list[ToolUse] = []
        stop = "other"
        spent = Usage()
        raw: ProviderRaw | None = None
        clock = asyncio.get_running_loop().time
        started = clock()
        # Only the first round may use the given stream; later rounds ask the model.
        source = (
            first
            if first is not None
            else provider.stream(
                system=system, messages=messages, tools=tools, max_tokens=MAX_TOKENS
            )
        )
        first = None
        first_token: float | None = None
        try:
            async for ev in source:
                if first_token is None and isinstance(ev, TextDelta | ToolUse):
                    first_token = clock()
                if isinstance(ev, TextDelta):
                    text.append(ev.text)
                    await emit(ReplyDelta(text=ev.text))
                elif isinstance(ev, ToolUse):
                    uses.append(ev)
                elif isinstance(ev, Done):
                    stop = ev.stop_reason
                    spent = spent + ev.usage
                    raw = ev.raw
        except asyncio.CancelledError:
            # What the user already read, or heard, is kept and marked; a half-formed
            # tool call is dropped, because it never ran.
            if text:
                said = "".join(text)
                if heard is not None:
                    # Written but never said is dropped. With nothing heard, the marker
                    # alone shows she began.
                    said = cut(heard(said))
                kept = said + INTERRUPTED if said else INTERRUPTED.strip()
                await asyncio.shield(add(_assistant(kept, [], None)))
            raise
        except ProviderError as e:
            logger.warning("model provider %s failed: %s", provider.name, e.kind)
            if text:
                await add(_assistant("".join(text), [], None))
            return done("provider_error", e.kind)

        await emit(CallTimed(started=started, first_token=first_token, ended=clock(), usage=spent))
        usage = usage + spent
        over_budget = False
        if pricing is not None and cost is not None:
            round_cost = pricing.cost(spent)
            cost += round_cost
            over_budget = guard.add_cost(round_cost).verdict is Verdict.STOP

        if stop == "refusal":
            # The provider declined. Whatever text arrived is kept; nothing it asked
            # for is run, and the turn says so instead of ending in silence.
            if text:
                await add(_assistant("".join(text), [], None))
            return done("refusal")
        if stop == "max_tokens" and uses:
            # Cut off mid-reply: the last tool call's arguments may be incomplete.
            # A truncated write is worse than no write, so none of them run.
            if text:
                await add(_assistant("".join(text), [], None))
            return done("max_tokens")
        if not text and not uses:
            # Nothing came back. That is said plainly rather than shown as a silent
            # success: a small model can spend its whole reply thinking.
            return done("max_tokens" if stop == "max_tokens" else "empty")
        if not uses:
            await add(_assistant("".join(text), uses, raw))
            return done("max_tokens" if stop == "max_tokens" else "end")

        # A tool call and its answer are saved as a pair, and nothing can come between
        # them: not one interrupt, not several, not the tab closing. A call left
        # unanswered makes the saved history unusable on every later turn. Stopping the
        # reply is also not cancelling the work (VOI-15c): calls handed to the spine
        # finish and are recorded before the turn ends.
        refuse = Refusal.LOOP_GUARD if over_budget else None

        async def round_trip() -> None:
            await add(_assistant("".join(text), uses, raw))  # noqa: B023
            results = await run_all(uses, refuse)  # noqa: B023
            await add(Msg(role="user", blocks=tuple(results)))

        if await _finish(round_trip()):
            raise asyncio.CancelledError
        if over_budget:
            return done("cost")


def cut(heard: str) -> str:
    """What a spoken reply keeps of a round when it is cut off: the heard part, then "…"
    (D8). The page shows the same."""
    return f"{heard}…" if heard else ""


async def _finish(work: Awaitable[None]) -> bool:
    """Run `work` to the end even if this task is cancelled meanwhile, however often.

    Returns whether a cancellation arrived, so the caller can honour it afterwards."""
    task = asyncio.ensure_future(work)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():  # the work itself was cancelled from outside
                raise
            cancelled = True
    task.result()
    return cancelled


def _assistant(text: str, uses: Sequence[ToolUse], raw: ProviderRaw | None) -> Msg:
    """`raw` is kept only for a turn saved exactly as the provider returned it."""
    blocks: list[Block] = [TextBlock(text=text)] if text else []
    blocks.extend(ToolUseBlock(id=u.id, name=u.name, args=u.args) for u in uses)
    return Msg(role="assistant", blocks=tuple(blocks), raw=raw)
