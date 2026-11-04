"""This month's spend per provider, kept in memory for the spend gate (BRN-7).

The ledger reads this month's `usage` rows once, then counts each new call as it is
recorded. Every row was priced when its call ended, one row per call, so the ledger
only adds up dollars that are already known: it never prices summed usage. A row
with no price counts by its quantity (tokens, characters or seconds), never as $0.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from trillion.zones import month_start

Caller = Literal["conversation", "background"]
Unit = Literal["tokens", "chars", "seconds"]


class UsageSource(Protocol):
    async def spend_since(self, owner_id: str, since_ms: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Row:
    """One recorded call, as the ledger counts it."""

    provider_id: str | None
    caller: Caller
    unit: Unit
    # Tokens, characters or seconds, in `unit`.
    quantity: float
    # None when nothing priced the call.
    cost_usd: float | None
    at_ms: int


@dataclass(frozen=True)
class Share:
    """One caller's part of a provider's month."""

    dollars: float = 0.0
    units: float = 0


@dataclass(frozen=True)
class Spent:
    """A provider's month so far: dollars over priced calls, and the quantity of the
    calls nothing priced, in `unit` (None when there were none yet)."""

    dollars: float = 0.0
    units: float = 0
    unit: Unit | None = None
    by_caller: Mapping[Caller, Share] = field(default_factory=dict)


@dataclass
class _Tally:
    dollars: float = 0.0
    units: float = 0
    unit: Unit | None = None
    by_caller: dict[Caller, Share] = field(default_factory=dict)

    def count(self, caller: Caller, unit: Unit, cost: float | None, quantity: float) -> None:
        share = self.by_caller.get(caller, Share())
        if cost is not None:
            self.dollars += cost
            share = Share(dollars=share.dollars + cost, units=share.units)
        elif quantity:
            self.units += quantity
            self.unit = unit
            share = Share(dollars=share.dollars, units=share.units + quantity)
        self.by_caller[caller] = share


class Ledger:
    def __init__(
        self,
        store: UsageSource,
        clock: Callable[[], int],
        tz: str | Callable[[], str],
        *,
        owner_id: str = "local",
    ) -> None:
        self._store = store
        self._clock = clock
        # The user's timezone, or "" for this machine's local time. A callable is read
        # each time, so a timezone set later in setup is the one used.
        self._tz = tz
        self._owner = owner_id
        self._month: str | None = None
        self._since = 0
        self._tallies: dict[str | None, _Tally] = {}

    def _month_now(self) -> tuple[str, int]:
        zone = self._tz() if callable(self._tz) else self._tz
        return month_start(self._clock(), zone)

    def _roll(self) -> None:
        """A new month starts at zero: every row in it is counted by `add`."""
        month, since = self._month_now()
        if month != self._month:
            self._month, self._since = month, since
            self._tallies = {}

    async def load(self) -> None:
        """Read this month's rows from midnight on the 1st, in the user's timezone."""
        self._month, self._since = self._month_now()
        tallies: dict[str | None, _Tally] = {}
        for r in await self._store.spend_since(self._owner, self._since):
            tally = tallies.setdefault(r["provider_id"], _Tally())
            tally.count(r["caller"], r["units"], r["cost_usd"], 0)
            tally.count(r["caller"], r["units"], None, r["unpriced"])
        self._tallies = tallies

    def add(self, row: Row) -> None:
        """Count a call just recorded. One from before this month counts for nothing."""
        self._roll()
        if row.at_ms < self._since:
            return
        tally = self._tallies.setdefault(row.provider_id, _Tally())
        tally.count(row.caller, row.unit, row.cost_usd, row.quantity)

    def spent(self, provider_id: str | None) -> Spent:
        self._roll()
        tally = self._tallies.get(provider_id)
        if tally is None:
            return Spent()
        return Spent(
            dollars=tally.dollars,
            units=tally.units,
            unit=tally.unit,
            by_caller=dict(tally.by_caller),
        )
