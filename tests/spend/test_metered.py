"""The metered providers: every paid call asks the gate first and records one row (BRN-7)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from trillion.brain.types import (
    Done,
    ModelEvent,
    Msg,
    Pricing,
    ProviderError,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolSchema,
    Usage,
)
from trillion.spend import prices
from trillion.spend.caller import as_caller
from trillion.spend.gate import Gate, ModelCapped, VoiceCapped
from trillion.spend.ledger import Ledger, Row
from trillion.spend.metered import (
    Meter,
    MeteredHearing,
    MeteredModel,
    MeteredVoice,
    PriceBook,
)
from trillion.voice.types import CAPTURE, Heard, VoiceError

NOW = 1793800000000  # 2026-11-04, well inside the month in UTC
USAGE = Usage(input_tokens=1_000_000, output_tokens=100_000)
CAPS = {
    "anthropic": {"soft": 20.0, "hard": 50.0},
    "elevenlabs": {"soft": 20.0, "hard": 50.0},
    "deepgram": {"soft": 20.0, "hard": 50.0},
}


class Store:
    """A usage store in memory: what `record_usage` was given, and the month to load."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = False
        # Refuses a row that names a conversation, as the foreign key does for one
        # that does not exist.
        self.no_conversations = False
        self.wait: asyncio.Event | None = None

    async def record_usage(self, **row: Any) -> None:
        if self.wait is not None:
            await self.wait.wait()
        if self.fail:
            raise RuntimeError("the database is gone")
        if self.no_conversations and row["conversation_id"] is not None:
            raise RuntimeError("FOREIGN KEY constraint failed")
        self.rows.append(row)

    async def spend_since(
        self, owner_id: str, since_ms: int, until_ms: int | None = None
    ) -> list[dict[str, Any]]:
        return []


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> int:
        self.now += 1
        return self.now


class Model:
    """A model that answers each call with the events it is given, and counts calls."""

    name = "anthropic"

    def __init__(self, *events: ModelEvent, model: str = "claude-opus-5-5") -> None:
        self.model = model
        self.events = list(events) or [
            TextDelta(text="Hi."),
            Done(stop_reason="end", usage=USAGE),
        ]
        self.calls = 0
        self.closed = 0
        self.extra = "delegated"
        self.gate: asyncio.Event | None = None

    def pricing(self) -> Pricing | None:
        return Pricing(input=1.0, output=1.0)

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        quick: bool = False,
    ) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        try:
            for ev in self.events:
                if self.gate is not None and isinstance(ev, Done):
                    await self.gate.wait()
                if isinstance(ev, Broken):
                    raise ProviderError("overloaded")
                yield ev
        finally:
            self.closed += 1


async def meter_for(caps: Any = CAPS, user_prices: Any = None) -> tuple[Meter, Store, Ledger, Gate]:
    store = Store()
    clock = Clock()
    ledger = Ledger(store, clock, "UTC")
    await ledger.load()

    async def caps_source() -> Any:
        return caps

    gate = Gate(ledger, caps_source)
    await gate.reload_caps()

    async def prices_source() -> Any:
        return user_prices

    book = PriceBook(prices_source)
    await book.reload()
    return (
        Meter(gate=gate, ledger=ledger, store=store, clock=clock, prices=book),
        store,
        ledger,
        gate,
    )


async def ask(
    provider: Any, *, system: Sequence[SystemBlock] = (), messages: Sequence[Msg] = ()
) -> list[ModelEvent]:
    return [
        ev
        async for ev in provider.stream(system=system, messages=messages, tools=[], max_tokens=10)
    ]


# 400 characters sent: an estimate of 100 tokens.
PROMPT = [SystemBlock(text="s" * 200)]
ASKED = Msg(role="user", blocks=(TextBlock(text="u" * 200),))
PROMPT_TOKENS = 100


class Broken:
    """Where a stream fails partway."""


class Reporting(Model):
    """A model that says what it has used so far, as Anthropic's does once a reply
    starts."""

    reports_usage_so_far = True

    def __init__(self, start: Usage, *events: Any) -> None:
        super().__init__(*events)
        self.start = start

    async def stream(  # type: ignore[override]
        self, *, on_usage: Any = None, **kw: Any
    ) -> AsyncIterator[ModelEvent]:
        on_usage(self.start)
        async for ev in super().stream(**kw):
            yield ev


def over(ledger: Ledger, pid: str, dollars: float = 60.0) -> None:
    ledger.add(Row(pid, "conversation", "tokens", 0, dollars, NOW))


