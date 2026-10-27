"""Stripe revenue for a period, added up here rather than by the model.

Stripe's own analytics tool needs a paid plan, and paging raw charges through the
model fails twice over: a page of charges is far past what one result may carry, and
a model cannot add up hundreds of amounts reliably. So this tool pages the charges
itself, through the Stripe server the user already connected, and returns totals.

It exists only while a Stripe server from the catalogue is connected, registered and
taken away with that server's own tools. Its risk is the one the user gave the
Stripe tool it calls, `stripe_api_read`: free when that is free, asking first when
that asks, so it is never a way around the user's choice. What it returns is totals
only, never a charge, a name or an address, and still wrapped as untrusted like any
connector result.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.tool import CATEGORY, Call
from trillion.spine.contract import (
    ASKS_BY_CARD,
    GATED,
    JSON,
    ActionSummary,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

NAME = "stripe_revenue"
CATALOG = "stripe"
READ_TOOL = "stripe_api_read"
ACCOUNTS_TOOL = "list_available_accounts_or_orgs"
PAGE_SIZE = 100
MAX_PAGES = 50
MAX_SECONDS = 60.0
# Past this a bucketed answer is a table nobody reads: a coarser bucket says more.
MAX_BUCKETS = 100

PERIODS = {
    "month_to_date": "month to date",
    "year_to_date": "year to date",
    "last_month": "last month",
    "last_7_days": "the last 7 days",
    "last_30_days": "the last 30 days",
    "this_quarter": "this quarter",
    "custom": "",
}
BY = ("none", "month", "week", "day")
# Currencies Stripe counts in whole units, and those it counts in thousandths, as its
# currency docs list them. ISK, HUF and TWD are not here: Stripe writes their charges
# with two decimals (always 00 for ISK). UGX is on Stripe's zero-decimal list, but its
# docs have also asked for it as two decimals for backwards compatibility; if Stripe's
# charges show UGX amounts 100 times too large, it belongs with ISK instead.
ZERO_DECIMAL = frozenset("bif clp djf gnf jpy kmf krw mga pyg rwf ugx vnd vuv xaf xof xpf".split())
THREE_DECIMAL = frozenset("bhd jod kwd omr tnd".split())
NET_MEANS = (
    "Refunds are those made on this period's charges; disputes and Stripe fees are not deducted."
)

_ACCOUNT = re.compile(r"\bacct_[A-Za-z0-9]+\b")


class _Problem(ValueError):
    """Something the user or the model can fix. The message is safe to show."""


# ── Periods ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Span:
    """Local dates, both included."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _month_start(d: date, back: int = 0) -> date:
    months = d.year * 12 + d.month - 1 - back
    return date(months // 12, months % 12 + 1, 1)


def _month_end(d: date) -> date:
    return _month_start(d, -1) - timedelta(days=1)


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _same_way_in(start: date, today: date, earlier: date, earlier_end: date) -> Span:
    """As far into `earlier`'s period as today is into this one, never past its end:
    October 1 to 27 compares with September 1 to 27, March 31 with February's end."""
    return Span(earlier, min(earlier + (today - start), earlier_end))


def _before(span: Span) -> Span:
    """The equal stretch just before."""
    return Span(span.start - timedelta(days=span.days), span.start - timedelta(days=1))


# Stripe began in 2010; a year past next is a typo, and far years overflow the maths.
FIRST_YEAR = 2010


def _day(value: Any, what: str, today: date) -> date:
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        raise _Problem(f"{what} must be a date as YYYY-MM-DD") from None
    if not FIRST_YEAR <= d.year <= today.year + 1:
        raise _Problem(f"{what} must be between {FIRST_YEAR} and {today.year + 1}")
    return d


def spans(period: str, today: date, start: Any = None, end: Any = None) -> tuple[Span, Span]:
    """The period asked for and the one to compare it with, in local dates. A period
    still running ends today and compares with the same stretch of the one before."""
    if period == "month_to_date":
        first = _month_start(today)
        prev = _month_start(today, 1)
        return Span(first, today), _same_way_in(first, today, prev, _month_end(prev))
    if period == "year_to_date":
        first = date(today.year, 1, 1)
        # The same date a year before; 29 February compares with the 28th.
        last_day = min(today.day, _month_end(date(today.year - 1, today.month, 1)).day)
        same = date(today.year - 1, today.month, last_day)
        return Span(first, today), Span(date(today.year - 1, 1, 1), same)
    if period == "this_quarter":
        first = _quarter_start(today)
        prev = _quarter_start(first - timedelta(days=1))
        return Span(first, today), _same_way_in(first, today, prev, first - timedelta(days=1))
    if period == "last_month":
        first = _month_start(today, 1)
        prev = _month_start(today, 2)
        return Span(first, _month_end(first)), Span(prev, _month_end(prev))
    if period in ("last_7_days", "last_30_days"):
        days = 7 if period == "last_7_days" else 30
        span = Span(today - timedelta(days=days - 1), today)
        return span, _before(span)
    if period == "custom":
        if not start or not end:
            raise _Problem("a custom period needs from and to, as YYYY-MM-DD")
        span = Span(_day(start, "from", today), _day(end, "to", today))
        if span.end < span.start:
            raise _Problem("to must not be before from")
        return span, _before(span)
    raise _Problem(f"period must be one of {', '.join(PERIODS)}")


def _bucket(d: date, by: str) -> str:
    if by == "month":
        return d.strftime("%Y-%m")
    if by == "week":
        # Named by the Monday it starts on.
        return (d - timedelta(days=d.weekday())).isoformat()
    return d.isoformat()


def buckets(span: Span, by: str) -> list[str]:
    """Every bucket in the span, in order, so a quiet one shows as zero."""
    keys: list[str] = []
    d = span.start
    while d <= span.end:
        key = _bucket(d, by)
        if not keys or keys[-1] != key:
            keys.append(key)
        d += timedelta(days=1)
    return keys


# ── Counting ──────────────────────────────────────────────────────────────


@dataclass
class _Sum:
    gross: int = 0
    refunds: int = 0
    charges: int = 0
    disputed: int = 0
    disputed_amount: int = 0

    def add(self, gross: int, refunded: int, disputed: bool) -> None:
        self.gross += gross
        self.refunds += refunded
        self.charges += 1
        if disputed:
            self.disputed += 1
            self.disputed_amount += gross


@dataclass
class _Tally:
    # Per currency, in the currency's smallest unit.
    totals: dict[str, _Sum] = field(default_factory=dict)
    by: dict[str, dict[str, _Sum]] = field(default_factory=dict)
    pages: int = 0
    # Charges that could not be read: left out and said, never counted as nothing.
    unreadable: int = 0
    # Why it stopped short, if it did: a code below, or what the server said.
    short: str = ""


@dataclass
class _Budget:
    pages: int
    deadline: float


class _Unreadable(ValueError):
    pass


# Why a period stopped short, put into words by `_short_note`.
_PAGES, _TIME, _REPEAT, _GARBLED, _UNREADABLE = "pages", "time", "repeat", "garbled", "unreadable"


def _amount(value: Any) -> int:
    # A whole number of the smallest unit, never negative; True is not 1.
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise _Unreadable


def _counted(charge: Mapping[str, Any]) -> tuple[str, int, int, bool] | None:
    """A charge's currency, captured amount, refunded amount and whether it is
    disputed, if it brought money in. Raises _Unreadable for one that cannot be read."""
    # A hold not yet captured, or one that expired or was released, brought nothing in.
    if (
        charge.get("status") != "succeeded"
        or charge.get("paid") is not True
        or charge.get("captured") is not True
    ):
        return None
    currency = charge.get("currency")
    if not isinstance(currency, str) or not currency:
        raise _Unreadable
    if charge.get("amount_captured") is None:
        amount = gross = _amount(charge.get("amount"))
    else:
        gross = _amount(charge.get("amount_captured"))
        amount = _amount(charge.get("amount", gross))
    refunded = _amount(charge.get("amount_refunded", 0))
    # Capturing part of a hold releases the rest, and Stripe counts that release in
    # amount_refunded: it was never revenue, so it is no refund either.
    released = max(0, amount - gross)
    refunds = max(0, min(refunded - released, gross))
    return currency.lower(), gross, refunds, charge.get("disputed") is True


def _page_of(text: str) -> Mapping[str, Any] | None:
    """A list page from what the server said: the JSON object itself, or the first one
    found in its text."""
    candidates = [text, *text.split("\n")]
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])
    for c in candidates:
        try:
            found = json.loads(c)
        except (ValueError, RecursionError):
            continue
        if isinstance(found, dict) and isinstance(found.get("data"), list):
            return found
    return None


