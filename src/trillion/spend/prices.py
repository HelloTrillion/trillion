"""What a paid call costs: one table, plus what the provider and the user say (BRN-7).

A price comes from, in this order:

1. the user's own price for that provider and model (preference `spend_prices`);
2. the cost the provider reported for the call (OpenRouter's `usage.cost`);
3. the built-in table below;
4. nothing for a local model, which is free.

Anything else is unpriced: None, never $0. Its caps then count units instead.

Rates are keyed by provider id and model. A model of "*" stands for any model of
that provider. Every built-in rate records the day it was checked and the page it
came from; a model whose rate could not be confirmed is left out, not guessed.
"""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

from trillion.brain.types import Pricing, Usage
from trillion.spend.types import Unit

__all__ = [
    "ANY",
    "TABLE",
    "Rate",
    "Unit",
    "UserPrices",
    "ceiling_rate",
    "estimate",
    "hearing_provider_id",
    "parse_user_prices",
    "price",
    "provider_id",
    "rate_for",
    "voice_provider_id",
]

_UNITS: tuple[Unit, ...] = get_args(Unit)
ANY = "*"


@dataclass(frozen=True, kw_only=True)
class Rate:
    """US dollars. Token rates are per million tokens. `per_unit` is per thousand
    characters for "chars", and per minute for "seconds"."""

    unit: Unit = "tokens"
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    per_unit: float = 0.0
    # A request whose prompt (input + cache reads + cache writes) is over this many
    # tokens is billed whole at `long_prompt`.
    long_prompt_over: int = 0
    long_prompt: Rate | None = None
    checked: date | None = None
    source: str = ""

    def pricing(self) -> Pricing | None:
        """The token rate as the model providers report it, or None for a per-unit rate."""
        if self.unit != "tokens":
            return None
        return Pricing(
            input=self.input,
            output=self.output,
            cache_read=self.cache_read,
            cache_write=self.cache_write,
            long_prompt_over=self.long_prompt_over if self.long_prompt else 0,
            long_prompt=self.long_prompt.pricing() if self.long_prompt else None,
        )

    def cost(self, usage: Usage | float) -> float | None:
        """Tokens are priced from a Usage; characters and seconds from a count. The
        wrong kind for this rate is unpriced."""
        if isinstance(usage, Usage):
            pricing = self.pricing()
            return pricing.cost(usage) if pricing else None
        if self.unit == "chars":
            return usage / 1_000 * self.per_unit
        if self.unit == "seconds":
            return usage / 60 * self.per_unit
        return None


UserPrices = Mapping[tuple[str, str], Rate]

# ── The table ────────────────────────────────────────────────────────────

_CHECKED = date(2026, 11, 4)

# Anthropic: moved unchanged from brain/providers/anthropic.py, where it was checked
# against Anthropic's published first-party rates on 2026-10-22. Cache writes bill at
# 1.25x input.
_ANTHROPIC_SOURCE = "https://www.anthropic.com/pricing#api"
_ANTHROPIC_CHECKED = date(2026, 10, 22)
_ANTHROPIC_LONG_OVER = 100_000


def _anthropic(i: float, o: float, cr: float, long: Rate | None = None) -> Rate:
    return Rate(
        input=i,
        output=o,
        cache_read=cr,
        cache_write=i * 1.25,
        long_prompt_over=_ANTHROPIC_LONG_OVER if long else 0,
        long_prompt=long,
        checked=_ANTHROPIC_CHECKED,
        source=_ANTHROPIC_SOURCE,
    )


_ANTHROPIC: dict[str, Rate] = {
    "claude-fable-5-1": _anthropic(10.0, 50.0, 0.25),
    "claude-fable-5": _anthropic(10.0, 50.0, 1.0),
    "claude-opus-5-5": _anthropic(4.0, 20.0, 0.20),
    "claude-opus-5": _anthropic(5.0, 25.0, 0.50),
    "claude-opus-4-8": _anthropic(5.0, 25.0, 0.50),
    "claude-opus-4-7": _anthropic(5.0, 25.0, 0.50),
    "claude-opus-4-6": _anthropic(5.0, 25.0, 0.50),
    "claude-opus-4-5": _anthropic(5.0, 25.0, 0.50),
    "claude-sonnet-5-5": _anthropic(2.0, 10.0, 0.10),
    "claude-sonnet-5": _anthropic(2.0, 10.0, 0.20),
    "claude-sonnet-4-6": _anthropic(3.0, 15.0, 0.30),
    "claude-haiku-5-5": _anthropic(0.10, 0.50, 0.01, long=_anthropic(0.50, 2.50, 0.05)),
    "claude-haiku-4-5": _anthropic(1.0, 5.0, 0.10),
}

