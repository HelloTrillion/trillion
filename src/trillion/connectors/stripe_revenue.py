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
from trillion.connectors.mcp.tool import CATEGORY, Call, Offered
from trillion.spine.contract import (
    ASKS_BY_CARD,
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
# Currencies Stripe counts in whole units, and those it counts in thousandths.
ZERO_DECIMAL = frozenset("bif clp djf gnf jpy kmf krw mga pyg rwf ugx vnd vuv xaf xof xpf".split())
THREE_DECIMAL = frozenset("bhd jod kwd omr tnd".split())

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


def _day(value: Any, what: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise _Problem(f"{what} must be a date as YYYY-MM-DD") from None


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
        span = Span(_day(start, "from"), _day(end, "to"))
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

    def add(self, gross: int, refunded: int) -> None:
        self.gross += gross
        self.refunds += refunded
        self.charges += 1


@dataclass
class _Tally:
    # Per currency, in the currency's smallest unit.
    totals: dict[str, _Sum] = field(default_factory=dict)
    by: dict[str, dict[str, _Sum]] = field(default_factory=dict)
    pages: int = 0
    # Why it stopped short, if it did.
    short: str = ""


@dataclass
class _Budget:
    pages: int
    deadline: float


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _counted(charge: Mapping[str, Any]) -> tuple[str, int, int] | None:
    """A charge's currency, captured amount and refunded amount, if it brought money in."""
    if charge.get("status") != "succeeded" or charge.get("paid") is not True:
        return None
    currency = charge.get("currency")
    if not isinstance(currency, str) or not currency:
        return None
    captured = charge.get("amount_captured")
    gross = _int(captured) if captured is not None else _int(charge.get("amount"))
    return currency.lower(), gross, _int(charge.get("amount_refunded"))


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
    """The Stripe accounts a listing names: (id, name) each, the name when it has one."""
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
                    str(item[k])
                    for k in ("display_name", "business_name", "name")
                    if isinstance(item.get(k), str) and item[k].strip()
                ),
                "",
            )
            found.setdefault(ids[0], name)
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
    offered: Iterable[Offered],
    call: Call,
    *,
    zone: Callable[[], str | None] | None = None,
) -> list[StripeRevenue]:
    """The built-in tools a connected server brings: `stripe_revenue` for a Stripe
    server from the catalogue that offers the read it needs, nothing for any other."""
    names = {o.name for o in offered}
    if server.catalog != CATALOG or READ_TOOL not in names:
        return []
    return [StripeRevenue(server, call, zone=zone, lists_accounts=ACCOUNTS_TOOL in names)]


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
        asks = risk is not Risk.READ
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
        self.title = server.shown
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
            account = str(args.get("account") or "") or await self._account(budget)
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
            raise _Problem("no Stripe account was found; say which one with account")
        named = ", ".join(f"{i} ({n})" if n else i for i, n in found)
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
        while True:
            if budget.pages <= 0:
                tally.short = "pages"
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
                tally.short = "Stripe's answer could not be read"
                return tally
            budget.pages -= 1
            tally.pages += 1
            data = [c for c in page["data"] if isinstance(c, Mapping)]
            for charge in data:
                created = charge.get("created")
                if not isinstance(created, int) or not lo <= created < hi:
                    continue
                counted = _counted(charge)
                if counted is None:
                    continue
                currency, gross, refunded = counted
                tally.totals.setdefault(currency, _Sum()).add(gross, refunded)
                if by != "none":
                    key = _bucket(datetime.fromtimestamp(created, tz).date(), by)
                    tally.by.setdefault(currency, {}).setdefault(key, _Sum()).add(gross, refunded)
            last = data[-1].get("id") if data else None
            if page.get("has_more") is not True or not isinstance(last, str):
                return tally
            after = last

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
        currencies = sorted(
            set(now.totals) | set(before.totals),
            key=lambda c: (-now.totals.get(c, _Sum()).charges, c),
        )

        def one(currency: str) -> dict[str, Any]:
            got = now.totals.get(currency, _Sum())
            prior = before.totals.get(currency, _Sum())
            figures = _figures(got, currency)
            then = prior.gross - prior.refunds
            if then:
                change = (got.gross - got.refunds) / then - 1
                figures["net_change_pct"] = round(change * 100, 1)
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
        complete = not now.short and not before.short
        out["complete"] = complete
        notes: list[str] = []
        if len(currencies) > 1:
            notes.append("Several currencies: each is added up on its own, never together.")
        if not complete:
            notes.append(_short_note(now.short or before.short, bool(now.short)))
        if notes:
            out["note"] = " ".join(notes)
        out["source"] = self.title
        return out


def _short_note(why: str, current: bool) -> str:
    which = "these totals" if current else "the previous period"
    if why == "pages":
        stop = f"Stopped after {MAX_PAGES * PAGE_SIZE:,} charges"
    elif why == "time":
        stop = f"Stopped after {MAX_SECONDS:g} seconds"
    else:
        stop = f"Stripe stopped answering partway ({why})"
    return f"{stop}, so {which} cover only part of the period: say so."


def _unanswered(why: str) -> str:
    """Why nothing at all was counted: never a zero that looks like no sales."""
    if why in ("time", "pages", ""):
        return "Stripe did not answer in time; nothing was counted"
    return f"Stripe did not answer: {why}"
