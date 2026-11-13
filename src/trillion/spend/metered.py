"""The metered providers: the one way a paid call is made (BRN-7).

`MeteredModel`, `MeteredVoice` and `MeteredHearing` wrap a model, a voice and a
recognizer, with the same interface as what they wrap, so nothing that uses them
changes shape. The runtime hands out providers only through them.

**Before a call** the wrapper asks the gate, which raises `ModelCapped` or
`VoiceCapped` at a hard cap, before anything is sent. A call already running is never
stopped: the check is made only before a call.

**After a call** (a model stream's `Done`, a sentence's audio, a hearing stream
closed) it records one usage row, priced on its own and never as part of a sum, with
the caller and conversation of `caller.current()`, and the ledger counts it. A model
call that may cost and reports no usage, or stops before its `Done` (a barge-in, a
dropped draft, an error partway), is still recorded once: with the usage its provider
reported so far, else estimated at four characters a token of what was sent and
streamed. A row
that cannot be written is logged (its kind only, never an amount) and still counted,
so the caps hold for the rest of this run.

Who calls is read when the call starts, so a stream read on another task is still
counted for whoever started it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import json
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from typing import Any, Protocol

from trillion.brain.types import (
    ContextBlock,
    Done,
    ModelEvent,
    ModelProvider,
    Msg,
    Pricing,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSchema,
    ToolUse,
    ToolUseBlock,
    Usage,
)
from trillion.spend import prices
from trillion.spend.caller import Call, current
from trillion.spend.gate import UNCAPPED, Gate
from trillion.spend.ledger import Ledger, Row
from trillion.spend.types import Unit
from trillion.voice.types import (
    CAPTURE,
    AudioFormat,
    Choice,
    Heard,
    STTProvider,
    STTStream,
    TTSProvider,
    Voice,
    Where,
)

log = logging.getLogger(__name__)

# The preference that holds the user's own prices (see `prices.parse_user_prices`).
PREF = "spend_prices"

# Bytes in one second of what the page sends: 16-bit samples.
_SECOND_BYTES = CAPTURE.sample_rate * CAPTURE.channels * 2


class UsageSink(Protocol):
    async def record_usage(
        self,
        *,
        owner_id: str,
        conversation_id: str | None,
        provider: str,
        model: str,
        usage: Usage,
        cost_usd: float | None,
        now_ms: int,
        provider_id: str | None = None,
        caller: Any = "conversation",
        units: Unit = "tokens",
    ) -> None: ...


class PriceBook:
    """The user's own prices (preference `spend_prices`), held in memory like the
    gate's caps: read by `reload` at startup and again after every write to them.
    Until read, there are none, and the price table prices everything."""

    def __init__(self, source: Callable[[], Awaitable[Any]] | None) -> None:
        self._source = source
        self._rates: dict[tuple[str, str], prices.Rate] = {}

    async def reload(self) -> None:
        if self._source is not None:
            self._rates = prices.parse_user_prices(await self._source())

    def rates(self) -> prices.UserPrices:
        return self._rates


# How long a caller's `ready` may take before its row is written without its
# conversation.
READY_TIMEOUT_S = 5.0
# Far longer than one usage row takes to write; one not written by then won't be.
DRAIN_S = 5.0


class Meter:
    """What every metered provider shares: the gate it asks, the ledger it counts in,
    the store it records to, the prices and the clock."""

    def __init__(
        self,
        *,
        gate: Gate,
        ledger: Ledger,
        store: UsageSink,
        clock: Callable[[], int],
        prices: PriceBook | None = None,
        owner_id: str = "local",
    ) -> None:
        self.gate = gate
        self.ledger = ledger
        self.store = store
        self.clock = clock
        self.prices = prices if prices is not None else PriceBook(None)
        self.owner_id = owner_id
        self.ready_timeout_s = READY_TIMEOUT_S
        # Records still being written, held until done: a cancelled caller no longer
        # holds them, and `drain` waits for them before the database closes.
        self._recording: set[asyncio.Future[None]] = set()

    # A provider already metered is wrapped again only for another provider id: what
    # it wraps is wrapped, never the wrapper, so a call is never counted twice.

    def model(self, inner: ModelProvider, provider_id: str | None) -> MeteredModel:
        if isinstance(inner, MeteredModel):
            if inner.provider_id == provider_id:
                return inner
            inner = inner.inner
        return MeteredModel(inner, provider_id, self)

    def voice(self, inner: TTSProvider, provider_id: str | None) -> MeteredVoice:
        if isinstance(inner, MeteredVoice):
            if inner.provider_id == provider_id:
                return inner
            inner = inner.inner
        return MeteredVoice(inner, provider_id, self)

    def hearing(self, inner: STTProvider, provider_id: str | None) -> MeteredHearing:
        if isinstance(inner, MeteredHearing):
            if inner.provider_id == provider_id:
                return inner
            inner = inner.inner
        return MeteredHearing(inner, provider_id, self)

    def price(self, provider_id: str, model: str, usage: Usage | float) -> float | None:
        return prices.price(provider_id, model, usage, self.prices.rates())

    async def _ready(self, call: Call) -> bool:
        """The caller's conversation exists, as far as can be told. A `ready` that
        fails, hangs or is cancelled on its own is a failed one: the row is still
        written, without the conversation."""
        if call.ready is None:
            return True
        try:
            await asyncio.wait_for(call.ready(), self.ready_timeout_s)
            return True
        except asyncio.CancelledError:
            # Ours to stop only if this task was cancelled; otherwise it was the
            # caller's own, and the stream it is recorded for is not being stopped.
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
        except Exception as e:
            log.warning("usage recorded without its conversation: %s", type(e).__name__)
            return False
        log.warning("usage recorded without its conversation: CancelledError")
        return False

    async def record(
        self,
        call: Call,
        *,
        provider_id: str | None,
        provider: str,
        model: str,
        usage: Usage,
        unit: Unit,
        quantity: float,
        price: Callable[[], float | None],
    ) -> None:
        """Write one row and count it. Never raises: the call it records is done.

        A row that cannot name its conversation (its `ready` failed, or the store
        refused the conversation) is written once more without it, so its dollars
        are still there after a restart."""
        conversation = call.conversation_id if await self._ready(call) else None
        try:
            cost = price()
        except Exception as e:
            # Unpriced, so it counts by its quantity, never as $0. The kind only.
            log.warning("usage for %s not priced: %s", provider, type(e).__name__)
            cost = None
        at = self.clock()
        # The store files a row with no provider id under its provider's name: the
        # ledger counts it there too, so memory and a reload agree.
        pid = provider if provider_id is None else provider_id
        row = Row(pid, call.caller, unit, quantity, cost, at)
        try:
            for attempt in (conversation, None) if conversation is not None else (None,):
                try:
                    await self.store.record_usage(
                        owner_id=self.owner_id,
                        conversation_id=attempt,
                        provider=provider,
                        model=model,
                        usage=usage,
                        cost_usd=cost,
                        now_ms=at,
                        provider_id=provider_id,
                        caller=call.caller,
                        units=unit,
                    )
                    break
                except Exception as e:
                    # The kind only: never an amount.
                    log.warning("usage not recorded for %s: %s", provider, type(e).__name__)
        finally:
            try:
                self.ledger.add(row)
            except Exception as e:
                log.warning("usage not counted for %s: %s", provider, type(e).__name__)

    async def settle(self, record: Coroutine[Any, Any, None]) -> None:
        """Run a record to the end even if the caller is cancelled meanwhile."""
        task = asyncio.ensure_future(record)
        self._recording.add(task)
        task.add_done_callback(self._recording.discard)
        await asyncio.shield(task)

    async def drain(self, timeout_s: float = DRAIN_S) -> None:
        """Wait, at most `timeout_s`, for every record still being written, then cancel
        what is left. Call it before the database closes. Never raises.

        It looks at whether each is done, not at whether its done-callback has taken it
        out of the set yet: a loop that shut down before running that callback leaves a
        finished one behind, and waiting on it again would spin forever."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        recording = self._recording
        while True:
            # In place: each one's done-callback discards it from this same set.
            for t in [t for t in recording if t.done()]:
                recording.discard(t)
            if not recording:
                return
            wait = deadline - loop.time()
            if wait <= 0:
                break
            with contextlib.suppress(Exception):
                await asyncio.wait(list(recording), timeout=wait)
        left = list(recording)
        # The count only: never what they were recording.
        log.warning("%d usage record(s) not finished before close", len(left))
        for t in left:
            t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait(left, timeout=1.0)
        recording.clear()