# OpenAI: the Standard tier. Cache writes are the 30-minute write rate where the page
# lists one, kept for completeness: the compatible provider reports no writes today.
# Long context is more than 272K input tokens, for the models that list it.
# Pro models are left out: the page gives them no cached-input rate.
_OPENAI_SOURCE = "https://developers.openai.com/api/docs/pricing"
_OPENAI_LONG_OVER = 272_000


def _openai(
    i: float,
    o: float,
    cr: float,
    cw: float = 0.0,
    long: tuple[float, float, float] | tuple[float, float, float, float] | None = None,
) -> Rate:
    return Rate(
        input=i,
        output=o,
        cache_read=cr,
        cache_write=cw,
        long_prompt_over=_OPENAI_LONG_OVER if long else 0,
        long_prompt=_openai(*long) if long else None,
        checked=_CHECKED,
        source=_OPENAI_SOURCE,
    )


_OPENAI: dict[str, Rate] = {
    "gpt-6-astra": _openai(10.0, 50.0, 1.0, 12.50, long=(20.0, 75.0, 2.0, 25.0)),
    "gpt-6.1-sol": _openai(2.0, 10.0, 0.10, 2.50, long=(4.0, 15.0, 0.20, 5.0)),
    "gpt-6.1-terra": _openai(1.0, 5.0, 0.05, 1.25, long=(2.0, 7.50, 0.10, 2.50)),
    "gpt-6-sol": _openai(2.0, 10.0, 0.20, 2.50, long=(4.0, 15.0, 0.40, 5.0)),
    "gpt-6-luna": _openai(0.10, 0.50, 0.01, 0.125, long=(0.20, 0.75, 0.02, 0.25)),
    "gpt-5.6-sol": _openai(4.0, 20.0, 0.40, 5.0, long=(8.0, 30.0, 0.80, 10.0)),
    "gpt-5.6-terra": _openai(2.0, 12.0, 0.20, 2.50, long=(4.0, 18.0, 0.40, 5.0)),
    "gpt-5.6-luna": _openai(0.20, 1.20, 0.02, 0.25, long=(0.40, 1.80, 0.04, 0.50)),
    "gpt-5.5": _openai(5.0, 30.0, 0.50, long=(10.0, 45.0, 1.0)),
    "gpt-5.4": _openai(2.50, 15.0, 0.25, long=(5.0, 22.50, 0.50)),
    "gpt-5.4-mini": _openai(0.75, 4.50, 0.075),
    "gpt-5.4-nano": _openai(0.20, 1.25, 0.02),
    "gpt-5.2": _openai(1.75, 14.0, 0.175),
    "gpt-5.1": _openai(1.25, 10.0, 0.125),
    "gpt-5": _openai(1.25, 10.0, 0.125),
    "gpt-5-mini": _openai(0.25, 2.0, 0.025),
    "gpt-5-nano": _openai(0.05, 0.40, 0.005),
    "gpt-4.1": _openai(2.0, 8.0, 0.50),
    "gpt-4.1-mini": _openai(0.40, 1.60, 0.10),
    "gpt-4o": _openai(2.50, 10.0, 1.25),
    "gpt-4o-mini": _openai(0.15, 0.60, 0.075),
}

# DeepSeek bills half these rates off-peak. The peak rate is used, so a cap is never
# reached later than the bill says; off-peak use is counted at up to twice its cost.
# "deepseek-v4-flash" is an older name that DeepSeek still accepts and bills as Flash.
_DEEPSEEK_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"