def _walk(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def accounts_in(text: str) -> list[tuple[str, str]]:
    """The Stripe accounts a listing names: (id, name) each, the name when it has one.
    From the listing's JSON; only when that names none, from ids in its words."""
    found: dict[str, str] = {}
    try:
        parsed: Any = json.loads(text)
    except (ValueError, RecursionError):
        parsed = None
    for item in _walk(parsed):
        ids = [v for v in item.values() if isinstance(v, str) and _ACCOUNT.fullmatch(v)]
        if len(ids) == 1:
            name = next(
                (
                    str(item[k]).strip()
                    for k in ("display_name", "business_name", "name")
                    if isinstance(item.get(k), str) and item[k].strip()
                ),
                "",
            )
            found.setdefault(ids[0], name)
    if not found:
        for m in _ACCOUNT.finditer(text):
            found.setdefault(m.group(0), "")
    return list(found.items())


def _major(amount: int, currency: str) -> float | int:
    """An amount in the smallest unit, as people write it."""
    if currency in ZERO_DECIMAL:
        return amount
    places = 3 if currency in THREE_DECIMAL else 2
    return round(amount / float(10**places), places)


def _figures(s: _Sum, currency: str) -> dict[str, Any]:
    return {
        "gross": _major(s.gross, currency),
        "refunds": _major(s.refunds, currency),
        "net": _major(s.gross - s.refunds, currency),
        "charges": s.charges,
    }


# ── The tool ─────────────────────────────────────────────────────────────


def companions(
    server: ServerConfig,
    registered: Iterable[str],
    call: Call,
    *,
    zone: Callable[[], str | None] | None = None,
) -> list[StripeRevenue]:
    """The built-in tools a connected server brings: `stripe_revenue` for a Stripe
    server from the catalogue whose `stripe_api_read` was registered, nothing for any
    other. `registered` names the server's registered tools as the server knows them."""
    names = set(registered)
    if server.catalog != CATALOG or READ_TOOL not in names:
        return []
    # The listing has its own setting: used only where the user let it run on its own.
    lists = ACCOUNTS_TOOL in names and server.risk_of(ACCOUNTS_TOOL) is Risk.READ
    return [StripeRevenue(server, call, zone=zone, lists_accounts=lists)]


class StripeRevenue(BaseTool):
    def __init__(
        self,
        server: ServerConfig,
        call: Call,
        *,
        zone: Callable[[], str | None] | None = None,
        lists_accounts: bool = True,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        # The user's word on the Stripe read it calls, never anything looser.
        risk = server.risk_of(READ_TOOL)
        asks = risk in GATED
        self.definition = ToolDefinition(
            name=NAME,
            description=(
                "Add up Stripe revenue for a period: gross, refunds, net and the number "
                "of successful charges, per currency, with the previous period to compare "
                "and optional totals by month, week or day. Use it for any revenue total "
                "or trend; never page charges yourself, and never use stripe_analytics, "
                "which needs a paid plan. Periods are in the user's timezone; custom takes "
                "from and to as YYYY-MM-DD. Good for a kpis or chart widget with source "
                "Stripe." + (f" {ASKS_BY_CARD}" if asks else "")
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": list(PERIODS)},
                    "from": {"type": "string", "description": "Custom only: first day."},
                    "to": {"type": "string", "description": "Custom only: last day."},
                    "by": {"type": "string", "enum": list(BY)},
                    "account": {
                        "type": "string",
                        "description": "Stripe account id (acct_...), when there are several.",
                    },
                    "livemode": {"type": "boolean", "description": "False for test mode."},
                },
                "required": ["period"],
                "additionalProperties": False,
            },
            risk=risk,
            category=CATEGORY,
            # Its own cap is a minute; the rest is room to say what it counted.
            timeout_s=MAX_SECONDS + 30,
        )
        self.server = server.name
        # The name its tile has: a widget built from it names this as its source.
        self.title = self.connector_title = server.shown
        self._timeout = server.timeout_s
        self._call = call
        self._zone = zone
        self._lists_accounts = lists_accounts
        self._clock = clock
        self._now = now

    # ── The card ─────────────────────────────────────────────────────────

    def describe(self, args: JSON) -> ActionSummary:
        period = str(args.get("period", ""))
        if period == "custom":
            when = f"{args.get('from', '?')} to {args.get('to', '?')}"
        else:
            when = PERIODS.get(period) or period
        parts = [f"Add up {self.title} revenue, {when}"]
        if args.get("by") not in (None, "none"):
            parts.append(f"by {args['by']}")
        if args.get("account"):
            parts.append(f"account {args['account']}")
        if args.get("livemode") is False:
            parts.append("test mode")
        return ActionSummary(text=", ".join(parts))

    def target(self, args: JSON) -> str:
        return f"mcp:{self.server}:{NAME}"

    # ── Running ──────────────────────────────────────────────────────────

    def _tz(self) -> ZoneInfo:
        name = self._zone() if self._zone is not None else None
        try:
            return ZoneInfo(name) if name else ZoneInfo("UTC")
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        tz = self._tz()
        period = str(args.get("period", ""))
        by = str(args.get("by") or "none")
        livemode = args.get("livemode", True) is not False
        budget = _Budget(pages=MAX_PAGES, deadline=self._clock() + MAX_SECONDS)
        try:
            if by not in BY:
                raise _Problem(f"by must be one of {', '.join(BY)}")
            today = self._now().astimezone(tz).date()
            current, previous = spans(period, today, args.get("from"), args.get("to"))
            keys = buckets(current, by) if by != "none" else []
            if len(keys) > MAX_BUCKETS:
                raise _Problem(f"that is {len(keys)} {by}s; ask by a longer stretch")
            account = str(args.get("account") or "")
            if account and not _ACCOUNT.fullmatch(account):
                raise _Problem("account must be a Stripe account id, such as acct_...")
            account = account or await self._account(budget)
            now = await self._count(current, tz, account, livemode, budget, by)
            if not now.pages:
                raise _Problem(_unanswered(now.short))
            before = await self._count(previous, tz, account, livemode, budget, "none")
        except _Problem as e:
            return ToolResult(ok=False, content=str(e), effect="none", error_kind="refused")
        out = self._result(period, current, previous, by, keys, now, before)
        return ToolResult(ok=True, content=json.dumps(out, ensure_ascii=False), effect="confirmed")

    async def _ask(self, tool: str, args: JSON, budget: _Budget) -> tuple[str, str]:
        """One call to the server: its text, or why there is none."""
        left = budget.deadline - self._clock()
        if left <= 0:
            return "", "time"
        out = await self._call(tool, args, min(self._timeout, left))
        if not out.ok:
            return "", out.text.strip()[:300] or "Stripe did not answer"
        return out.text, ""

    async def _account(self, budget: _Budget) -> str:
        """The one account there is, or none to name when the server cannot list them."""
        if not self._lists_accounts:
            return ""
        text, why = await self._ask(ACCOUNTS_TOOL, {}, budget)
        if why:
            raise _Problem(f"the Stripe accounts could not be listed: {why}")
        found = accounts_in(text)
        if len(found) == 1:
            return found[0][0]
        if not found:
            # Nothing to choose between: Stripe reads the account it signed in to.
            return ""
        named = ", ".join(f"{i} ({n[:60]})" if n else i for i, n in found)
        raise _Problem(f"Several Stripe accounts are connected: {named}. Say which with account.")

    async def _count(
        self,
        span: Span,
        tz: ZoneInfo,
        account: str,
        livemode: bool,
        budget: _Budget,
        by: str,
    ) -> _Tally:
        tally = _Tally()
        lo = int(datetime.combine(span.start, datetime.min.time(), tz).timestamp())
        hi_day = span.end + timedelta(days=1)
        hi = int(datetime.combine(hi_day, datetime.min.time(), tz).timestamp())
        after = ""
        seen: set[str] = set()
        while True:
            if budget.pages <= 0:
                tally.short = _PAGES
                return tally
            params: dict[str, Any] = {"created": {"gte": lo, "lt": hi}, "limit": PAGE_SIZE}
            if after:
                params["starting_after"] = after
            args: dict[str, Any] = {
                "stripe_api_operation_id": "GetCharges",
                "livemode": livemode,
                "parameters": params,
            }
            if account:
                args["stripe_context"] = account
            text, why = await self._ask(READ_TOOL, args, budget)
            if why:
                tally.short = why
                return tally
            page = _page_of(text)
            if page is None:
                tally.short = _GARBLED
                return tally
            budget.pages -= 1
            tally.pages += 1
            data = page["data"]
            fresh = 0
            oldest: int | None = None
            for charge in data:
                if not isinstance(charge, Mapping):
                    tally.unreadable += 1
                    continue
                cid = charge.get("id")
                if isinstance(cid, str):
                    # A page the server sends twice is counted once.
                    if cid in seen:
                        continue
                    seen.add(cid)
                fresh += 1
                created = charge.get("created")
                if not isinstance(created, int) or isinstance(created, bool):
                    tally.unreadable += 1
                    continue
                oldest = created if oldest is None else min(oldest, created)
                if not lo <= created < hi:
                    continue
                try:
                    counted = _counted(charge)
                except _Unreadable:
                    tally.unreadable += 1
                    continue
                if counted is None:
                    continue
                currency, gross, refunded, disputed = counted
                tally.totals.setdefault(currency, _Sum()).add(gross, refunded, disputed)
                if by != "none":
                    key = _bucket(datetime.fromtimestamp(created, tz).date(), by)
                    bucket = tally.by.setdefault(currency, {}).setdefault(key, _Sum())
                    bucket.add(gross, refunded, disputed)
            if page.get("has_more") is not True:
                break
            last = data[-1].get("id") if data and isinstance(data[-1], Mapping) else None
            if not isinstance(last, str):
                # More promised, and nothing to ask for it by.
                tally.short = _GARBLED
                return tally
            if not fresh or last == after:
                tally.short = _REPEAT
                return tally
            # Newest first: once a page reaches before the start, the rest is too.
            if oldest is not None and oldest < lo:
                break
            after = last
        if tally.unreadable:
            tally.short = _UNREADABLE
        return tally

    def _result(
        self,
        period: str,
        current: Span,
        previous: Span,
        by: str,
        keys: list[str],
        now: _Tally,
        before: _Tally,
    ) -> dict[str, Any]:
        compared = before.pages > 0
        currencies = sorted(
            set(now.totals) | set(before.totals),
            key=lambda c: (-now.totals.get(c, _Sum()).charges, c),
        )

        def one(currency: str) -> dict[str, Any]:
            got = now.totals.get(currency, _Sum())
            prior = before.totals.get(currency, _Sum())
            figures = _figures(got, currency)
            if got.disputed:
                figures["disputed"] = {
                    "charges": got.disputed,
                    "amount": _major(got.disputed_amount, currency),
                }
            then = prior.gross - prior.refunds
            if compared and then:
                change = (got.gross - got.refunds) / then - 1
                figures["net_change_pct"] = round(change * 100, 1)
            if compared:
                figures["previous"] = {
                    "from": previous.start.isoformat(),
                    "to": previous.end.isoformat(),
                    **_figures(prior, currency),
                }
            if by != "none":
                rows = now.by.get(currency, {})
                figures[f"by_{by}"] = [
                    {by: k, **_figures(rows.get(k, _Sum()), currency)} for k in keys
                ]
            return {"currency": currency, **figures}

        out: dict[str, Any] = {
            "period": period,
            "from": current.start.isoformat(),
            "to": current.end.isoformat(),
        }
        if len(currencies) <= 1:
            out.update(one(currencies[0]) if currencies else {**one(""), "currency": None})
        else:
            out["by_currency"] = [one(c) for c in currencies]
        out["complete"] = not now.short and not before.short
        notes = [NET_MEANS]
        if len(currencies) > 1:
            notes.append("Several currencies: each is added up on its own, never together.")
        if now.short:
            notes.append(_short_note(now.short, "these totals"))
        if not compared:
            notes.append("The previous period could not be counted, so there is no comparison.")
        elif before.short:
            notes.append(_short_note(before.short, "the previous period"))
        out["note"] = " ".join(notes)
        out["source"] = self.title
        return out


def _short_note(why: str, which: str) -> str:
    stop = {
        _PAGES: f"Stopped after {MAX_PAGES * PAGE_SIZE:,} charges",
        _TIME: f"Stopped after {MAX_SECONDS:g} seconds",
        _REPEAT: "Stripe repeated a page",
        _GARBLED: "Stripe's answer could not be read",
        _UNREADABLE: "Some charges could not be read and were left out",
    }.get(why) or f"Stripe stopped answering partway ({why})"
    return f"{stop}, so {which} cover only part of the period: say so."


def _unanswered(why: str) -> str:
    """Why nothing at all was counted: never a zero that looks like no sales."""
    if why == _GARBLED:
        return "Stripe's answer could not be read; nothing was counted"
    if why in (_TIME, _PAGES, ""):
        return "Stripe did not answer in time; nothing was counted"
    return f"Stripe did not answer: {why}"
