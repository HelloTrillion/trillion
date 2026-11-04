"""Caps per provider, and the gate that says where each one stands this month (BRN-7).

States, at the thresholds the spend caps spec sets, each reached *at* its number:
- `warned` at `warn_pct` percent of the soft cap;
- `over_soft` at the soft cap;
- `capped` at the hard cap, where `check_model` and `check_voice` refuse.

**Dollars and units.** Every set of caps says its unit.
- **Dollar caps** (the usual) compare the month's priced dollars plus an estimate for
  its unpriced units: those calls priced at the provider's dearest rate in the price
  table (`prices.ceiling_rate`), so a model newer than the table is never counted for
  less than it may cost. A provider with no rate in the table cannot be estimated:
  its unpriced units count toward no cap, and `unestimated` reports them so Settings
  can ask for a price.
- **Unit caps** (tokens, characters or seconds), for a provider nothing prices,
  compare only the unpriced units in that unit. Dollars, and units of another kind,
  are ignored, and `unestimated` reports units of another kind.

**Where caps come from.** The preference `spend_caps`, `{provider_id: {soft, hard,
warn_pct}}`, read by `reload_caps` at startup and again after every write to it.
- `null` for `soft` or `hard` means that cap is removed. `unit` is "dollars" when
  left out.
- A provider with no entry has no caps: an existing install is given the defaults
  once, on upgrade, and a new key is saved with them.
- A malformed entry, or a malformed preference, counts as `default_for` that
  provider, so a typo never removes protection.

`local`, and a provider id of None (the system voice, local Whisper, an address that
does not pass the check), are never capped.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from trillion.brain.types import ProviderError
from trillion.spend import prices
from trillion.spend.caller import current
from trillion.spend.ledger import Ledger
from trillion.spend.types import Caller, Unit
from trillion.voice.types import VoiceError

log = logging.getLogger(__name__)

PREF = "spend_caps"

State = Literal["ok", "warned", "over_soft", "capped"]

# What a cap is counted in: dollars, or the unit of the calls nothing priced.
CapUnit = Literal["dollars", "tokens", "chars", "seconds"]
_CAP_UNITS: tuple[CapUnit, ...] = get_args(CapUnit)

# Never capped: it runs on this computer.
UNCAPPED = frozenset({"local"})


@dataclass(frozen=True)
class Caps:
    """A provider's monthly caps, in `unit`. None means that cap is removed."""

    soft: float | None
    hard: float | None
    warn_pct: int = 80
    unit: CapUnit = "dollars"


DEFAULT = Caps(20.0, 50.0, 80)
# For a provider the price table has no rate for: roughly $20 and $50 at about $4 per
# million tokens.
DEFAULT_TOKENS = Caps(5_000_000, 12_000_000, 80, "tokens")
# For a voice with no rate in the table: roughly $20 and $50 at about $0.05 per
# thousand characters.
DEFAULT_CHARS = Caps(400_000, 1_000_000, 80, "chars")
# For hearing with no rate in the table: 600 and 1500 minutes, roughly $20 and $50 at
# about $0.033 a minute.
DEFAULT_SECONDS = Caps(600 * 60, 1500 * 60, 80, "seconds")
NO_CAPS = Caps(None, None, 80)

# What each kind of provider id counts when nothing prices it.
_VOICE_IDS = ("elevenlabs", "openai-voice:")
_HEARING_IDS = ("deepgram",)


def _unit_of(provider_id: str) -> Unit:
    if provider_id.startswith(_VOICE_IDS):
        return "chars"
    if provider_id.startswith(_HEARING_IDS):
        return "seconds"
    return "tokens"


def default_for(provider_id: str) -> Caps:
    """The caps a provider starts with: `DEFAULT` in dollars when the price table can
    price it, else the default in its own unit, so it is still capped."""
    unit = _unit_of(provider_id)
    if prices.ceiling_rate(provider_id, unit) is not None:
        return DEFAULT
    return {"tokens": DEFAULT_TOKENS, "chars": DEFAULT_CHARS, "seconds": DEFAULT_SECONDS}[unit]


# ── The preference ───────────────────────────────────────────────────────


def _amount(raw: Mapping[str, Any], key: str) -> tuple[bool, float | None]:
    """(ok, value): a cap must be given, as null or a non-negative finite number."""
    if key not in raw:
        return False, None
    value = raw[key]
    if value is None:
        return True, None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False, None
    if not math.isfinite(value) or value < 0:
        return False, None
    return True, float(value)


def _caps(raw: Any) -> Caps | None:
    """One entry, or None when it is malformed."""
    if not isinstance(raw, dict):
        return None
    soft_ok, soft = _amount(raw, "soft")
    hard_ok, hard = _amount(raw, "hard")
    if not (soft_ok and hard_ok):
        return None
    if soft is not None and hard is not None and hard < soft:
        return None
    pct = raw.get("warn_pct", DEFAULT.warn_pct)
    if isinstance(pct, bool) or not isinstance(pct, int) or not 1 <= pct <= 100:
        return None
    unit = raw.get("unit", "dollars")
    if unit not in _CAP_UNITS:
        return None
    return Caps(soft, hard, pct, unit)


