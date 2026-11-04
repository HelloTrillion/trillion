"""The spend gate: where each provider stands against its caps this month (BRN-7)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from trillion.brain.types import ProviderError
from trillion.spend.caller import as_caller
from trillion.spend.gate import (
    DEFAULT,
    NO_CAPS,
    Caps,
    Gate,
    ModelCapped,
    SpendCapped,
    VoiceCapped,
    parse_caps,
)
from trillion.spend.ledger import Ledger, Row
from trillion.spend.types import Caller, Unit
from trillion.voice.types import VoiceError

NOW = 1793800000000  # 2026-11-04, well inside the month in UTC


class Rows:
    """A usage store that holds rows in memory."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.reads = 0

    async def spend_since(
        self, owner_id: str, since_ms: int, until_ms: int | None = None
    ) -> list[dict[str, Any]]:
        self.reads += 1
        return list(self.rows)


class Clock:
    def __init__(self, now: int = NOW) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def source(raw: Any) -> Callable[[], Awaitable[Any]]:
    async def read() -> Any:
        return raw

    return read


async def gate_for(raw: Any, tz: str | Callable[[], str] = "UTC") -> tuple[Gate, Ledger]:
    ledger = Ledger(Rows(), Clock(), tz)
    await ledger.load()
    gate = Gate(ledger, source(raw))
    await gate.reload_caps()
    return gate, ledger


def spend(
    ledger: Ledger,
    pid: str,
    *,
    cost: float | None = None,
    quantity: float = 0,
    unit: Unit = "tokens",
    caller: Caller = "conversation",
) -> None:
    ledger.add(Row(pid, caller, unit, quantity, cost, NOW))


CAPS = {"anthropic": {"soft": 20.0, "hard": 50.0, "warn_pct": 80}}


# ── States ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("dollars", "state"),
    [
        (0.0, "ok"),
        (15.99, "ok"),
        (16.0, "warned"),
        (19.99, "warned"),
        (20.0, "over_soft"),
        (49.99, "over_soft"),
        (50.0, "capped"),
        (80.0, "capped"),
    ],
)
async def test_each_state_at_and_around_its_threshold(dollars: float, state: str) -> None:
    gate, ledger = await gate_for(CAPS)
    if dollars:
        spend(ledger, "anthropic", cost=dollars)
    assert gate.state("anthropic") == state


async def test_the_warning_follows_its_own_percentage() -> None:
    gate, ledger = await gate_for({"anthropic": {"soft": 10, "hard": 100, "warn_pct": 50}})
    spend(ledger, "anthropic", cost=4.99)
    assert gate.state("anthropic") == "ok"
    spend(ledger, "anthropic", cost=0.01)
    assert gate.state("anthropic") == "warned"


async def test_an_unpriced_provider_reads_its_caps_as_units() -> None:
    caps = {"custom:example.com": {"soft": 1000, "hard": 5000, "warn_pct": 80}}
    gate, ledger = await gate_for(caps)
    spend(ledger, "custom:example.com", quantity=799)
    assert gate.state("custom:example.com") == "ok"
    spend(ledger, "custom:example.com", quantity=1)
    assert gate.state("custom:example.com") == "warned"
    spend(ledger, "custom:example.com", quantity=200)
    assert gate.state("custom:example.com") == "over_soft"
    spend(ledger, "custom:example.com", quantity=4000)
    assert gate.state("custom:example.com") == "capped"


async def test_characters_are_units_too() -> None:
    gate, ledger = await gate_for({"openai-voice:example.com": {"soft": 100, "hard": 200}})
    spend(ledger, "openai-voice:example.com", quantity=200, unit="chars")
    assert gate.state("openai-voice:example.com") == "capped"


async def test_priced_and_unpriced_use_together_take_the_worse_state() -> None:
    caps = {"custom:example.com": {"soft": 100, "hard": 1000, "warn_pct": 80}}
    gate, ledger = await gate_for(caps)
    # $1 is well under a $100 soft cap, but 1000 unpriced tokens is at the hard cap.
    spend(ledger, "custom:example.com", cost=1.0)
    spend(ledger, "custom:example.com", quantity=1000)
    assert gate.state("custom:example.com") == "capped"