# ── Models ───────────────────────────────────────────────────────────────


async def test_one_row_per_model_call_with_its_caller_and_conversation() -> None:
    meter, store, ledger, _ = await meter_for()
    inner = Model()
    metered = meter.model(inner, "anthropic")
    with as_caller("conversation", "c1"):
        events = await ask(metered)
    await ask(metered)
    # The events pass through unchanged.
    assert events == inner.events
    assert len(store.rows) == 2
    first, second = store.rows
    expected = prices.price("anthropic", "claude-opus-5-5", USAGE)
    assert first == {
        "owner_id": "local",
        "conversation_id": "c1",
        "provider": "anthropic",
        "model": "claude-opus-5-5",
        "usage": USAGE,
        "cost_usd": expected,
        "now_ms": first["now_ms"],
        "provider_id": "anthropic",
        "caller": "conversation",
        "units": "tokens",
    }
    # An unlabelled call is background, in no conversation.
    assert (second["caller"], second["conversation_id"]) == ("background", None)
    assert expected is not None
    spent = ledger.spent("anthropic")
    assert spent.dollars == pytest.approx(2 * expected)
    assert spent.by_caller["conversation"].dollars == pytest.approx(expected)


async def test_each_call_is_priced_alone_never_as_a_sum() -> None:
    # Haiku 5.5 bills a whole request at a higher rate past 100K prompt tokens: two
    # calls of 60K are two cheap calls, not one dear one.
    meter, store, _, _ = await meter_for()
    small = Usage(input_tokens=60_000)
    metered = meter.model(
        Model(Done(stop_reason="end", usage=small), model="claude-haiku-5-5"), "anthropic"
    )
    await ask(metered)
    await ask(metered)
    one = prices.price("anthropic", "claude-haiku-5-5", small)
    assert [r["cost_usd"] for r in store.rows] == [one, one]


async def test_a_capped_provider_refuses_before_the_model_is_called() -> None:
    meter, store, ledger, _ = await meter_for()
    inner = Model()
    over(ledger, "anthropic")
    with as_caller("conversation", "c1"), pytest.raises(ModelCapped) as refused:
        await ask(meter.model(inner, "anthropic"))
    assert inner.calls == 0
    assert store.rows == []
    # The provider error callers already handle, with kind "capped".
    assert isinstance(refused.value, ProviderError) and refused.value.kind == "capped"
    assert refused.value.caller == "conversation"


async def test_local_and_unknown_providers_are_never_refused() -> None:
    meter, store, ledger, _ = await meter_for({"local": {"soft": 0, "hard": 0}})
    over(ledger, "local")
    await ask(meter.model(Model(), "local"))
    await ask(meter.model(Model(), None))
    assert len(store.rows) == 2
    # A local model is free; a provider with no id is priced as it prices itself.
    assert store.rows[0]["cost_usd"] == 0.0
    assert store.rows[1]["provider_id"] is None
    assert store.rows[1]["cost_usd"] == Pricing(input=1.0, output=1.0).cost(USAGE)


async def test_a_call_in_flight_when_the_cap_is_crossed_finishes_and_is_recorded() -> None:
    meter, store, ledger, gate = await meter_for()
    inner = Model()
    inner.gate = asyncio.Event()
    metered = meter.model(inner, "anthropic")
    stream = metered.stream(system=[], messages=[], tools=[], max_tokens=10)
    first = await anext(stream)
    assert first == TextDelta(text="Hi.")
    # Something else spends past the cap while this call is still answering.
    over(ledger, "anthropic")
    assert gate.state("anthropic") == "capped"
    inner.gate.set()
    rest = [ev async for ev in stream]
    assert isinstance(rest[-1], Done)
    assert len(store.rows) == 1
    # The next call is refused.
    with pytest.raises(ModelCapped):
        await ask(metered)
    assert inner.calls == 1


async def test_a_stream_cancelled_after_its_usage_still_records() -> None:
    meter, store, ledger, _ = await meter_for()
    metered = meter.model(Model(), "anthropic")
    stream = metered.stream(system=[], messages=[], tools=[], max_tokens=10)
    await anext(stream)
    done = await anext(stream)
    assert isinstance(done, Done)
    # Closed by its reader before the end of the stream.
    await stream.aclose()
    assert len(store.rows) == 1
    assert ledger.spent("anthropic").dollars > 0