def parse_caps(raw: Any) -> dict[str, Caps]:
    """The preference `spend_caps` as caps per provider id. A malformed entry is
    `default_for` that provider, never dropped; a key that is not a provider id is
    skipped."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Caps] = {}
    for pid, entry in raw.items():
        if not isinstance(pid, str):
            continue
        caps = _caps(entry)
        if caps is None:
            log.warning("spend caps for %s are malformed: using the defaults", pid)
            caps = default_for(pid)
        out[pid] = caps
    return out


# ── Refusals ─────────────────────────────────────────────────────────────


class SpendCapped(Exception):
    """A call refused at a provider's hard cap, before anything was sent. Raised as
    `ModelCapped` or `VoiceCapped`, so each wrapper's callers catch it as the error
    they already handle, with kind `capped`."""

    kind: str

    def __init__(self, provider_id: str, caller: Caller, cap: float, unit: CapUnit) -> None:
        if type(self) is SpendCapped:
            raise TypeError("raise ModelCapped or VoiceCapped, not SpendCapped")
        # The next class in line is ProviderError or VoiceError, which takes a kind.
        super().__init__("capped", f"{provider_id} is at its {cap:g} {unit} cap this month")
        self.kind = "capped"
        self.provider_id = provider_id
        self.caller = caller
        self.cap = cap
        self.unit = unit


class ModelCapped(SpendCapped, ProviderError):
    """A model call refused at the hard cap."""


class VoiceCapped(SpendCapped, VoiceError):
    """A voice or hearing call refused at the hard cap."""


# ── The gate ─────────────────────────────────────────────────────────────


def _reached(spent: float, limit: float) -> bool:
    """At or over a limit. Spend is a sum of many small floats (5000 calls at $0.01 add
    up to a hair under $50), so a hair under counts as at."""
    return spent >= limit or math.isclose(spent, limit, rel_tol=1e-9, abs_tol=1e-9)


def _state(spent: float, caps: Caps) -> State:
    if caps.hard is not None and _reached(spent, caps.hard):
        return "capped"
    if caps.soft is not None:
        if _reached(spent, caps.soft):
            return "over_soft"
        if _reached(spent, caps.soft * caps.warn_pct / 100):
            return "warned"
    return "ok"


class Gate:
    def __init__(self, ledger: Ledger, caps_source: Callable[[], Awaitable[Any]]) -> None:
        """`caps_source` reads the preference `spend_caps` as stored."""
        self._ledger = ledger
        self._source = caps_source
        self._caps: dict[str, Caps] | None = None
        # The preference itself is malformed: every provider gets its defaults.
        self._malformed = False
        self._loading = asyncio.Lock()

    async def reload_caps(self) -> None:
        """Read the caps again. Call it at startup and after every write to them."""
        raw = await self._source()
        malformed = raw is not None and not isinstance(raw, dict)
        if malformed:
            log.warning("the spend caps preference is malformed: using the defaults")
        self._caps = parse_caps(raw)
        self._malformed = malformed

    def caps(self, provider_id: str) -> Caps:
        if self._caps is None:
            # Never fail open: no answer until the caps have been read.
            raise RuntimeError("the spend caps are not loaded yet")
        if (caps := self._caps.get(provider_id)) is not None:
            return caps
        return default_for(provider_id) if self._malformed else NO_CAPS

    def spent_dollars(self, provider_id: str) -> float:
        """This month's priced dollars, plus the estimate for what nothing priced."""
        spent = self._ledger.spent(provider_id)
        extra = None
        if spent.units and spent.unit is not None:
            extra = prices.estimate(provider_id, spent.unit, spent.units)
        return spent.dollars + (extra or 0.0)

    def _counted(self, provider_id: str, caps: Caps) -> float:
        """The month so far, in the caps' unit."""
        if caps.unit == "dollars":
            return self.spent_dollars(provider_id)
        spent = self._ledger.spent(provider_id)
        return spent.units if spent.unit == caps.unit else 0.0

    def counted(self, provider_id: str) -> float:
        """The month so far, in the unit of this provider's caps."""
        return self._counted(provider_id, self.caps(provider_id))

    def unestimated(self, provider_id: str | None) -> tuple[float, Unit] | None:
        """Unpriced units this month that count toward none of this provider's caps:
        under dollar caps, those with no rate in the table to estimate from; under unit
        caps, those of another unit. None when everything is counted."""
        if provider_id is None or provider_id in UNCAPPED:
            return None
        caps = self.caps(provider_id)
        spent = self._ledger.spent(provider_id)
        if not spent.units or spent.unit is None:
            return None
        if caps.unit == "dollars":
            if prices.ceiling_rate(provider_id, spent.unit) is not None:
                return None
        elif caps.unit == spent.unit:
            return None
        return spent.units, spent.unit

    def state(self, provider_id: str | None) -> State:
        """Where a provider stands this month. Reads only what is in memory."""
        if provider_id is None or provider_id in UNCAPPED:
            return "ok"
        caps = self.caps(provider_id)
        return _state(self._counted(provider_id, caps), caps)

    async def refresh(self) -> None:
        """Count from the right start of the month again if the timezone moved it.

        A reload that fails is logged and leaves `stale` set, so the next check tries
        again; meanwhile the counts already held answer."""
        self._ledger.roll()
        if not self._ledger.stale:
            return
        async with self._loading:
            if not self._ledger.stale:
                return
            try:
                await self._ledger.load()
            except Exception as e:
                # The kind of failure only: never an amount.
                log.warning(
                    "the spend ledger could not reload (%s): using the counts it has",
                    type(e).__name__,
                )

    async def check_model(self, provider_id: str | None) -> None:
        """Before a model call: raises `ModelCapped` at the hard cap."""
        await self._check(provider_id, ModelCapped)

    async def check_voice(self, provider_id: str | None) -> None:
        """Before a voice or hearing call: raises `VoiceCapped` at the hard cap."""
        await self._check(provider_id, VoiceCapped)

    async def _check(self, provider_id: str | None, error: type[SpendCapped]) -> None:
        if provider_id is None or provider_id in UNCAPPED:
            return
        self.caps(provider_id)
        await self.refresh()
        caps = self.caps(provider_id)
        # Only a hard cap refuses.
        if caps.hard is None or self.state(provider_id) != "capped":
            return
        raise error(provider_id, current().caller, caps.hard, caps.unit)
