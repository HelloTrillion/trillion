"""Caps per provider, and the gate that says where each one stands this month (BRN-7).

States, at the thresholds the spend caps spec sets, each reached *at* its number:
- `warned` at `warn_pct` percent of the soft cap;
- `over_soft` at the soft cap;
- `capped` at the hard cap, where `check_model` and `check_voice` refuse.

**Dollars and units.** A provider's month can hold priced calls (dollars) and calls
nothing priced (tokens, characters or seconds), for example one model in the price
table and another not. Dollars are compared with the caps. When the month also has
unpriced units, those are compared with the same cap numbers read as units, and the
worse of the two states wins. So unpriced use is never dropped, and pricing one model
never lifts the cap on the rest.

**Where caps come from.** The preference `spend_caps`, `{provider_id: {soft, hard,
warn_pct}}`, read by `reload_caps` at startup and again after every write to it.
- `null` for `soft` or `hard` means that cap is removed.
- A provider with no entry has no caps: an existing install is given the defaults
  once, on upgrade, and a new key is saved with them.
- A malformed entry, or a malformed preference, counts as `DEFAULT`, so a typo never
  removes protection.

`local`, and a provider id of None (the system voice, local Whisper, an address that
does not pass the check), are never capped.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from trillion.brain.types import ProviderError
from trillion.spend.caller import current
from trillion.spend.ledger import Ledger
from trillion.spend.types import Caller, Unit
from trillion.voice.types import VoiceError

log = logging.getLogger(__name__)

PREF = "spend_caps"

State = Literal["ok", "warned", "over_soft", "capped"]
_ORDER: tuple[State, ...] = ("ok", "warned", "over_soft", "capped")

# What a cap is counted in: dollars, or the unit of the calls nothing priced.
CapUnit = Literal["dollars"] | Unit

# Never capped: it runs on this computer.
UNCAPPED = frozenset({"local"})


@dataclass(frozen=True)
class Caps:
    """A provider's monthly caps. None means that cap is removed. Read as dollars
    against priced spend, and as units against unpriced spend."""

    soft: float | None
    hard: float | None
    warn_pct: int = 80


DEFAULT = Caps(20.0, 50.0, 80)
NO_CAPS = Caps(None, None, 80)


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
    return Caps(soft, hard, pct)


def parse_caps(raw: Any) -> dict[str, Caps]:
    """The preference `spend_caps` as caps per provider id. A malformed entry is
    `DEFAULT`, never dropped; a key that is not a provider id is skipped."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Caps] = {}
    for pid, entry in raw.items():
        if not isinstance(pid, str):
            continue
        caps = _caps(entry)
        if caps is None:
            log.warning("spend caps for %s are malformed: using the defaults", pid)
            caps = DEFAULT
        out[pid] = caps
    return out


# ── Refusals ─────────────────────────────────────────────────────────────


class SpendCapped(Exception):
    """A call refused at a provider's hard cap, before anything was sent. Raised as
    `ModelCapped` or `VoiceCapped`, so each wrapper's callers catch it as the error
    they already handle, with kind `capped`."""

    kind: str

    def __init__(self, provider_id: str, caller: Caller, cap: float, unit: CapUnit) -> None:
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


def _state(spent: float, caps: Caps) -> State:
    if caps.hard is not None and spent >= caps.hard:
        return "capped"
    if caps.soft is not None:
        if spent >= caps.soft:
            return "over_soft"
        # In whole numbers on both sides, so 80% of $20 is exactly $16.
        if spent * 100 >= caps.soft * caps.warn_pct:
            return "warned"
    return "ok"


class Gate:
    def __init__(self, ledger: Ledger, caps_source: Callable[[], Awaitable[Any]]) -> None:
        """`caps_source` reads the preference `spend_caps` as stored."""
        self._ledger = ledger
        self._source = caps_source
        self._caps: dict[str, Caps] | None = None
        # Caps for every provider when the preference itself is malformed.
        self._fallback = NO_CAPS
        self._loading = asyncio.Lock()

    async def reload_caps(self) -> None:
        """Read the caps again. Call it at startup and after every write to them."""
        raw = await self._source()
        malformed = raw is not None and not isinstance(raw, dict)
        if malformed:
            log.warning("the spend caps preference is malformed: using the defaults")
        self._caps = parse_caps(raw)
        self._fallback = DEFAULT if malformed else NO_CAPS

    def caps(self, provider_id: str) -> Caps:
        if self._caps is None:
            # Never fail open: no answer until the caps have been read.
            raise RuntimeError("the spend caps are not loaded yet")
        return self._caps.get(provider_id, self._fallback)

    def state(self, provider_id: str | None) -> State:
        """Where a provider stands this month. Reads only what is in memory."""
        if provider_id is None or provider_id in UNCAPPED:
            return "ok"
        caps = self.caps(provider_id)
        spent = self._ledger.spent(provider_id)
        by_dollars = _state(spent.dollars, caps)
        by_units = _state(spent.units, caps) if spent.units else "ok"
        return max(by_dollars, by_units, key=_ORDER.index)

    async def refresh(self) -> None:
        """Count from the right start of the month again if the timezone moved it."""
        # Reading the ledger is what notices a moved start.
        self._ledger.spent(None)
        if not self._ledger.stale:
            return
        async with self._loading:
            if self._ledger.stale:
                await self._ledger.load()

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
        spent = self._ledger.spent(provider_id)
        # The dollar cap when dollars reached it, else the units that did.
        if spent.dollars >= caps.hard or spent.unit is None:
            unit: CapUnit = "dollars"
        else:
            unit = spent.unit
        raise error(provider_id, current().caller, caps.hard, unit)