async def test_a_task_cancelled_while_the_row_is_written_still_counts_it() -> None:
    meter, store, ledger, _ = await meter_for()
    store.wait = asyncio.Event()
    metered = meter.model(Model(), "anthropic")
    task = asyncio.create_task(ask(metered))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    store.wait.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(store.rows) == 1
    assert ledger.spent("anthropic").dollars > 0


async def test_a_stream_cut_off_before_its_usage_records_an_estimate() -> None:
    """Barge-in, a dropped draft: what was sent and streamed was billed, so it is
    counted at four characters a token."""
    meter, store, ledger, _ = await meter_for()
    inner = Model(TextDelta(text="x" * 400), Done(stop_reason="end", usage=USAGE))
    metered = meter.model(inner, "anthropic")
    stream = metered.stream(system=PROMPT, messages=[ASKED], tools=[], max_tokens=10)
    await anext(stream)
    await stream.aclose()
    (row,) = store.rows
    assert row["usage"] == Usage(input_tokens=PROMPT_TOKENS, output_tokens=100)
    assert row["cost_usd"] == prices.price("anthropic", "claude-opus-5-5", row["usage"])
    assert ledger.spent("anthropic").dollars == pytest.approx(row["cost_usd"])
    # The provider's own stream is closed too.
    assert inner.closed == 1


async def test_a_task_cancelled_before_done_records_once() -> None:
    meter, store, ledger, _ = await meter_for()
    inner = Model(TextDelta(text="x" * 40), Done(stop_reason="end", usage=USAGE))
    inner.gate = asyncio.Event()
    metered = meter.model(inner, "anthropic")

    async def read() -> None:
        async for _ in metered.stream(system=PROMPT, messages=[ASKED], tools=[], max_tokens=10):
            pass

    task = asyncio.create_task(read())
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(5):
        await asyncio.sleep(0)
    (row,) = store.rows
    assert row["usage"] == Usage(input_tokens=PROMPT_TOKENS, output_tokens=10)
    assert ledger.spent("anthropic").dollars == pytest.approx(row["cost_usd"])


async def test_usage_the_provider_reported_before_it_stopped_is_what_is_recorded() -> None:
    """Anthropic says the prompt's tokens when the reply starts: a reply stopped
    partway records those, and no fewer output tokens than were streamed."""
    meter, store, _, _ = await meter_for()
    start = Usage(input_tokens=5_000, cache_read_tokens=20_000, output_tokens=1)
    inner = Reporting(start, TextDelta(text="x" * 80), Done(stop_reason="end", usage=USAGE))
    metered = meter.model(inner, "anthropic")
    stream = metered.stream(system=PROMPT, messages=[ASKED], tools=[], max_tokens=10)
    await anext(stream)
    await stream.aclose()
    (row,) = store.rows
    assert row["usage"] == Usage(input_tokens=5_000, cache_read_tokens=20_000, output_tokens=20)


async def test_an_error_partway_records_what_was_used() -> None:
    meter, store, _, _ = await meter_for()
    inner = Model(TextDelta(text="x" * 40), Broken())
    metered = meter.model(inner, "anthropic")
    with pytest.raises(ProviderError):
        await ask(metered, system=PROMPT, messages=[ASKED])
    (row,) = store.rows
    assert row["usage"] == Usage(input_tokens=PROMPT_TOKENS, output_tokens=10)


async def test_an_error_before_anything_came_back_records_nothing() -> None:
    meter, store, _, _ = await meter_for()
    metered = meter.model(Model(Broken()), "anthropic")
    with pytest.raises(ProviderError):
        await ask(metered, system=PROMPT, messages=[ASKED])
    assert store.rows == []


async def test_a_server_that_sends_no_usage_is_counted_by_its_characters() -> None:
    meter, store, ledger, _ = await meter_for()
    pid = "custom:llm.example.com"
    inner = Model(TextDelta(text="x" * 400), Done(stop_reason="end", usage=Usage()))
    await ask(meter.model(inner, pid), system=PROMPT, messages=[ASKED])
    (row,) = store.rows
    assert row["usage"] == Usage(input_tokens=PROMPT_TOKENS, output_tokens=100)
    assert row["cost_usd"] is None
    assert ledger.spent(pid).units == PROMPT_TOKENS + 100


async def test_a_local_model_with_no_usage_still_records_nothing() -> None:
    meter, store, _, _ = await meter_for()
    inner = Model(TextDelta(text="x" * 400), Done(stop_reason="end", usage=Usage()))
    await ask(meter.model(inner, "local"), system=PROMPT, messages=[ASKED])
    stream = meter.model(Model(TextDelta(text="x" * 400)), "local").stream(
        system=PROMPT, messages=[ASKED], tools=[], max_tokens=10
    )
    await anext(stream)
    await stream.aclose()
    assert store.rows == []


