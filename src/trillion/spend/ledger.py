"""This month's spend per provider, kept in memory for the spend gate (BRN-7).

The ledger reads this month's `usage` rows once, then counts each new call as it is
recorded. Every row was priced when its call ended, one row per call, so the ledger
only adds up dollars that are already known: it never prices summed usage. A row
with no price counts by its quantity (tokens, characters or seconds), never as $0.

Until it has loaded, the ledger refuses to answer: zeros would let every call
through, and the gate must never fail open.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from trillion.spend.types import Caller, Unit
from trillion.zones import month_start


class UsageSource(Protocol):
    async def spend_since(
        self, owner_id: str, since_ms: int, until_ms: int | None = None
    ) -> list[dict[str, Any]]: ...


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
    """A provider's month so far. `dollars` sums the priced calls and `units` the
    quantity of the calls nothing priced; a provider with both kinds (one model
    priced, another not) has both nonzero. `unit` is the unit of `units` only, None
    while there is no unpriced call."""

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
        self._loaded = False
        self._month: tuple[str, int] | None = None
        self._tallies: dict[str | None, _Tally] = {}
        # While a load reads, the calls counted meanwhile, replayed once it has read.
        # None when no load is reading.
        self._held: list[Row] | None = None
        # The timezone moved this month's start: only a reload counts from the new one.
        self.stale = False

    def _month_now(self) -> tuple[str, int]:
        zone = self._tz() if callable(self._tz) else self._tz
        return month_start(self._clock(), zone)

    def _ready(self) -> None:
        if not self._loaded:
            raise RuntimeError("the spend ledger is not loaded yet")

    def _roll(self) -> None:
        """Keep the month current. A new month starts at zero: every row in it is
        counted by `add`. A new timezone in the same month moves the start, which
        only a reload can count from, so it is marked `stale` and nothing counted
        is dropped meanwhile."""
        now = self._month_now()
        if self._month is None or now == self._month:
            return
        if now[0] != self._month[0]:
            self._tallies = {}
        else:
            self.stale = True
        self._month = now

    async def load(self) -> None:
        """Read this month's rows from midnight on the 1st, in the user's timezone.
        Call it once at startup, and again when `stale`.

        The read stops at the moment it starts, that moment included. A call counted
        while it reads is held: one stamped up to that moment is already in the read
        and is dropped, one stamped after it is counted once the read is done. Until the
        first load finishes, `spent` refuses to answer."""
        if self._held is not None:
            raise RuntimeError("the spend ledger is already loading")
        month = self._month_now()
        cut = self._clock()
        self._held = []
        try:
            rows = await self._store.spend_since(self._owner, month[1], cut)
        except BaseException:
            self._held = None
            raise
        tallies: dict[str | None, _Tally] = {}
        for r in rows:
            tally = tallies.setdefault(r["provider_id"], _Tally())
            tally.count(r["caller"], r["units"], r["cost_usd"], 0)
            tally.count(r["caller"], r["units"], None, r["unpriced"])
        held, self._held = self._held, None
        self._month, self._tallies, self._loaded, self.stale = month, tallies, True, False
        for row in held:
            if row.at_ms > cut:
                self._count(row)

    def _count(self, row: Row) -> None:
        self._roll()
        if self._month is None or row.at_ms < self._month[1]:
            return
        tally = self._tallies.setdefault(row.provider_id, _Tally())
        tally.count(row.caller, row.unit, row.cost_usd, row.quantity)

    def add(self, row: Row) -> None:
        """Count a call just recorded. One from before this month counts for nothing."""
        if self._held is not None:
            self._held.append(row)
            if not self._loaded:
                return
        self._ready()
        # During a reload the old counts still answer `spent`, so they count it too;
        # the new ones get it from the held list.
        self._count(row)

    def spent(self, provider_id: str | None) -> Spent:
        self._ready()
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
