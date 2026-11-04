"""The metered providers: the one way a paid call is made (BRN-7).

`MeteredModel`, `MeteredVoice` and `MeteredHearing` wrap a model, a voice and a
recognizer, with the same interface as what they wrap, so nothing that uses them
changes shape. The runtime hands out providers only through them.

**Before a call** the wrapper asks the gate, which raises `ModelCapped` or
`VoiceCapped` at a hard cap, before anything is sent. A call already running is never
stopped: the check is made only before a call.

**After a call** (a model stream's `Done`, a sentence's audio, a hearing stream
closed) it records one usage row, priced on its own and never as part of a sum, with
the caller and conversation of `caller.current()`, and the ledger counts it. A row
that cannot be written is logged (its kind only, never an amount) and still counted,
so the caps hold for the rest of this run.

Who calls is read when the call starts, so a stream read on another task is still
counted for whoever started it.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from typing import Any, Protocol

from trillion.brain.types import (
    Done,
    ModelEvent,
    ModelProvider,
    Msg,
    Pricing,
    SystemBlock,
    ToolSchema,
    Usage,
)
from trillion.spend import prices
from trillion.spend.caller import Call, current
from trillion.spend.gate import Gate
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

    def model(self, inner: ModelProvider, provider_id: str | None) -> MeteredModel:
        if isinstance(inner, MeteredModel):
            return inner
        return MeteredModel(inner, provider_id, self)

    def voice(self, inner: TTSProvider, provider_id: str | None) -> MeteredVoice:
        if isinstance(inner, MeteredVoice):
            return inner
        return MeteredVoice(inner, provider_id, self)

    def hearing(self, inner: STTProvider, provider_id: str | None) -> MeteredHearing:
        if isinstance(inner, MeteredHearing):
            return inner
        return MeteredHearing(inner, provider_id, self)

    def price(self, provider_id: str, model: str, usage: Usage | float) -> float | None:
        return prices.price(provider_id, model, usage, self.prices.rates())

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
        cost: float | None,
    ) -> None:
        """Write one row and count it. Never raises: the call it records is done."""
        if call.ready is not None:
            try:
                await call.ready()
            except Exception as e:
                log.warning("usage not recorded: its caller failed (%s)", type(e).__name__)
        at = self.clock()
        # The store files a row with no provider id under its provider's name: the
        # ledger counts it there too, so memory and a reload agree.
        row = Row(
            provider if provider_id is None else provider_id, call.caller, unit, quantity, cost, at
        )
        try:
            await self.store.record_usage(
                owner_id=self.owner_id,
                conversation_id=call.conversation_id,
                provider=provider,
                model=model,
                usage=usage,
                cost_usd=cost,
                now_ms=at,
                provider_id=provider_id,
                caller=call.caller,
                units=unit,
            )
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
        # Held until it is done: a cancelled caller no longer holds it.
        _recording.add(task)
        task.add_done_callback(_recording.discard)
        await asyncio.shield(task)


# Records still being written whose caller has gone.
_recording: set[asyncio.Future[None]] = set()


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
        # not take it.
        extra = {"quick": True} if quick else {}
        events = self.inner.stream(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens, **extra
        )
        try:
            async for ev in events:
                if isinstance(ev, Done) and ev.usage != Usage():
                    # Recorded before it is passed on: a reader that stops at `Done`
                    # never loses the row.
                    await self._meter.settle(
                        self._meter.record(
                            call,
                            provider_id=self.provider_id,
                            provider=self.inner.name,
                            model=self.inner.model,
                            usage=ev.usage,
                            unit="tokens",
                            quantity=_tokens(ev.usage),
                            cost=self._cost(ev.usage),
                        )
                    )
                yield ev
        finally:
            await _close(events)


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
        assert self.provider_id is not None  # noqa: S101 - only a billed sentence records
        model = str(getattr(self.inner, "model", self.inner.name))
        await self._meter.record(
            call,
            provider_id=self.provider_id,
            provider=self.inner.name,
            model=model,
            usage=Usage(input_tokens=chars),
            unit="chars",
            quantity=chars,
            cost=self._meter.price(self.provider_id, model, chars),
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
        assert owner.provider_id is not None  # noqa: S101 - only a hosted stream records
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
            cost=owner._meter.price(owner.provider_id, self._model, seconds),
        )