async def test_a_stream_read_to_its_done_records_once_and_never_estimates() -> None:
    meter, store, _, _ = await meter_for()
    inner = Reporting(
        Usage(input_tokens=7), TextDelta(text="x" * 400), Done(stop_reason="end", usage=USAGE)
    )
    metered = meter.model(inner, "anthropic")
    stream = metered.stream(system=PROMPT, messages=[ASKED], tools=[], max_tokens=10)
    async for ev in stream:
        if isinstance(ev, Done):
            break
    await stream.aclose()
    assert [r["usage"] for r in store.rows] == [USAGE]


async def test_a_call_that_used_nothing_records_nothing() -> None:
    meter, store, _, _ = await meter_for()
    await ask(meter.model(Model(Done(stop_reason="end", usage=Usage())), "anthropic"))
    assert store.rows == []


async def test_a_failed_write_never_breaks_the_reply_and_the_ledger_still_counts_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    meter, store, ledger, _ = await meter_for()
    store.fail = True
    events = await ask(meter.model(Model(), "anthropic"))
    assert isinstance(events[-1], Done)
    assert ledger.spent("anthropic").dollars > 0
    assert "usage not recorded" in caplog.text
    # The kind of failure only: never an amount.
    assert "RuntimeError" in caplog.text and "$" not in caplog.text


async def test_the_row_and_the_ledger_share_one_moment() -> None:
    meter, store, ledger, _ = await meter_for()
    seen: list[Row] = []
    add = ledger.add
    ledger.add = lambda row: (seen.append(row), add(row))[1]  # type: ignore[method-assign]
    await ask(meter.model(Model(), "anthropic"))
    assert [r.at_ms for r in seen] == [store.rows[0]["now_ms"]]


async def test_a_caller_whose_conversation_is_saved_late_makes_it_exist_first() -> None:
    meter, store, _, _ = await meter_for()
    made: list[int] = []

    async def ready() -> None:
        made.append(len(store.rows))

    with as_caller("background", "c9", ready=ready):
        await ask(meter.model(Model(), "anthropic"))
    assert made == [0]
    assert store.rows[0]["conversation_id"] == "c9"


async def test_the_metered_model_looks_like_what_it_wraps() -> None:
    meter, _, _, _ = await meter_for()
    inner = Model()
    metered = meter.model(inner, "anthropic")
    assert isinstance(metered, MeteredModel)
    assert (metered.name, metered.model, metered.provider_id) == (
        "anthropic",
        "claude-opus-5-5",
        "anthropic",
    )
    assert metered.pricing() == inner.pricing()
    assert metered.extra == "delegated"
    # What is set on it is set on what it wraps.
    metered.extra = "changed"
    assert inner.extra == "changed"
    metered.model = "claude-sonnet-5-5"
    assert inner.model == "claude-sonnet-5-5"
    # Wrapping a metered provider again changes nothing.
    assert meter.model(metered, "anthropic") is metered


async def test_a_failed_ready_still_keeps_the_dollars_without_the_conversation() -> None:
    meter, store, ledger, _ = await meter_for()

    async def broken() -> None:
        raise RuntimeError("no conversation")

    with as_caller("background", "c9", ready=broken):
        events = await ask(meter.model(Model(), "anthropic"))
    assert isinstance(events[-1], Done)
    (row,) = store.rows
    assert row["conversation_id"] is None and row["cost_usd"] is not None
    assert ledger.spent("anthropic").dollars == pytest.approx(row["cost_usd"])


async def test_a_ready_cancelled_on_its_own_never_cancels_the_stream() -> None:
    meter, store, _, _ = await meter_for()

    async def cancelled() -> None:
        raise asyncio.CancelledError

    with as_caller("background", "c9", ready=cancelled):
        events = await ask(meter.model(Model(), "anthropic"))
    assert isinstance(events[-1], Done)
    assert [r["conversation_id"] for r in store.rows] == [None]


async def test_a_ready_that_hangs_is_given_up_on() -> None:
    meter, store, _, _ = await meter_for()
    meter.ready_timeout_s = 0.01

    async def hangs() -> None:
        await asyncio.Event().wait()

    with as_caller("background", "c9", ready=hangs):
        await ask(meter.model(Model(), "anthropic"))
    assert [r["conversation_id"] for r in store.rows] == [None]