def _deepseek(miss: float, output: float, hit: float) -> Rate:
    return Rate(
        input=miss, output=output, cache_read=hit, checked=_CHECKED, source=_DEEPSEEK_SOURCE
    )


_DEEPSEEK: dict[str, Rate] = {
    "deepseek-flash": _deepseek(0.30, 1.20, 0.006),
    "deepseek-v4-flash": _deepseek(0.30, 1.20, 0.006),
    "deepseek-v4-pro": _deepseek(1.32, 3.96, 0.044),
}

TABLE: dict[tuple[str, str], Rate] = {
    **{("anthropic", m): r for m, r in _ANTHROPIC.items()},
    **{("openai", m): r for m, r in _OPENAI.items()},
    **{("custom:api.deepseek.com", m): r for m, r in _DEEPSEEK.items()},
    # Pay-as-you-go. Flash v2.5 is the model voice/providers/elevenlabs.py uses.
    ("elevenlabs", "eleven_flash_v2_5"): Rate(
        unit="chars", per_unit=0.04, checked=_CHECKED, source="https://elevenlabs.io/pricing/api"
    ),
    # Pay-as-you-go streaming, Flux: the models voice/providers/deepgram.py uses.
    ("deepgram", "flux-general-en"): Rate(
        unit="seconds", per_unit=0.0077, checked=_CHECKED, source="https://deepgram.com/pricing"
    ),
    ("deepgram", "flux-general-multi"): Rate(
        unit="seconds", per_unit=0.0078, checked=_CHECKED, source="https://deepgram.com/pricing"
    ),
}

# ── Looking a price up ───────────────────────────────────────────────────

# An Anthropic dated id ("-20251001") prices like its alias. Only Anthropic's: an
# OpenAI snapshot can bill differently from its alias, so it is unpriced unless listed.
_ANTHROPIC_DATED = re.compile(r"-\d{8}$")


def rate_for(rates: UserPrices | None, provider_id: str, model: str) -> Rate | None:
    """The rate for this model: its own, its alias's, then the provider's "*"."""
    if not rates:
        return None
    alias = _ANTHROPIC_DATED.sub("", model) if provider_id == "anthropic" else model
    for key in dict.fromkeys((model, alias, ANY)):
        if (rate := rates.get((provider_id, key))) is not None:
            return rate
    return None


def price(
    provider_id: str,
    model: str,
    usage: Usage | float,
    user_prices: UserPrices | None = None,
) -> float | None:
    """US dollars for one call, or None when nothing prices it.

    `usage` is the model stream's Usage for a token-priced call, or a count of
    characters spoken or seconds heard for voice and hearing."""
    if (mine := rate_for(user_prices, provider_id, model)) is not None:
        if (cost := mine.cost(usage)) is not None:
            return cost
    if isinstance(usage, Usage) and usage.reported_cost is not None:
        return usage.reported_cost
    if (built_in := rate_for(TABLE, provider_id, model)) is not None:
        if (cost := built_in.cost(usage)) is not None:
            return cost
    if provider_id == "local":
        return 0.0
    return None


# ── Estimating what nothing priced ───────────────────────────────────────


def _highest(rate: Rate) -> float:
    """A token rate's dearest part, per million tokens, long prompts included."""
    parts = [rate.input, rate.output, rate.cache_read, rate.cache_write]
    if rate.long_prompt is not None:
        parts.append(_highest(rate.long_prompt))
    return max(parts)


def ceiling_rate(provider_id: str, unit: Unit) -> float | None:
    """The dearest rate in the table for this provider and unit: per million tokens
    (the highest of any model's rates, which is its output rate in practice), per
    thousand characters, or per minute. It prices a call nothing priced, such as a
    model newer than the table, so that call is never counted for less than it may
    cost. None when the table has no rate in that unit for the provider."""
    rates = [
        _highest(r) if unit == "tokens" else r.per_unit
        for (pid, _model), r in TABLE.items()
        if pid == provider_id and r.unit == unit
    ]
    return max(rates) if rates else None


def estimate(provider_id: str, unit: Unit, quantity: float) -> float | None:
    """US dollars for `quantity` units nothing priced, at `ceiling_rate`, or None."""
    rate = ceiling_rate(provider_id, unit)
    if rate is None:
        return None
    per = {"tokens": 1_000_000, "chars": 1_000, "seconds": 60}[unit]
    return quantity / per * rate