async def _close(stream: object) -> None:
    close = getattr(stream, "aclose", None)
    if close is not None:
        await close()


def _tokens(u: Usage) -> int:
    return u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens


class _Wrapper:
    """Delegates everything it does not meter to what it wraps, reads and writes."""

    _OWN: frozenset[str] = frozenset({"inner", "provider_id", "_meter"})
    inner: Any
    provider_id: str | None
    _meter: Meter

    def __init__(self, inner: Any, provider_id: str | None, meter: Meter) -> None:
        object.__setattr__(self, "inner", inner)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "_meter", meter)

    def __getattr__(self, name: str) -> Any:
        # Only reached for what this class does not define.
        if name in _Wrapper._OWN:
            raise AttributeError(name)
        return getattr(self.inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self.inner, name, value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.inner!r}, {self.provider_id!r})"


class MeteredModel(_Wrapper):
    """A `ModelProvider` whose every call asks the gate first and records its usage."""

    inner: ModelProvider

    @property
    def name(self) -> str:
        return self.inner.name

    @name.setter
    def name(self, value: str) -> None:
        self.inner.name = value

    @property
    def model(self) -> str:
        return self.inner.model

    @model.setter
    def model(self, value: str) -> None:
        self.inner.model = value

    def pricing(self) -> Pricing | None:
        return self.inner.pricing()

    def _cost(self, usage: Usage) -> float | None:
        if self.provider_id is not None:
            return self._meter.price(self.provider_id, self.inner.model, usage)
        # A model nothing says the cap of (a stand-in, in tests) prices as it says.
        pricing = self.inner.pricing()
        return pricing.cost(usage) if pricing is not None else None

    def _estimates(self) -> bool:
        """Whether a call with no usage is estimated: one that may cost. A local model,
        or one nothing says the cap of, records only what it reports, as before."""
        return self.provider_id is not None and self.provider_id not in UNCAPPED

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        quick: bool = False,
    ) -> AsyncIterator[ModelEvent]:
        call = current()
        await self._meter.gate.check_model(self.provider_id)
        # `quick` is passed only when asked for: a provider with nothing to tune may
        # not take it. `on_usage` only to one that says it reports usage so far.
        extra: dict[str, Any] = {"quick": True} if quick else {}
        so_far: list[Usage] = []
        if getattr(self.inner, "reports_usage_so_far", False):
            extra["on_usage"] = so_far.append
        events = self.inner.stream(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens, **extra
        )
        # Every call is recorded once: at its `Done`, or when it stops without one.
        recorded = False
        streamed = 0
        failed = False

        def sent() -> int:
            return _prompt_chars(system, messages, tools)

        try:
            async for ev in events:
                if isinstance(ev, TextDelta):
                    streamed += len(ev.text)
                elif isinstance(ev, ToolUse):
                    streamed += len(ev.name) + len(json.dumps(dict(ev.args), default=str))
                elif isinstance(ev, Done) and not recorded:
                    recorded = True
                    usage = ev.usage
                    if usage == Usage() and self._estimates():
                        # A server that sends no usage still bills what it was sent.
                        usage = _estimate(sent(), streamed)
                    if usage != Usage():
                        # Recorded before it is passed on: a reader that stops at
                        # `Done` never loses the row.
                        await self._meter.settle(self._record(call, usage))
                yield ev
        except Exception:
            failed = True
            raise
        finally:
            try:
                await _close(events)
            finally:
                # Stopped without a `Done` (a barge-in, a dropped draft, an error
                # partway): what was used so far is counted, as the provider reported
                # it, else estimated. An error before anything came back used nothing.
                if not recorded and self._estimates() and (streamed or so_far or not failed):
                    recorded = True
                    usage = _stopped(so_far[-1] if so_far else None, sent(), streamed)
                    if usage != Usage():
                        await self._meter.settle(self._record(call, usage))

    def _record(self, call: Call, usage: Usage) -> Coroutine[Any, Any, None]:
        return self._meter.record(
            call,
            provider_id=self.provider_id,
            provider=self.inner.name,
            model=self.inner.model,
            usage=usage,
            unit="tokens",
            quantity=_tokens(usage),
            price=functools.partial(self._cost, usage),
        )