async def test_unpriced_units_never_lower_what_dollars_say() -> None:
    caps = {"custom:example.com": {"soft": 100, "hard": 1000, "warn_pct": 80}}
    gate, ledger = await gate_for(caps)
    spend(ledger, "custom:example.com", cost=100.0)
    spend(ledger, "custom:example.com", quantity=5)
    assert gate.state("custom:example.com") == "over_soft"


async def test_removed_caps_are_never_capped() -> None:
    gate, ledger = await gate_for({"anthropic": {"soft": None, "hard": None}})
    spend(ledger, "anthropic", cost=10_000.0, quantity=10**9)
    assert gate.state("anthropic") == "ok"
    await gate.check_model("anthropic")


async def test_a_soft_cap_alone_warns_but_never_caps() -> None:
    gate, ledger = await gate_for({"anthropic": {"soft": 20, "hard": None}})
    spend(ledger, "anthropic", cost=1000.0)
    assert gate.state("anthropic") == "over_soft"


async def test_a_hard_cap_alone_caps_with_no_warning_before() -> None:
    gate, ledger = await gate_for({"anthropic": {"soft": None, "hard": 50}})
    spend(ledger, "anthropic", cost=49.0)
    assert gate.state("anthropic") == "ok"
    spend(ledger, "anthropic", cost=1.0)
    assert gate.state("anthropic") == "capped"


async def test_local_and_no_provider_are_never_capped() -> None:
    caps = {"local": {"soft": 0, "hard": 0}}
    gate, ledger = await gate_for(caps)
    spend(ledger, "local", cost=5.0, quantity=10**9)
    assert gate.state("local") == "ok"
    assert gate.state(None) == "ok"
    await gate.check_model("local")
    await gate.check_voice(None)


async def test_conversation_and_background_count_toward_the_same_cap() -> None:
    gate, ledger = await gate_for(CAPS)
    spend(ledger, "anthropic", cost=25.0)
    spend(ledger, "anthropic", cost=25.0, caller="background")
    assert gate.state("anthropic") == "capped"


# ── Caps from preferences ────────────────────────────────────────────────


def test_caps_parse_from_the_preference() -> None:
    got = parse_caps(
        {
            "anthropic": {"soft": 10, "hard": 30.5, "warn_pct": 90},
            "openai": {"soft": None, "hard": 40},
        }
    )
    assert got == {"anthropic": Caps(10.0, 30.5, 90), "openai": Caps(None, 40.0, 80)}


def test_no_preference_means_no_entries() -> None:
    assert parse_caps(None) == {}


@pytest.mark.parametrize(
    "entry",
    [
        "twenty",
        None,
        {"hard": 50},  # soft left out: a typo, not a removal
        {"soft": 20},
        {"soft": "20", "hard": 50},
        {"soft": True, "hard": 50},
        {"soft": -1, "hard": 50},
        {"soft": float("inf"), "hard": None},
        {"soft": float("nan"), "hard": None},
        {"soft": 60, "hard": 50},  # hard below soft
        {"soft": 20, "hard": 50, "warn_pct": 0},
        {"soft": 20, "hard": 50, "warn_pct": 101},
        {"soft": 20, "hard": 50, "warn_pct": 80.5},
        {"soft": 20, "hard": 50, "warn_pct": True},
    ],
)
def test_a_malformed_entry_falls_back_to_the_defaults(entry: Any) -> None:
    assert parse_caps({"anthropic": entry}) == {"anthropic": DEFAULT}


def test_a_malformed_entry_never_loses_the_others() -> None:
    got = parse_caps({"anthropic": "x", "openai": {"soft": None, "hard": None}, 3: {}})
    assert got == {"anthropic": DEFAULT, "openai": NO_CAPS}


async def test_a_provider_with_no_entry_has_no_caps() -> None:
    gate, ledger = await gate_for(CAPS)
    spend(ledger, "openai", cost=10_000.0)
    assert gate.caps("openai") == NO_CAPS
    assert gate.state("openai") == "ok"


