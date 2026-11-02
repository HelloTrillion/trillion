"""One turn of the agent: stream the model, run its tool calls through the spine, repeat (BRN-3).

The loop never calls a tool. It hands each call to the router and gives the
model back exactly what the router recorded. Every message is saved as it
completes, so an interruption or a crash leaves a history that is still true.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field

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
from trillion.spine.contract import JSON, Risk, ToolCall, ToolContext, ToolDefinition
from trillion.spine.loop_guard import Limits, LoopGuard, Verdict
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router, RouterResponse

logger = logging.getLogger(__name__)

# The board tools' category (tools/workspace.py), named here so the loop needs nothing
# from the workspace.
WORKSPACE = "workspace"
# Staying quiet (tools/quiet.py, VOI-7), named here for the same reason: by its name and
# its category, so no other tool can end a turn in silence.
QUIET = "stay_quiet"
QUIET_CATEGORY = "quiet"
# Why a call to stay quiet was not run: it said something else in the same round.
ALONE = (
    "call stay_quiet alone, with no text and no other tool; what you wrote or asked for "
    "this round is your reply"
)

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
    # The arguments as the model called it: a read a widget remembers is replayed with them.
    args: JSON = field(default_factory=dict)


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
    # With stop "quiet": the kind of silence she chose, "listening" or "done".
    quiet: str | None = None


# Asked of each call whether it runs in the background: the immediate answer if it does
# (or a refusal), None to run it in the turn (the background reads spec).
Background = Callable[[ToolUse, ToolDefinition, ToolContext], Awaitable[RouterResponse | None]]


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
    # Async, so the host may look at the shared folders off the event loop (the
    # shared folders spec): each call gets them as they are at that call.
    ctx: Callable[[str], Awaitable[ToolContext]],
    emit: Callable[[LoopEvent], Awaitable[None]],
    save: Callable[[Msg], Awaitable[None]],
    limits: Limits | None = None,
    first: AsyncIterator[ModelEvent] | None = None,
    heard: Callable[[str], str | None] | None = None,
    background: Background | None = None,
) -> TurnDone:
    """Run until the model stops asking for tools or a limit stops it.

    `history` ends with the user's message. `save` is called once for each message
    the turn adds; `emit` must not raise.

    In each round, her board's calls run after every other call of the round, whether
    the round runs in parallel (reads only) or in order (anything else); the answers
    still go back in the order she asked.

    `first`, when given, stands in for the first model call: a draft started while the
    user was finishing (VOI-12).

    `heard`, for a spoken turn, cuts the text to what the user heard when it is
    interrupted (D8): given what she wrote this round, it returns the part heard, or
    None when the user read it all instead (her voice failed or was muted).

    `background`, when given, may take a slow read off the turn: the call is answered
    at once and its result arrives later, outside this turn."""
    guard = LoopGuard(limits)
    # Whether she has said anything this turn: after that, a silence is not one.
    spoke = False
    tools = tool_schemas(registry)
    pricing = provider.pricing()
    messages = list(history)
    usage = Usage()
    cost: float | None = 0.0 if pricing is not None else None

    def done(stop: str, error_kind: str | None = None, quiet: str | None = None) -> TurnDone:
        return TurnDone(stop=stop, usage=usage, cost_usd=cost, error_kind=error_kind, quiet=quiet)

    async def add(msg: Msg) -> None:
        messages.append(msg)
        await save(msg)

    async def call(
        u: ToolUse, refuse: Refusal | None = None, not_alone: bool = False
    ) -> ToolResultBlock:
        definition = registry.definition(u.name)
        risk = definition.risk if definition is not None else None
        await emit(
            ToolStarted(
                call_id=u.id, name=u.name, risk=risk.value if risk is not None else "unknown"
            )
        )
        if refuse is not None:
            response = RouterResponse(status="refused", reason=refuse, detail="cost ceiling")
        elif not_alone and quiet(u):
            # Silence with words or work beside it is not silence: never run.
            response = RouterResponse(
                status="refused", reason=Refusal.PRECONDITION_FAILED, detail=ALONE
            )
        elif u.invalid is not None:
            response = RouterResponse(
                status="refused", reason=Refusal.INVALID_ARGS, detail=u.invalid
            )
        else:
            context = await ctx(u.id)
            handed = None
            if background is not None and definition is not None:
                handed = await background(u, definition, context)
            if handed is not None:
                response = handed
            else:
                response = await router.dispatch(ToolCall(name=u.name, args=u.args), context, guard)
        await emit(ToolEnded(call_id=u.id, name=u.name, response=response, args=u.args))
        return ToolResultBlock(
            tool_use_id=u.id,
            content=response.to_model(),
            is_error=response.status in ("failed", "refused"),
        )

    def board(u: ToolUse) -> bool:
        d = registry.definition(u.name)
        return d is not None and d.category == WORKSPACE

    def quiet(u: ToolUse) -> bool:
        d = registry.definition(u.name)
        return u.name == QUIET and d is not None and d.category == QUIET_CATEGORY

    async def run_all(
        batch: Sequence[ToolUse], refuse: Refusal | None, not_alone: bool = False
    ) -> list[Block]:
        reads_only = all(
            (d := registry.definition(u.name)) is not None and d.risk is Risk.READ for u in batch
        )
        # Her board's calls go after the rest of the round: a widget she saves beside the
        # read it shows remembers that read only once the read has ended (live widgets).
        order = [i for i, u in enumerate(batch) if not board(u)]
        order += [i for i, u in enumerate(batch) if board(u)]
        out: dict[int, Block] = {}
        if reads_only and refuse is None:
            first = [i for i in order if not board(batch[i])]
            for group in (first, order[len(first) :]):
                done = await asyncio.gather(*(call(batch[i], not_alone=not_alone) for i in group))
                out.update(zip(group, done, strict=True))
        else:
            # Anything that writes or proposes runs in the order the model asked.
            for i in order:
                out[i] = await call(batch[i], refuse, not_alone)
        # Answered in the order she asked.
        return [out[i] for i in range(len(batch))]

    while True:
        if guard.next_round().verdict is Verdict.STOP:
            return done("rounds")

        text: list[str] = []
        # Whether any of this round's text has been shown yet.
        shown = False
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
                    if shown:
                        await emit(ReplyDelta(text=ev.text))
                    elif "".join(text).strip():
                        # Whitespace alone is shown only with the words it leads to:
                        # before a lone stay_quiet there are none (VOI-7).
                        shown = True
                        await emit(ReplyDelta(text="".join(text)))
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
                kept = kept_of(said, heard(said) if heard is not None else None)
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
        # Staying quiet is the whole of a round or no part of it (VOI-7).
        not_alone = bool("".join(text).strip()) or len(uses) > 1
        answered: list[Block] = []

        # A lone silence keeps no whitespace written before it.
        written = "" if not not_alone and quiet(uses[0]) else "".join(text)

        async def round_trip() -> None:
            await add(_assistant(written, uses, raw))  # noqa: B023
            results = await run_all(uses, refuse, not_alone)  # noqa: B023
            answered.extend(results)  # noqa: B023
            await add(Msg(role="user", blocks=tuple(results)))

        if await _finish(round_trip()):
            raise asyncio.CancelledError
        if over_budget:
            return done("cost")
        if not not_alone and quiet(uses[0]):
            (result,) = answered
            if isinstance(result, ToolResultBlock) and not result.is_error:
                if spoke:
                    # She already said something this turn (a line before a read): the
                    # turn ends as one she spoke in, never as a silence. Nothing more
                    # is asked of the model; the call and its answer stay saved.
                    return done("end")
                # She chose to say nothing, and the call and its answer are saved as a
                # pair like any other: later turns see the silence was hers. The session
                # decides what the room does next.
                return done("quiet", quiet=str(uses[0].args.get("kind")))
        spoke = spoke or bool("".join(text).strip())


def kept_of(written: str, heard: str | None) -> str:
    """The one rule for what an interrupted reply keeps of its round (VOI-9, D8).

    Read (`heard` is None): all she wrote. Heard: the heard part and "…"; written but
    never said is dropped. With nothing heard, the marker alone shows she began."""
    said = written if heard is None else cut(heard)
    return said + INTERRUPTED if said else INTERRUPTED.strip()


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