# Characters to a token, roughly, for a call whose usage is not known.
_CHARS_PER_TOKEN = 4


def _prompt_chars(
    system: Sequence[SystemBlock], messages: Sequence[Msg], tools: Sequence[ToolSchema]
) -> int:
    """The characters of text a call sent: its system prompt, messages and tools."""
    n = sum(len(s.text) for s in system)
    for m in messages:
        for b in m.blocks:
            if isinstance(b, TextBlock | ContextBlock):
                n += len(b.text)
            elif isinstance(b, ToolResultBlock):
                n += len(b.content)
            elif isinstance(b, ToolUseBlock):
                n += len(b.name) + len(json.dumps(dict(b.args), default=str))
    for t in tools:
        n += len(t.name) + len(t.description) + len(json.dumps(dict(t.input_schema), default=str))
    return n


def _estimate(sent: int, streamed: int) -> Usage:
    """Usage at four characters a token, rounded up."""
    return Usage(
        input_tokens=math.ceil(sent / _CHARS_PER_TOKEN),
        output_tokens=math.ceil(streamed / _CHARS_PER_TOKEN),
    )


def _stopped(reported: Usage | None, sent: int, streamed: int) -> Usage:
    """A call that stopped without its `Done`: the usage its provider reported so far,
    with no fewer output tokens than were streamed; else the estimate."""
    guess = _estimate(sent, streamed)
    if reported is None:
        return guess
    return dataclasses.replace(
        reported, output_tokens=max(reported.output_tokens, guess.output_tokens)
    )