# ── User prices ──────────────────────────────────────────────────────────


def _amount(raw: Mapping[str, Any], key: str, *, required: bool) -> float | None:
    """A non-negative finite number, 0.0 when optional and absent, None when bad."""
    if key not in raw:
        return None if required else 0.0
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _user_rate(raw: Any) -> Rate | None:
    if not isinstance(raw, dict):
        return None
    unit = raw.get("unit", "tokens")
    if unit not in _UNITS:
        return None
    if unit != "tokens":
        per_unit = _amount(raw, "per_unit", required=True)
        return None if per_unit is None else Rate(unit=unit, per_unit=per_unit, source="user")
    amounts = {
        "input": _amount(raw, "input", required=True),
        "output": _amount(raw, "output", required=True),
        "cache_read": _amount(raw, "cache_read", required=False),
        "cache_write": _amount(raw, "cache_write", required=False),
    }
    if any(v is None for v in amounts.values()):
        return None
    return Rate(
        input=amounts["input"] or 0.0,
        output=amounts["output"] or 0.0,
        cache_read=amounts["cache_read"] or 0.0,
        cache_write=amounts["cache_write"] or 0.0,
        source="user",
    )


def parse_user_prices(raw: Any) -> dict[tuple[str, str], Rate]:
    """The preference `spend_prices`, `{provider_id: {model: rate}}`, as rates.

    A token rate needs `input` and `output` (per million tokens), and may give
    `cache_read` and `cache_write`. A rate with `unit` "chars" or "seconds" needs
    `per_unit` (per thousand characters, or per minute). Anything malformed is
    skipped, so one bad entry never loses the others."""
    out: dict[tuple[str, str], Rate] = {}
    if not isinstance(raw, dict):
        return out
    for pid, models in raw.items():
        if not isinstance(pid, str) or not isinstance(models, dict):
            continue
        for model, entry in models.items():
            if isinstance(model, str) and (rate := _user_rate(entry)) is not None:
                out[(pid, model)] = rate
    return out


# ── Provider ids ─────────────────────────────────────────────────────────


def _host(kind: Literal["custom"], base_url: str | None) -> str | None:
    """The host of an address that passes the same check a saved setting does."""
    # Imported here: brain.config builds the providers, and the providers read this table.
    from trillion.brain.config import ConfigError, endpoint

    if not base_url:
        return None
    try:
        checked = endpoint(kind, base_url)
    except ConfigError:
        return None
    return urlsplit(checked).hostname if checked else None


# OpenAI's own API, however it was entered: billed as the "openai" kind is.
_OPENAI_HOST = "api.openai.com"


def _on_this_computer(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def provider_id(kind: str, base_url: str | None) -> str | None:
    """Which cap a model counts against: "anthropic", "openai", "local", or
    "custom:<host>" by the host of its checked base URL. A custom address on this
    computer (localhost, 127.0.0.0/8, ::1, *.localhost) is "local", and OpenAI's own
    address is "openai", whichever kind it was entered as. None for a custom model
    whose address does not pass the check, or a kind this version does not know."""
    if kind in ("anthropic", "openai", "local"):
        return kind
    if kind == "custom" and (host := _host("custom", base_url)):
        if _on_this_computer(host):
            return "local"
        if host == _OPENAI_HOST:
            return "openai"
        return f"custom:{host}"
    return None


def voice_provider_id(kind: str, base_url: str | None) -> str | None:
    """Which cap a voice (a `voice.catalog.SPEECH` kind) counts against. The
    system voice, and a speech server on this computer, are free and never capped:
    None."""
    if kind == "elevenlabs":
        return "elevenlabs"
    if kind == "custom" and (host := _host("custom", base_url)):
        return None if _on_this_computer(host) else f"openai-voice:{host}"
    return None


def hearing_provider_id(kind: str) -> str | None:
    """Which cap hearing (a `voice.catalog.LISTENING` kind) counts against. Local
    Whisper runs on this computer and is never capped: None."""
    return "deepgram" if kind == "deepgram" else None