async def test_a_row_refused_for_its_conversation_is_kept_without_it() -> None:
    meter, store, _, _ = await meter_for()
    store.no_conversations = True
    with as_caller("conversation", "gone"):
        await ask(meter.model(Model(), "anthropic"))
    (row,) = store.rows
    assert row["conversation_id"] is None and row["caller"] == "conversation"


async def test_a_pricing_failure_records_the_row_unpriced(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    meter, store, ledger, _ = await meter_for()

    def broken(*_args: Any) -> float | None:
        raise ValueError("bad rate")

    monkeypatch.setattr(prices, "price", broken)
    events = await ask(meter.model(Model(), "anthropic"))
    assert isinstance(events[-1], Done)
    (row,) = store.rows
    assert row["cost_usd"] is None
    # Counted by its tokens, never as $0.
    assert ledger.spent("anthropic").units == 1_100_000
    assert "ValueError" in caplog.text and "$" not in caplog.text


async def test_drain_waits_for_rows_whose_caller_has_gone() -> None:
    meter, store, _, _ = await meter_for()
    store.wait = asyncio.Event()
    task = asyncio.create_task(ask(meter.model(Model(), "anthropic")))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    drained = asyncio.create_task(meter.drain())
    await asyncio.sleep(0)
    assert not drained.done()
    store.wait.set()
    await drained
    assert len(store.rows) == 1
    # Nothing left to wait for.
    await meter.drain()


async def test_wrapping_a_metered_provider_with_another_id_wraps_what_it_wraps() -> None:
    meter, _, _, _ = await meter_for()
    inner = Model()
    first = meter.model(inner, None)
    again = meter.model(first, "anthropic")
    assert again is not first and again.inner is inner and again.provider_id == "anthropic"
    voice = meter.voice(Voice(), None)
    assert meter.voice(voice, "elevenlabs").inner is voice.inner
    hearing = meter.hearing(Hearing(), None)
    assert meter.hearing(hearing, "deepgram").inner is hearing.inner


# ── User prices ──────────────────────────────────────────────────────────


async def test_user_prices_win_and_are_read_again_on_reload() -> None:
    mine = {"anthropic": {"claude-opus-5-5": {"input": 1.0, "output": 10.0}}}
    meter, store, _, _ = await meter_for(user_prices=mine)
    metered = meter.model(Model(), "anthropic")
    await ask(metered)
    assert store.rows[-1]["cost_usd"] == pytest.approx(1.0 + 1.0)
    # Changed, then read again: the next call is priced by the new rate.
    mine["anthropic"]["claude-opus-5-5"] = {"input": 2.0, "output": 10.0}
    await ask(metered)
    assert store.rows[-1]["cost_usd"] == pytest.approx(2.0)
    await meter.prices.reload()
    await ask(metered)
    assert store.rows[-1]["cost_usd"] == pytest.approx(3.0)


async def test_prices_never_read_are_the_tables() -> None:
    book = PriceBook(None)
    assert book.rates() == {}


# ── Voice ────────────────────────────────────────────────────────────────


class Voice:
    name = "elevenlabs"
    where = "core"
    output_format = CAPTURE
    model = "eleven_flash_v2_5"

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls: list[str] = []
        self.fail_after = fail_after

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        self.calls.append(text)
        if not text.strip():
            return
        for i in range(3):
            if self.fail_after is not None and i >= self.fail_after:
                raise VoiceError("unreachable")
            yield b"\x00\x00" * 10

    async def voices(self) -> list[Any]:
        return []


async def speak(voice: Any, text: str) -> bytes:
    return b"".join([c async for c in voice.synthesize(text, voice="v1")])


async def test_voice_is_counted_in_characters_per_sentence() -> None:
    meter, store, ledger, _ = await meter_for()
    inner = Voice()
    metered = meter.voice(inner, "elevenlabs")
    assert isinstance(metered, MeteredVoice)
    with as_caller("conversation", "c1"):
        audio = await speak(metered, "Hello there.")
    assert audio == b"\x00\x00" * 30
    (row,) = store.rows
    assert row["units"] == "chars" and row["usage"] == Usage(input_tokens=12)
    assert (row["provider_id"], row["model"], row["caller"]) == (
        "elevenlabs",
        "eleven_flash_v2_5",
        "conversation",
    )
    assert row["cost_usd"] == prices.price("elevenlabs", "eleven_flash_v2_5", 12)
    assert ledger.spent("elevenlabs").dollars == pytest.approx(row["cost_usd"])
    # Nothing to say asks for nothing, and costs nothing.
    await speak(metered, "  ")
    assert len(store.rows) == 1


async def test_a_capped_voice_refuses_the_next_sentence_before_it_is_sent() -> None:
    meter, store, ledger, _ = await meter_for()
    inner = Voice()
    metered = meter.voice(inner, "elevenlabs")
    over(ledger, "elevenlabs")
    with pytest.raises(VoiceCapped) as refused:
        await speak(metered, "Hello.")
    assert inner.calls == [] and store.rows == []
    assert isinstance(refused.value, VoiceError) and refused.value.kind == "capped"


async def test_a_sentence_cut_off_after_its_audio_began_is_still_counted() -> None:
    meter, store, _, _ = await meter_for()
    with pytest.raises(VoiceError):
        await speak(meter.voice(Voice(fail_after=1), "elevenlabs"), "Hello.")
    assert len(store.rows) == 1
    # One refused outright was never billed.
    with pytest.raises(VoiceError):
        await speak(meter.voice(Voice(fail_after=0), "elevenlabs"), "Hello.")
    assert len(store.rows) == 1


async def test_the_system_voice_is_wrapped_but_never_refused_or_counted() -> None:
    meter, store, _, _ = await meter_for()
    metered = meter.voice(Voice(), None)
    await speak(metered, "Hello.")
    assert store.rows == []
    assert (metered.name, metered.where, metered.output_format) == ("elevenlabs", "core", CAPTURE)


# ── Hearing ──────────────────────────────────────────────────────────────


class Stream:
    def __init__(self) -> None:
        self.sent = 0
        self.closed = 0

    async def send(self, pcm: bytes) -> None:
        self.sent += len(pcm)

    async def finish(self) -> None:
        return None

    async def close(self) -> None:
        self.closed += 1

    async def events(self) -> AsyncIterator[Heard]:
        yield Heard(text="hi", final=True)


class Hearing:
    name = "deepgram"
    streaming = True
    turns = True
    word_times = True

    def __init__(self) -> None:
        self.opened = 0

    def model_for(self, choice: str) -> str:
        return "flux-general-en"

    async def open(self, *, choice: str) -> Stream:
        self.opened += 1
        return Stream()

    async def choices(self) -> list[Any]:
        return []

    async def prepare(self, choice: str) -> None:
        return None


SECOND = CAPTURE.sample_rate * CAPTURE.channels * 2


async def test_hearing_is_counted_in_whole_seconds_rounded_up_on_close() -> None:
    meter, store, ledger, _ = await meter_for()
    metered = meter.hearing(Hearing(), "deepgram")
    assert isinstance(metered, MeteredHearing)
    with as_caller("conversation", "c1"):
        stream = await metered.open(choice="en")
    await stream.send(b"\x00" * SECOND)
    await stream.send(b"\x00" * (SECOND // 2))
    assert [h async for h in stream.events()] == [Heard(text="hi", final=True)]
    await stream.finish()
    assert store.rows == []
    await stream.close()
    await stream.close()  # twice is safe, and counts once
    (row,) = store.rows
    assert row["units"] == "seconds" and row["usage"] == Usage(input_tokens=2)
    assert (row["model"], row["caller"], row["conversation_id"]) == (
        "flux-general-en",
        "conversation",
        "c1",
    )
    assert row["cost_usd"] == prices.price("deepgram", "flux-general-en", 2)
    assert ledger.spent("deepgram").dollars == pytest.approx(row["cost_usd"])


async def test_a_stream_that_crosses_the_cap_is_not_cut_but_the_next_open_is_refused() -> None:
    meter, store, ledger, _ = await meter_for()
    inner = Hearing()
    metered = meter.hearing(inner, "deepgram")
    stream = await metered.open(choice="en")
    over(ledger, "deepgram")
    await stream.send(b"\x00" * SECOND)
    await stream.close()
    assert len(store.rows) == 1
    with pytest.raises(VoiceCapped):
        await metered.open(choice="en")
    assert inner.opened == 1


async def test_local_hearing_is_wrapped_but_never_refused_or_counted() -> None:
    meter, store, _, _ = await meter_for()
    metered = meter.hearing(Hearing(), None)
    stream = await metered.open(choice="en")
    await stream.send(b"\x00" * SECOND)
    await stream.close()
    assert store.rows == []
    assert (metered.streaming, metered.turns, metered.word_times) == (True, True, True)
    metered.word_times = False
    assert metered.word_times is False