class MeteredVoice(_Wrapper):
    """A `TTSProvider` that asks the gate before each sentence and counts its
    characters once its audio has begun: a sentence cut off midway is still billed."""

    inner: TTSProvider

    @property
    def name(self) -> str:
        return self.inner.name

    @name.setter
    def name(self, value: str) -> None:
        self.inner.name = value

    @property
    def where(self) -> Where:
        return self.inner.where

    @where.setter
    def where(self, value: Where) -> None:
        self.inner.where = value

    @property
    def output_format(self) -> AudioFormat | None:
        return self.inner.output_format

    @output_format.setter
    def output_format(self, value: AudioFormat | None) -> None:
        self.inner.output_format = value

    async def voices(self) -> list[Voice]:
        return await self.inner.voices()

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        call = current()
        # Empty text makes no request, so it is neither asked about nor counted.
        billed = bool(text.strip()) and self.provider_id is not None
        if billed:
            await self._meter.gate.check_voice(self.provider_id)
        began = False
        audio = self.inner.synthesize(text, voice=voice)
        try:
            async for chunk in audio:
                began = True
                yield chunk
            began = True
        finally:
            await _close(audio)
            if billed and began:
                await self._meter.settle(self._record(call, len(text)))

    async def _record(self, call: Call, chars: int) -> None:
        pid = self.provider_id
        assert pid is not None  # noqa: S101 - only a billed sentence records
        model = str(getattr(self.inner, "model", self.inner.name))
        await self._meter.record(
            call,
            provider_id=self.provider_id,
            provider=self.inner.name,
            model=model,
            usage=Usage(input_tokens=chars),
            unit="chars",
            quantity=chars,
            price=lambda: self._meter.price(pid, model, chars),
        )


class MeteredHearing(_Wrapper):
    """An `STTProvider` that asks the gate before each stream opens and counts the
    seconds of audio sent on it when it closes. A stream that crosses the cap is let
    finish; the next one is refused."""

    inner: STTProvider

    @property
    def name(self) -> str:
        return self.inner.name

    @name.setter
    def name(self, value: str) -> None:
        self.inner.name = value

    @property
    def streaming(self) -> bool:
        return self.inner.streaming

    @streaming.setter
    def streaming(self, value: bool) -> None:
        self.inner.streaming = value

    @property
    def turns(self) -> bool:
        return self.inner.turns

    @turns.setter
    def turns(self, value: bool) -> None:
        self.inner.turns = value

    @property
    def word_times(self) -> bool:
        return self.inner.word_times

    @word_times.setter
    def word_times(self, value: bool) -> None:
        self.inner.word_times = value

    async def choices(self) -> list[Choice]:
        return await self.inner.choices()

    async def prepare(self, choice: str) -> None:
        await self.inner.prepare(choice)

    async def open(self, *, choice: str) -> STTStream:
        call = current()
        await self._meter.gate.check_voice(self.provider_id)
        stream = await self.inner.open(choice=choice)
        if self.provider_id is None:
            return stream
        model_for = getattr(self.inner, "model_for", None)
        model = str(model_for(choice)) if callable(model_for) else choice
        return _MeteredStream(stream, self, call, model)


class _MeteredStream:
    """One hearing stream, counting the audio sent on it."""

    def __init__(self, inner: STTStream, owner: MeteredHearing, call: Call, model: str) -> None:
        self._inner = inner
        self._owner = owner
        self._call = call
        self._model = model
        self._bytes = 0
        self._recorded = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def send(self, pcm: bytes) -> None:
        # Counted as it is handed over, so audio sent on a stream that then breaks is
        # never counted for less than it may have cost.
        self._bytes += len(pcm)
        await self._inner.send(pcm)

    async def finish(self) -> None:
        await self._inner.finish()

    def events(self) -> AsyncIterator[Heard]:
        return self._inner.events()

    async def close(self) -> None:
        try:
            await self._inner.close()
        finally:
            if not self._recorded:
                self._recorded = True
                if self._bytes:
                    await self._owner._meter.settle(self._record())

    async def _record(self) -> None:
        owner = self._owner
        pid = owner.provider_id
        assert pid is not None  # noqa: S101 - only a hosted stream records
        # Whole seconds, rounded up: the row and the ledger hold the same number.
        seconds = math.ceil(self._bytes / _SECOND_BYTES)
        await owner._meter.record(
            self._call,
            provider_id=owner.provider_id,
            provider=owner.inner.name,
            model=self._model,
            usage=Usage(input_tokens=seconds),
            unit="seconds",
            quantity=seconds,
            price=lambda: owner._meter.price(pid, self._model, seconds),
        )