@pytest.mark.parametrize("raw", ["oops", 42, ["anthropic"]])
async def test_a_malformed_preference_caps_every_provider_at_the_defaults(raw: Any) -> None:
    gate, ledger = await gate_for(raw)
    assert gate.caps("openai") == DEFAULT
    spend(ledger, "openai", cost=50.0)
    assert gate.state("openai") == "capped"


async def test_caps_are_read_again_on_reload() -> None:
    raw: dict[str, Any] = {"anthropic": {"soft": 20, "hard": 50}}
    ledger = Ledger(Rows(), Clock(), "UTC")
    await ledger.load()
    gate = Gate(ledger, source(raw))
    await gate.reload_caps()
    spend(ledger, "anthropic", cost=50.0)
    assert gate.state("anthropic") == "capped"
    # Raising the cap is writing a new hard cap: nothing else is needed.
    raw["anthropic"] = {"soft": 20, "hard": 100}
    assert gate.state("anthropic") == "capped"
    await gate.reload_caps()
    assert gate.state("anthropic") == "over_soft"


async def test_the_gate_refuses_to_answer_before_its_caps_are_read() -> None:
    ledger = Ledger(Rows(), Clock(), "UTC")
    await ledger.load()
    gate = Gate(ledger, source(CAPS))
    with pytest.raises(RuntimeError):
        gate.state("anthropic")
    with pytest.raises(RuntimeError):
        await gate.check_model("anthropic")


# ── Refusing ─────────────────────────────────────────────────────────────


async def test_check_lets_a_call_through_below_the_hard_cap() -> None:
    gate, ledger = await gate_for(CAPS)
    spend(ledger, "anthropic", cost=49.99)
    await gate.check_model("anthropic")
    await gate.check_voice("anthropic")


async def test_check_model_refuses_as_a_provider_error_with_kind_capped() -> None:
    gate, ledger = await gate_for(CAPS)
    spend(ledger, "anthropic", cost=50.0)
    with as_caller("conversation", "c1"), pytest.raises(ProviderError) as caught:
        await gate.check_model("anthropic")
    err = caught.value
    assert isinstance(err, ModelCapped) and isinstance(err, SpendCapped)
    assert not isinstance(err, VoiceError)
    assert err.kind == "capped"
    assert (err.provider_id, err.caller, err.cap, err.unit) == (
        "anthropic",
        "conversation",
        50.0,
        "dollars",
    )


async def test_check_voice_refuses_as_a_voice_error_with_kind_capped() -> None:
    gate, ledger = await gate_for({"elevenlabs": {"soft": 1, "hard": 2}})
    spend(ledger, "elevenlabs", cost=2.0, unit="chars")
    with pytest.raises(VoiceError) as caught:
        await gate.check_voice("elevenlabs")
    err = caught.value
    assert isinstance(err, VoiceCapped) and isinstance(err, SpendCapped)
    assert not isinstance(err, ProviderError)
    assert err.kind == "capped"
    # No caller set: background, the stricter way.
    assert err.caller == "background"


async def test_a_refusal_on_units_names_the_unit() -> None:
    gate, ledger = await gate_for({"custom:example.com": {"soft": 10, "hard": 100}})
    spend(ledger, "custom:example.com", quantity=100)
    with pytest.raises(SpendCapped) as caught:
        await gate.check_model("custom:example.com")
    assert (caught.value.cap, caught.value.unit) == (100.0, "tokens")


async def test_check_counts_from_the_new_start_after_the_timezone_moves() -> None:
    # The same month, but in Chicago it began five hours later than in UTC.
    zone = ["UTC"]
    rows = Rows()
    ledger = Ledger(rows, Clock(), lambda: zone[0])
    await ledger.load()
    gate = Gate(ledger, source(CAPS))
    await gate.reload_caps()
    rows.rows = [
        {"provider_id": "anthropic", "caller": "conversation", "units": "tokens"}
        | {"cost_usd": 60.0, "unpriced": 0}
    ]
    zone[0] = "America/Chicago"
    with pytest.raises(ModelCapped):
        await gate.check_model("anthropic")
    assert not ledger.stale
    assert rows.reads == 2
