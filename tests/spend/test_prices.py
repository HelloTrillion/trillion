"""The price table, user prices, OpenRouter's own cost, and provider ids. No network."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest

from trillion.brain.providers.anthropic import AnthropicProvider
from trillion.brain.providers.openai_compat import OpenAICompatProvider
from trillion.brain.types import Done, Msg, Pricing, TextBlock, Usage
from trillion.spend import prices
from trillion.spend.prices import (
    TABLE,
    Rate,
    ceiling_rate,
    estimate,
    hearing_provider_id,
    parse_user_prices,
    price,
    provider_id,
    voice_provider_id,
)

TODAY = date(2026, 11, 4)
MILLION = Usage(input_tokens=1_000_000)


# ── Anthropic: moved, unchanged ─────────────────────────────────────────

# What AnthropicProvider.pricing() returned before the table moved, captured from it.
_OLD: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.2, 5.0),
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-7": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-6": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-5": (5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-5-5": (2.0, 10.0, 0.1, 2.5),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-sonnet-4-6": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-4-5": (1.0, 5.0, 0.1, 1.25),
}


@pytest.mark.parametrize("model", sorted(_OLD))
def test_anthropic_pricing_is_what_it_was(model: str) -> None:
    i, o, cr, cw = _OLD[model]
    assert AnthropicProvider(api_key=lambda: "", model=model).pricing() == Pricing(
        input=i, output=o, cache_read=cr, cache_write=cw
    )


def test_anthropic_haiku_5_5_keeps_its_long_prompt_rates() -> None:
    assert AnthropicProvider(api_key=lambda: "", model="claude-haiku-5-5").pricing() == Pricing(
        input=0.1,
        output=0.5,
        cache_read=0.01,
        cache_write=0.125,
        long_prompt_over=100_000,
        long_prompt=Pricing(input=0.5, output=2.5, cache_read=0.05, cache_write=0.625),
    )


def test_anthropic_models_in_the_table_are_exactly_the_old_ones() -> None:
    assert {m for (p, m) in TABLE if p == "anthropic"} == {*_OLD, "claude-haiku-5-5"}


def test_anthropic_dated_ids_and_unknown_models_still_price_as_before() -> None:
    dated = AnthropicProvider(api_key=lambda: "", model="claude-haiku-4-5-20251001").pricing()
    assert dated == AnthropicProvider(api_key=lambda: "", model="claude-haiku-4-5").pricing()
    assert AnthropicProvider(api_key=lambda: "", model="something-new").pricing() is None


def test_haiku_5_5_long_prompt_bills_the_whole_request_higher() -> None:
    at_line = Usage(input_tokens=100_000, output_tokens=1_000)
    assert price("anthropic", "claude-haiku-5-5", at_line) == pytest.approx(0.0105)
    over = Usage(input_tokens=90_000, cache_read_tokens=10_001, output_tokens=1_000)
    assert price("anthropic", "claude-haiku-5-5", over) == pytest.approx(0.04800005)


# ── Every table entry ───────────────────────────────────────────────────


@pytest.mark.parametrize("key", sorted(TABLE), ids=lambda k: f"{k[0]}/{k[1]}")
def test_every_entry_says_when_and_where_it_was_checked(key: tuple[str, str]) -> None:
    rate = TABLE[key]
    assert rate.checked is not None and rate.checked <= TODAY
    assert rate.source.startswith("https://")
    if rate.unit == "tokens":
        assert rate.input > 0 and rate.output > 0 and rate.per_unit == 0
    else:
        assert rate.per_unit > 0 and rate.input == rate.output == 0


# Provider, model: input, output, cache read per million tokens.
_TOKENS: dict[tuple[str, str], tuple[float, float, float]] = {
    ("openai", "gpt-6-astra"): (10.0, 50.0, 1.0),
    ("openai", "gpt-6.1-sol"): (2.0, 10.0, 0.10),
    ("openai", "gpt-6.1-terra"): (1.0, 5.0, 0.05),
    ("openai", "gpt-6-sol"): (2.0, 10.0, 0.20),
    ("openai", "gpt-6-luna"): (0.10, 0.50, 0.01),
    ("openai", "gpt-5.6-sol"): (4.0, 20.0, 0.40),
    ("openai", "gpt-5.6-terra"): (2.0, 12.0, 0.20),
    ("openai", "gpt-5.6-luna"): (0.20, 1.20, 0.02),
    ("openai", "gpt-5.5"): (5.0, 30.0, 0.50),
    ("openai", "gpt-5.4"): (2.50, 15.0, 0.25),
    ("openai", "gpt-5.4-mini"): (0.75, 4.50, 0.075),
    ("openai", "gpt-5.4-nano"): (0.20, 1.25, 0.02),
    ("openai", "gpt-5.2"): (1.75, 14.0, 0.175),
    ("openai", "gpt-5.1"): (1.25, 10.0, 0.125),
    ("openai", "gpt-5"): (1.25, 10.0, 0.125),
    ("openai", "gpt-5-mini"): (0.25, 2.0, 0.025),
    ("openai", "gpt-5-nano"): (0.05, 0.40, 0.005),
    ("openai", "gpt-4.1"): (2.0, 8.0, 0.50),
    ("openai", "gpt-4.1-mini"): (0.40, 1.60, 0.10),
    ("openai", "gpt-4o"): (2.50, 10.0, 1.25),
    ("openai", "gpt-4o-mini"): (0.15, 0.60, 0.075),
    ("custom:api.deepseek.com", "deepseek-flash"): (0.3, 1.2, 0.006),
    ("custom:api.deepseek.com", "deepseek-v4-flash"): (0.3, 1.2, 0.006),
    ("custom:api.deepseek.com", "deepseek-v4-pro"): (1.32, 3.96, 0.044),
}


@pytest.mark.parametrize("key", sorted(_TOKENS), ids=lambda k: f"{k[0]}/{k[1]}")
def test_token_rates(key: tuple[str, str]) -> None:
    i, o, cr = _TOKENS[key]
    pid, model = key
    # 100K tokens: under every long-prompt line.
    assert price(pid, model, Usage(input_tokens=100_000)) == pytest.approx(i / 10)
    assert price(pid, model, Usage(output_tokens=100_000)) == pytest.approx(o / 10)
    assert price(pid, model, Usage(cache_read_tokens=100_000)) == pytest.approx(cr / 10)


def test_every_token_entry_outside_anthropic_is_tested() -> None:
    priced = {k for k, r in TABLE.items() if r.unit == "tokens" and k[0] != "anthropic"}
    assert priced == set(_TOKENS)


# Long context: more than 272K input tokens bills the whole request at these.
_OPENAI_LONG: dict[str, tuple[float, float, float]] = {
    "gpt-6-astra": (20.0, 75.0, 2.0),
    "gpt-6.1-sol": (4.0, 15.0, 0.20),
    "gpt-6.1-terra": (2.0, 7.50, 0.10),
    "gpt-6-sol": (4.0, 15.0, 0.40),
    "gpt-6-luna": (0.20, 0.75, 0.02),
    "gpt-5.6-sol": (8.0, 30.0, 0.80),
    "gpt-5.6-terra": (4.0, 18.0, 0.40),
    "gpt-5.6-luna": (0.40, 1.80, 0.04),
    "gpt-5.5": (10.0, 45.0, 1.0),
    "gpt-5.4": (5.0, 22.50, 0.50),
}


@pytest.mark.parametrize("model", sorted(_OPENAI_LONG))
def test_openai_long_context_rates(model: str) -> None:
    i, o, cr = _OPENAI_LONG[model]
    at_line = Usage(input_tokens=272_000, output_tokens=1_000_000)
    short_i, short_o, _ = _TOKENS[("openai", model)]
    assert price("openai", model, at_line) == pytest.approx(0.272 * short_i + short_o)
    over = Usage(input_tokens=272_000, cache_read_tokens=1_000_000, output_tokens=1_000_000)
    assert price("openai", model, over) == pytest.approx(0.272 * i + cr + o)


def test_only_the_listed_openai_models_have_a_long_context_tier() -> None:
    tiered = {m for (p, m), r in TABLE.items() if p == "openai" and r.long_prompt is not None}
    assert tiered == set(_OPENAI_LONG)


def test_a_dated_openai_id_is_unpriced_unless_listed() -> None:
    # An OpenAI snapshot can bill differently from its alias (gpt-4o-2024-05-13 did).
    assert price("openai", "gpt-4o-2024-08-06", MILLION) is None
    assert price("openai", "gpt-4o-2024-05-13", MILLION) is None


def test_only_anthropic_dated_ids_price_like_their_alias() -> None:
    dated = price("anthropic", "claude-haiku-4-5-20251001", MILLION)
    assert dated == price("anthropic", "claude-haiku-4-5", MILLION) is not None
    assert price("openai", "gpt-4o-20240806", MILLION) is None
    mine = parse_user_prices({"custom:example.com": {"m": {"input": 1, "output": 1}}})
    assert price("custom:example.com", "m-20250101", MILLION, mine) is None


def test_elevenlabs_flash_per_thousand_characters() -> None:
    assert price("elevenlabs", "eleven_flash_v2_5", 1_000) == pytest.approx(0.04)
    assert price("elevenlabs", "eleven_flash_v2_5", 250) == pytest.approx(0.01)


def test_deepgram_flux_per_minute_from_seconds() -> None:
    assert price("deepgram", "flux-general-en", 60) == pytest.approx(0.0077)
    assert price("deepgram", "flux-general-multi", 90) == pytest.approx(0.0078 * 1.5)


# ── Order: user price, reported cost, table, local ──────────────────────


def test_a_user_price_wins_over_the_table() -> None:
    mine = parse_user_prices({"openai": {"gpt-4o": {"input": 1.0, "output": 2.0}}})
    assert price("openai", "gpt-4o", MILLION, mine) == pytest.approx(1.0)


def test_a_user_price_covers_a_model_the_table_does_not_know() -> None:
    mine = parse_user_prices({"custom:example.com": {"big-model": {"input": 3, "output": 6}}})
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    assert price("custom:example.com", "big-model", usage, mine) == pytest.approx(6.0)
    assert price("custom:example.com", "big-model", usage) is None


def test_a_user_rate_for_voice_covers_any_model() -> None:
    mine = parse_user_prices({"elevenlabs": {"*": {"unit": "chars", "per_unit": 0.02}}})
    assert price("elevenlabs", "eleven_flash_v2_5", 1_000, mine) == pytest.approx(0.02)
    assert price("elevenlabs", "eleven_v3", 1_000, mine) == pytest.approx(0.02)


def test_a_user_rate_for_one_model_wins_over_their_rate_for_any() -> None:
    mine = parse_user_prices(
        {
            "deepgram": {
                "*": {"unit": "seconds", "per_unit": 0.01},
                "flux-general-en": {"unit": "seconds", "per_unit": 0.005},
            }
        }
    )
    assert price("deepgram", "flux-general-en", 60, mine) == pytest.approx(0.005)
    assert price("deepgram", "flux-general-multi", 60, mine) == pytest.approx(0.01)


def test_openrouters_reported_cost_wins_over_the_table() -> None:
    usage = Usage(input_tokens=1_000_000, reported_cost=0.0123)
    assert price("openai", "gpt-4o", usage) == pytest.approx(0.0123)
    assert price("custom:openrouter.ai", "openai/gpt-4o", usage) == pytest.approx(0.0123)


def test_a_user_price_wins_over_a_reported_cost() -> None:
    mine = parse_user_prices({"custom:openrouter.ai": {"x/y": {"input": 1, "output": 1}}})
    usage = Usage(input_tokens=1_000_000, reported_cost=5.0)
    assert price("custom:openrouter.ai", "x/y", usage, mine) == pytest.approx(1.0)


def test_a_reported_cost_of_zero_is_a_real_zero() -> None:
    assert price("custom:openrouter.ai", "free/model", Usage(reported_cost=0.0)) == 0.0


def test_an_unknown_model_is_unpriced_and_a_local_one_is_free() -> None:
    assert price("openai", "gpt-unknown", MILLION) is None
    assert price("custom:example.com", "anything", MILLION) is None
    assert price("anthropic", "something-new", MILLION) is None
    assert price("local", "llama3", MILLION) == 0.0


def test_a_count_against_a_token_rate_or_usage_against_a_per_unit_rate_is_unpriced() -> None:
    assert price("openai", "gpt-4o", 1_000) is None
    assert price("elevenlabs", "eleven_flash_v2_5", MILLION) is None


def test_reported_cost_adds_up_and_stays_none_when_nothing_reported() -> None:
    assert (Usage() + Usage()).reported_cost is None
    assert (Usage() + Usage(reported_cost=0.5)).reported_cost == 0.5
    assert (Usage(reported_cost=0.25) + Usage(reported_cost=0.5)).reported_cost == 0.75


def test_a_partly_reported_total_is_not_reported() -> None:
    reported = Usage(input_tokens=10, reported_cost=0.25)
    unreported = Usage(input_tokens=10)
    # A round that used tokens without saying what they cost makes the total unknown.
    assert (reported + unreported).reported_cost is None
    assert (unreported + reported).reported_cost is None
    # Once unknown, later reported rounds do not bring a partial figure back.
    assert (reported + unreported + reported).reported_cost is None
    # An empty Usage() is the starting point of a sum, and changes nothing.
    assert (Usage() + reported + Usage() + reported).reported_cost == 0.5
    # A reported zero is a real zero, even with no tokens.
    assert (Usage(reported_cost=0.0) + reported).reported_cost == 0.25


# ── User prices from the preference ─────────────────────────────────────


def test_user_prices_parse_and_malformed_entries_are_ignored() -> None:
    raw: Any = {
        "openai": {
            "gpt-4o": {"input": 1, "output": 2, "cache_read": 0.5, "cache_write": 1.25},
            "bad-unit": {"unit": "liters", "per_unit": 1},
            "negative": {"input": -1, "output": 2},
            "words": {"input": "cheap", "output": 2},
            "boolean": {"input": True, "output": 2},
            "nothing": {},
            "not-a-dict": 3,
        },
        "elevenlabs": {"*": {"unit": "chars", "per_unit": 0.03}},
        "deepgram": {"*": {"unit": "seconds"}},
        "broken": "not a dict",
    }
    assert parse_user_prices(raw) == {
        ("openai", "gpt-4o"): Rate(
            unit="tokens", input=1.0, output=2.0, cache_read=0.5, cache_write=1.25, source="user"
        ),
        ("elevenlabs", "*"): Rate(unit="chars", per_unit=0.03, source="user"),
    }
    assert parse_user_prices(None) == {}
    assert parse_user_prices([1, 2]) == {}


# ── Provider ids ────────────────────────────────────────────────────────


def test_model_provider_ids() -> None:
    assert provider_id("anthropic", None) == "anthropic"
    assert provider_id("openai", None) == "openai"
    assert provider_id("openai", "https://ignored.example.com/v1") == "openai"
    assert provider_id("local", None) == "local"
    assert provider_id("local", "http://localhost:1234/v1") == "local"
    assert provider_id("custom", "https://openrouter.ai/api/v1") == "custom:openrouter.ai"
    assert provider_id("custom", "https://API.DeepSeek.com/") == "custom:api.deepseek.com"
    assert provider_id("custom", "http://192.168.1.20:8080/v1") == "custom:192.168.1.20"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8080/v1",
        "http://127.5.0.1/v1",
        "http://[::1]:8080/v1",
        "http://ollama.localhost/v1",
    ],
)
def test_a_custom_model_on_this_computer_is_local_and_its_voice_free(url: str) -> None:
    assert provider_id("custom", url) == "local"
    assert voice_provider_id("custom", url) is None


def test_openais_own_address_is_openai_whatever_it_is_called() -> None:
    assert provider_id("custom", "https://api.openai.com/v1") == "openai"
    assert provider_id("custom", "https://API.OpenAI.com/v1/") == "openai"


def test_a_custom_model_without_a_usable_address_has_no_id() -> None:
    assert provider_id("custom", None) is None
    assert provider_id("custom", "not a url") is None
    assert provider_id("custom", "http://example.com/v1") is None  # refused by the check


def test_voice_and_hearing_provider_ids() -> None:
    assert voice_provider_id("elevenlabs", None) == "elevenlabs"
    assert voice_provider_id("custom", "http://192.168.1.5:8880/v1") == "openai-voice:192.168.1.5"
    assert (
        voice_provider_id("custom", "https://tts.example.com/v1") == "openai-voice:tts.example.com"
    )
    assert voice_provider_id("custom", None) is None
    assert voice_provider_id("system", None) is None
    assert hearing_provider_id("deepgram") == "deepgram"
    assert hearing_provider_id("whisper") is None


def test_the_voice_and_hearing_models_the_app_uses_are_priced() -> None:
    from trillion.voice.providers import deepgram, elevenlabs

    assert price("elevenlabs", elevenlabs.DEFAULT_MODEL, 1_000) is not None
    for model in deepgram.MODELS.values():
        assert price("deepgram", model, 60) is not None


# ── OpenRouter on the wire ──────────────────────────────────────────────

HISTORY = [Msg(role="user", blocks=(TextBlock(text="hi"),))]


def _stream(*chunks: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


async def _run(base_url: str, body: bytes) -> tuple[dict[str, Any], Done]:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=body)

    p = OpenAICompatProvider(
        base_url=base_url,
        model="m",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    events = [e async for e in p.stream(system=[], messages=HISTORY, tools=[], max_tokens=64)]
    done = events[-1]
    assert isinstance(done, Done)
    return seen, done


_WITH_COST = _stream(
    {"choices": [{"delta": {"content": "Hi"}}]},
    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.00042}},
)


async def test_openrouter_is_asked_for_its_cost_and_the_cost_is_read() -> None:
    body, done = await _run("https://openrouter.ai/api/v1", _WITH_COST)
    assert body["usage"] == {"include": True}
    assert body["stream_options"] == {"include_usage": True}
    assert done.usage == Usage(input_tokens=10, output_tokens=2, reported_cost=0.00042)


async def test_other_servers_are_not_asked_and_a_cost_they_send_is_ignored() -> None:
    body, done = await _run("http://localhost:11434/v1", _WITH_COST)
    assert "usage" not in body
    assert done.usage.reported_cost is None


async def test_deepseek_cache_hits_are_read_from_its_own_field() -> None:
    chunk = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5}}
    chunk["usage"]["prompt_cache_hit_tokens"] = 60
    _, done = await _run("https://api.deepseek.com", _stream(chunk))
    assert done.usage == Usage(input_tokens=40, output_tokens=5, cache_read_tokens=60)
    # The standard field wins when both are sent.
    chunk["usage"]["prompt_tokens_details"] = {"cached_tokens": 30}
    _, done = await _run("https://api.deepseek.com", _stream(chunk))
    assert done.usage.cache_read_tokens == 30


async def test_an_openrouter_cost_that_is_not_a_number_is_left_out() -> None:
    bad = _stream({"choices": [], "usage": {"prompt_tokens": 1, "cost": "free"}})
    _, done = await _run("https://openrouter.ai/api/v1", bad)
    assert done.usage.reported_cost is None


def test_the_module_says_what_its_public_names_are() -> None:
    assert set(prices.__all__) >= {
        "TABLE",
        "Rate",
        "price",
        "provider_id",
        "voice_provider_id",
        "hearing_provider_id",
        "parse_user_prices",
    }


# ── The ceiling rate: estimating what nothing priced ─────────────────────


def test_the_ceiling_rate_is_the_providers_highest_token_rate() -> None:
    highest = 0.0
    for (pid, _model), rate in prices.TABLE.items():
        if pid == "anthropic":
            longer = rate.long_prompt.output if rate.long_prompt else 0.0
            highest = max(highest, rate.output, longer)
    assert prices.ceiling_rate("anthropic", "tokens") == highest
    assert prices.ceiling_rate("custom:api.deepseek.com", "tokens") == 3.96


def test_the_ceiling_rate_for_characters_and_seconds_is_the_highest_per_unit() -> None:
    assert prices.ceiling_rate("elevenlabs", "chars") == 0.04
    assert prices.ceiling_rate("deepgram", "seconds") == 0.0078


def test_no_rate_in_the_table_means_no_ceiling() -> None:
    assert prices.ceiling_rate("custom:example.com", "tokens") is None
    assert prices.ceiling_rate("elevenlabs", "tokens") is None
    assert prices.ceiling_rate("anthropic", "chars") is None


def test_an_estimate_prices_units_at_the_ceiling() -> None:
    ceiling = prices.ceiling_rate("anthropic", "tokens")
    assert ceiling is not None
    assert prices.estimate("anthropic", "tokens", 2_000_000) == pytest.approx(2 * ceiling)
    assert prices.estimate("elevenlabs", "chars", 10_000) == pytest.approx(0.4)
    assert prices.estimate("deepgram", "seconds", 600) == pytest.approx(0.078)
    assert prices.estimate("custom:example.com", "tokens", 1000) is None


# ── What a provider roughly costs, before and after a key (ONB-9, ONB-10) ──


def _talk_hour(rate: Rate) -> float:
    turns, given, back = prices.TALK_TURNS, prices.TURN_INPUT, prices.TURN_OUTPUT
    return turns * (given * rate.input + back * rate.output) / 1_000_000


def test_the_assumption_is_the_one_the_spec_states() -> None:
    assert (prices.TALK_TURNS, prices.TURN_INPUT, prices.TURN_OUTPUT) == (40, 3_000, 150)
    assert (prices.VOICE_SENTENCES, prices.SENTENCE_CHARS) == (40, 80)
    assert prices.HEARING_MINUTES == 60


def test_an_hour_of_talk_is_priced_from_the_table() -> None:
    sonnet = TABLE[("anthropic", "claude-sonnet-5-5")]
    assert prices.hourly("anthropic", "claude-sonnet-5-5") == pytest.approx(_talk_hour(sonnet))
    gpt = TABLE[("openai", "gpt-6-sol")]
    assert prices.hourly("openai", "gpt-6-sol") == pytest.approx(_talk_hour(gpt))
    # A dated Anthropic id prices like its alias.
    assert prices.hourly("anthropic", "claude-sonnet-5-5-20261001") == pytest.approx(
        _talk_hour(sonnet)
    )


def test_an_hour_of_voice_and_hearing_is_priced_from_the_table() -> None:
    chars = prices.VOICE_SENTENCES * prices.SENTENCE_CHARS
    assert prices.hourly("elevenlabs", None) == pytest.approx(chars / 1_000 * 0.04)
    assert prices.hourly("elevenlabs", "eleven_flash_v2_5") == pytest.approx(chars / 1_000 * 0.04)
    assert prices.hourly("deepgram", "flux-general-en") == pytest.approx(60 * 0.0077)
    # With no model named, hearing is estimated at the dearest rate.
    assert prices.hourly("deepgram", None) == pytest.approx(60 * 0.0078)


def test_a_user_price_wins_for_the_estimate() -> None:
    mine = parse_user_prices({"anthropic": {"claude-sonnet-5-5": {"input": 1, "output": 1}}})
    assert prices.hourly("anthropic", "claude-sonnet-5-5", mine) == pytest.approx(
        40 * 3_150 / 1_000_000
    )
    plan = parse_user_prices({"elevenlabs": {"*": {"unit": "chars", "per_unit": 0.01}}})
    assert prices.hourly("elevenlabs", None, plan) == pytest.approx(3.2 * 0.01)


def test_nothing_known_is_no_estimate_never_a_guess() -> None:
    assert prices.hourly("anthropic", "claude-unreleased-9") is None
    assert prices.hourly("anthropic", None) is None
    assert prices.hourly("custom:example.com", "m") is None
    assert prices.hourly("openai-voice:speech.example.com", None) is None


def test_the_line_says_the_amount_and_the_assumption() -> None:
    sonnet = _talk_hour(TABLE[("anthropic", "claude-sonnet-5-5")])
    assert prices.cost_line("anthropic", "claude-sonnet-5-5") == (
        f"Roughly ${sonnet:.2f} an hour of talk on Sonnet 5.5 (about 40 short turns), "
        "plus routines."
    )
    assert prices.cost_line("elevenlabs") == (
        "Roughly $0.13 an hour of speaking (about 40 sentences of 80 characters)."
    )
    assert prices.cost_line("deepgram", "flux-general-en") == (
        "Roughly $0.46 an hour of listening (60 minutes of audio)."
    )


def test_a_tiny_amount_is_less_than_a_cent_never_zero() -> None:
    mine = parse_user_prices({"openai": {"gpt-x": {"input": 0.001, "output": 0.001}}})
    assert prices.cost_line("openai", "gpt-x", mine) == (
        "Less than $0.01 an hour of talk on gpt-x (about 40 short turns), plus routines."
    )


def test_on_this_computer_is_free_and_says_so() -> None:
    free = "Runs on this computer: no cost, no cap."
    assert prices.cost_line("local", "gemma") == free
    assert prices.cost_line(None) == free


def test_an_unpriced_model_says_what_it_uses_and_asks_for_a_price() -> None:
    assert prices.cost_line("custom:example.com", "m1") == (
        "No price is known for m1 yet: about 40 short turns an hour send 120,000 tokens "
        "and get 6,000 back, plus routines. Add its price per million tokens to see dollars."
    )
    assert prices.cost_line("custom:openrouter.ai", "some/model") == (
        "OpenRouter reports what each call costs, so it depends on the model: about 40 "
        "short turns an hour send 120,000 tokens and get 6,000 back, plus routines."
    )
    assert prices.cost_line("openai", None) == (
        "Depends on the model: about 40 short turns an hour send 120,000 tokens and get "
        "6,000 back, plus routines."
    )


def test_an_unpriced_voice_or_hearing_says_what_it_uses() -> None:
    assert prices.cost_line("openai-voice:speech.example.com") == (
        "No price is known for this voice yet: about 40 sentences an hour come to 3,200 "
        "characters. Add its rate to see dollars."
    )


def test_model_names_read_as_people_say_them() -> None:
    assert prices.model_words("claude-sonnet-5-5") == "Sonnet 5.5"
    assert prices.model_words("claude-opus-4-5-20251101") == "Opus 4.5"
    assert prices.model_words("claude-fable-5") == "Fable 5"
    assert prices.model_words("gpt-6-sol") == "gpt-6-sol"


def test_each_provider_links_its_pricing_page() -> None:
    assert prices.pricing_url("anthropic") == "https://www.anthropic.com/pricing#api"
    assert prices.pricing_url("openai") == "https://developers.openai.com/api/docs/pricing"
    assert prices.pricing_url("custom:api.deepseek.com") == (
        "https://api-docs.deepseek.com/quick_start/pricing"
    )
    assert prices.pricing_url("custom:openrouter.ai") == "https://openrouter.ai/models"
    assert prices.pricing_url("elevenlabs") == "https://elevenlabs.io/pricing/api"
    assert prices.pricing_url("deepgram") == "https://deepgram.com/pricing"
    assert prices.pricing_url("custom:example.com") is None
    assert prices.pricing_url("local") is None
    assert prices.pricing_url(None) is None


def test_a_call_openrouter_did_not_cost_is_estimated_at_the_dearest_table_rate() -> None:
    dearest = max(
        max(
            r.input,
            r.output,
            r.cache_read,
            r.cache_write,
            *(
                (
                    r.long_prompt.input,
                    r.long_prompt.output,
                    r.long_prompt.cache_read,
                    r.long_prompt.cache_write,
                )
                if r.long_prompt
                else ()
            ),
        )
        for r in TABLE.values()
        if r.unit == "tokens"
    )
    assert prices.FALLBACK_TOKEN_RATE == dearest
    assert ceiling_rate("custom:openrouter.ai", "tokens") == dearest
    assert estimate("custom:openrouter.ai", "tokens", 1_000_000) == pytest.approx(dearest)
    # Still unpriced on its own: the row says nothing priced it.
    assert price("custom:openrouter.ai", "x/y", Usage(input_tokens=10)) is None
