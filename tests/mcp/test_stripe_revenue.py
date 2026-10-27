"""Stripe revenue, added up on the server from the Stripe connection the user has."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.spine.conftest import Spine
from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.servers import McpServers, ServerState
from trillion.connectors.mcp.tool import Offered, Outcome
from trillion.connectors.stripe_revenue import (
    MAX_PAGES,
    NAME,
    Span,
    StripeRevenue,
    accounts_in,
    buckets,
    companions,
    spans,
)
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import Risk, ToolContext
from trillion.spine.registry import runs_unattended
from trillion.workspace import live

LA = "America/Los_Angeles"
# Tuesday 27 October 2026, mid-morning in Los Angeles.
NOW = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)
CTX = ToolContext(owner_id="local", session_id="s1", turn=1, call_id="c1", via="text")
OFFERED = [
    Offered(name="stripe_api_read", description="Read", input_schema={"type": "object"}),
    Offered(name="list_available_accounts_or_orgs", description="", input_schema={}),
    Offered(name="stripe_analytics", description="Sigma", input_schema={"type": "object"}),
]


def at(day: str, zone: str = LA, hour: int = 12) -> int:
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hour, tzinfo=ZoneInfo(zone)).timestamp())


def charge(
    n: int,
    day: str,
    amount: int,
    *,
    refunded: int = 0,
    currency: str = "usd",
    status: str = "succeeded",
    paid: bool = True,
    captured: int | None = None,
) -> dict[str, Any]:
    return {
        "id": f"ch_{n}",
        "object": "charge",
        "amount": amount,
        "amount_captured": amount if captured is None else captured,
        "amount_refunded": refunded,
        "currency": currency,
        "status": status,
        "paid": paid,
        "created": at(day),
        "billing_details": {"name": "Sam Doe", "email": "sam@example.com"},
        "receipt_email": "jo@example.com",
        "description": "Order for Alex",
    }


class FakeStripe:
    """The Stripe server's two reads, answering from a list of charges, newest first,
    a page at a time."""

    def __init__(
        self,
        charges: list[dict[str, Any]],
        *,
        accounts: list[dict[str, Any]] | None = None,
        page: int = 100,
        fail_after: int | None = None,
    ) -> None:
        self.charges = sorted(charges, key=lambda c: -c["created"])
        self.accounts = (
            [{"id": "acct_test1", "display_name": "Sam's shop"}] if accounts is None else accounts
        )
        self.page = page
        self.fail_after = fail_after
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, args: Any, timeout: float) -> Outcome:
        self.calls.append((tool, json.loads(json.dumps(args))))
        if tool == "list_available_accounts_or_orgs":
            return Outcome(ok=True, text=json.dumps({"data": self.accounts}))
        assert tool == "stripe_api_read" and args["stripe_api_operation_id"] == "GetCharges"
        reads = sum(t == "stripe_api_read" for t, _ in self.calls)
        if self.fail_after is not None and reads > self.fail_after:
            return Outcome(ok=False, text="rate limited")
        p = args["parameters"]
        lo, hi = p["created"]["gte"], p["created"]["lt"]
        assert p["limit"] == 100
        rows = [c for c in self.charges if lo <= c["created"] < hi]
        if "starting_after" in p:
            ids = [c["id"] for c in rows]
            rows = rows[ids.index(p["starting_after"]) + 1 :]
        out = rows[: self.page]
        body = {"object": "list", "data": out, "has_more": len(rows) > self.page}
        return Outcome(ok=True, text=json.dumps(body))

    def reads(self) -> list[dict[str, Any]]:
        return [a for t, a in self.calls if t == "stripe_api_read"]


def server(risk: Risk | None = None, catalog: str = "stripe") -> ServerConfig:
    return ServerConfig(
        name="stripe",
        transport="http",
        url="https://mcp.stripe.example",
        title="Stripe",
        catalog=catalog,
        risks={"stripe_api_read": risk} if risk else {},
    )


def tool(fake: FakeStripe, risk: Risk | None = Risk.READ, **kw: Any) -> StripeRevenue:
    clock = kw.pop("clock", lambda: 0.0)
    return StripeRevenue(server(risk), fake, zone=lambda: LA, now=lambda: NOW, clock=clock, **kw)


async def run(t: StripeRevenue, **args: Any) -> dict[str, Any]:
    r = await t.execute(args, CTX)
    assert r.ok, r.content
    out: dict[str, Any] = json.loads(r.content)
    return out


# ── Periods ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("period", "today", "now", "before"),
    [
        ("month_to_date", "2026-10-27", ("2026-10-01", "2026-10-27"), ("2026-09-01", "2026-09-27")),
        ("month_to_date", "2026-03-31", ("2026-03-01", "2026-03-31"), ("2026-02-01", "2026-02-28")),
        ("month_to_date", "2026-01-05", ("2026-01-01", "2026-01-05"), ("2025-12-01", "2025-12-05")),
        ("year_to_date", "2026-10-27", ("2026-01-01", "2026-10-27"), ("2025-01-01", "2025-10-27")),
        ("year_to_date", "2028-02-29", ("2028-01-01", "2028-02-29"), ("2027-01-01", "2027-02-28")),
        ("last_month", "2026-01-15", ("2025-12-01", "2025-12-31"), ("2025-11-01", "2025-11-30")),
        ("last_month", "2026-03-10", ("2026-02-01", "2026-02-28"), ("2026-01-01", "2026-01-31")),
        ("last_7_days", "2026-01-03", ("2025-12-28", "2026-01-03"), ("2025-12-21", "2025-12-27")),
        ("last_30_days", "2026-10-27", ("2026-09-28", "2026-10-27"), ("2026-08-29", "2026-09-27")),
        ("this_quarter", "2026-10-27", ("2026-10-01", "2026-10-27"), ("2026-07-01", "2026-07-27")),
        ("this_quarter", "2026-01-10", ("2026-01-01", "2026-01-10"), ("2025-10-01", "2025-10-10")),
        ("this_quarter", "2026-06-30", ("2026-04-01", "2026-06-30"), ("2026-01-01", "2026-03-31")),
    ],
)
def test_periods_and_what_they_compare_with(
    period: str, today: str, now: tuple[str, str], before: tuple[str, str]
) -> None:
    got = spans(period, date.fromisoformat(today))
    as_text = [(s.start.isoformat(), s.end.isoformat()) for s in got]
    assert as_text == [now, before]


def test_a_custom_period_compares_with_the_equal_stretch_before() -> None:
    now, before = spans("custom", date(2026, 10, 27), "2026-01-01", "2026-01-31")
    assert (now.start, now.end) == (date(2026, 1, 1), date(2026, 1, 31))
    assert (before.start, before.end) == (date(2025, 12, 1), date(2025, 12, 31))


@pytest.mark.parametrize(
    ("start", "end", "why"),
    [
        (None, "2026-01-31", "from and to"),
        ("2026-02-01", "2026-01-31", "before"),
        ("January", "2026-01-31", "YYYY-MM-DD"),
    ],
)
async def test_a_bad_custom_period_is_refused_before_anything_is_read(
    start: str | None, end: str, why: str
) -> None:
    fake = FakeStripe([])
    args = {"period": "custom", "to": end, **({"from": start} if start else {})}
    r = await tool(fake).execute(args, CTX)
    assert not r.ok and why in r.content and r.effect == "none"
    assert fake.calls == []


def test_buckets_cover_the_span_with_none_missing() -> None:
    span = Span(date(2025, 11, 15), date(2026, 2, 3))
    assert buckets(span, "month") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    # Weeks named by their Monday, across the year's end.
    weeks = buckets(Span(date(2025, 12, 28), date(2026, 1, 6)), "week")
    assert weeks == ["2025-12-22", "2025-12-29", "2026-01-05"]
    assert len(buckets(Span(date(2026, 1, 1), date(2026, 1, 31)), "day")) == 31


async def test_the_period_is_the_users_day_not_utc() -> None:
    # 23:30 on 30 September in Los Angeles is already 1 October in UTC.
    late = charge(1, "2026-09-30", 5000)
    late["created"] = at("2026-09-30", hour=23) + 1800
    early = charge(2, "2026-10-01", 700)
    early["created"] = at("2026-10-01", hour=0) + 60
    one_day = {"period": "custom", "from": "2026-10-01", "to": "2026-10-01"}
    out = await run(tool(FakeStripe([late, early])), **one_day)
    assert out["previous"]["from"] == out["previous"]["to"] == "2026-09-30"
    assert out["gross"] == 7.0 and out["charges"] == 1
    assert out["previous"]["gross"] == 50.0


async def test_the_bounds_sent_are_local_midnights() -> None:
    fake = FakeStripe([])
    await run(tool(fake), period="last_month")
    first = fake.reads()[0]["parameters"]["created"]
    assert first["gte"] == int(datetime(2026, 9, 1, tzinfo=ZoneInfo(LA)).timestamp())
    assert first["lt"] == int(datetime(2026, 10, 1, tzinfo=ZoneInfo(LA)).timestamp())


# ── Counting ─────────────────────────────────────────────────────────────


async def test_totals_refunds_and_net() -> None:
    fake = FakeStripe(
        [
            charge(1, "2026-10-02", 10000),
            charge(2, "2026-10-10", 2550, refunded=550),
            charge(3, "2026-10-20", 1999),
        ]
    )
    out = await run(tool(fake), period="month_to_date")
    assert out["period"] == "month_to_date" and out["currency"] == "usd"
    assert (out["gross"], out["refunds"], out["net"], out["charges"]) == (145.49, 5.5, 139.99, 3)
    assert out["complete"] is True and out["source"] == "Stripe"
    first = fake.reads()[0]
    assert first["stripe_context"] == "acct_test1" and first["livemode"] is True


async def test_failed_and_unpaid_charges_are_not_revenue() -> None:
    fake = FakeStripe(
        [
            charge(1, "2026-10-02", 10000),
            charge(2, "2026-10-03", 5000, status="failed", paid=False),
            charge(3, "2026-10-04", 5000, status="pending"),
            charge(4, "2026-10-05", 5000, paid=False),
        ]
    )
    out = await run(tool(fake), period="month_to_date")
    assert (out["gross"], out["charges"]) == (100.0, 1)


async def test_the_captured_amount_counts_and_the_amount_is_the_fallback() -> None:
    partly = charge(1, "2026-10-02", 10000, captured=6000)
    old = charge(2, "2026-10-03", 2500)
    del old["amount_captured"]
    out = await run(tool(FakeStripe([partly, old])), period="month_to_date")
    assert out["gross"] == 85.0


async def test_it_pages_until_stripe_says_there_is_no_more() -> None:
    rows = [charge(n, f"2026-10-{1 + n % 27:02d}", 100) for n in range(250)]
    fake = FakeStripe(rows)
    out = await run(tool(fake), period="month_to_date")
    assert out["charges"] == 250 and out["gross"] == 250.0 and out["complete"] is True
    reads = fake.reads()
    current = [
        r
        for r in reads
        if r["parameters"]["created"]["gte"] == reads[0]["parameters"]["created"]["gte"]
    ]
    assert len(current) == 3
    assert "starting_after" not in current[0]["parameters"]
    newest_first = fake.charges
    assert current[1]["parameters"]["starting_after"] == newest_first[99]["id"]
    assert current[2]["parameters"]["starting_after"] == newest_first[199]["id"]


async def test_the_previous_period_is_counted_for_comparison() -> None:
    fake = FakeStripe([charge(1, "2026-10-05", 20000), charge(2, "2026-09-05", 10000)])
    out = await run(tool(fake), period="month_to_date")
    assert out["previous"] == {
        "from": "2026-09-01",
        "to": "2026-09-27",
        "gross": 100.0,
        "refunds": 0.0,
        "net": 100.0,
        "charges": 1,
    }
    assert out["net_change_pct"] == 100.0


async def test_buckets_by_month() -> None:
    fake = FakeStripe(
        [
            charge(1, "2026-01-15", 1000),
            charge(2, "2026-03-02", 2000, refunded=500),
            charge(3, "2026-03-28", 3000),
        ]
    )
    out = await run(tool(fake), period="year_to_date", by="month")
    months = out["by_month"]
    assert [m["month"] for m in months] == [f"2026-{m:02d}" for m in range(1, 11)]
    assert months[0] == {
        "month": "2026-01",
        "gross": 10.0,
        "refunds": 0.0,
        "net": 10.0,
        "charges": 1,
    }
    assert months[1]["charges"] == 0  # a quiet month is shown as zero
    assert months[2] == {
        "month": "2026-03",
        "gross": 50.0,
        "refunds": 5.0,
        "net": 45.0,
        "charges": 2,
    }
    assert "by_month" not in out["previous"]


async def test_too_many_buckets_is_refused() -> None:
    fake = FakeStripe([])
    r = await tool(fake).execute({"period": "year_to_date", "by": "day"}, CTX)
    assert not r.ok and "longer stretch" in r.content and fake.reads() == []


async def test_several_currencies_are_added_up_apart() -> None:
    fake = FakeStripe(
        [
            charge(1, "2026-10-02", 1000, currency="usd"),
            charge(2, "2026-10-03", 2000, currency="eur"),
            charge(3, "2026-10-04", 3000, currency="eur"),
        ]
    )
    out = await run(tool(fake), period="month_to_date")
    assert "gross" not in out and "never together" in out["note"]
    eur, usd = out["by_currency"]
    assert (eur["currency"], eur["gross"], eur["charges"]) == ("eur", 50.0, 2)
    assert (usd["currency"], usd["gross"], usd["charges"]) == ("usd", 10.0, 1)


async def test_a_zero_decimal_currency_is_whole_units() -> None:
    fake = FakeStripe([charge(1, "2026-10-02", 1500, refunded=200, currency="JPY")])
    out = await run(tool(fake), period="month_to_date")
    assert (out["currency"], out["gross"], out["refunds"], out["net"]) == ("jpy", 1500, 200, 1300)


async def test_nothing_sold_is_said_as_zero() -> None:
    out = await run(tool(FakeStripe([])), period="last_7_days")
    assert (out["currency"], out["gross"], out["charges"], out["complete"]) == (None, 0.0, 0, True)


async def test_no_charge_detail_or_person_reaches_the_result() -> None:
    fake = FakeStripe([charge(1, "2026-10-02", 1000), charge(2, "2026-09-02", 1000)])
    r = await tool(fake).execute({"period": "month_to_date", "by": "week"}, CTX)
    for leaked in (
        "ch_1",
        "ch_2",
        "Sam",
        "sam@example.com",
        "jo@example.com",
        "Alex",
        "acct_test1",
    ):
        assert leaked not in r.content, leaked


async def test_test_mode_is_asked_for_when_said() -> None:
    fake = FakeStripe([])
    await run(tool(fake), period="last_7_days", livemode=False)
    assert all(r["livemode"] is False for r in fake.reads())


# ── Caps ─────────────────────────────────────────────────────────────────


async def test_the_page_cap_returns_what_was_counted_and_says_so() -> None:
    rows = [charge(n, "2026-03-01", 100) for n in range(MAX_PAGES * 2 + 5)]
    fake = FakeStripe(rows, page=2)
    out = await run(tool(fake), period="year_to_date")
    assert out["complete"] is False and out["charges"] == MAX_PAGES * 2
    assert "5,000 charges" in out["note"] and "only part" in out["note"]
    assert len(fake.reads()) == MAX_PAGES


async def test_the_time_cap_returns_what_was_counted_and_says_so() -> None:
    ticks = iter([0.0, 0.0, 10.0, 70.0, 80.0, 90.0])
    rows = [charge(n, "2026-10-02", 100) for n in range(5)]
    fake = FakeStripe(rows, page=2)
    out = await run(tool(fake, clock=lambda: next(ticks)), period="month_to_date")
    assert out["complete"] is False and out["charges"] == 2 and "60 seconds" in out["note"]


async def test_a_server_that_stops_partway_is_said_plainly() -> None:
    rows = [charge(n, "2026-10-02", 100) for n in range(5)]
    out = await run(tool(FakeStripe(rows, page=2, fail_after=1)), period="month_to_date")
    assert out["complete"] is False and out["charges"] == 2 and "partway" in out["note"]


async def test_a_server_that_never_answers_is_a_failure_not_zero() -> None:
    r = await tool(FakeStripe([], fail_after=0)).execute({"period": "month_to_date"}, CTX)
    assert not r.ok and "rate limited" in r.content


# ── Accounts ─────────────────────────────────────────────────────────────


async def test_the_one_account_is_used_without_asking() -> None:
    fake = FakeStripe([])
    await run(tool(fake), period="last_7_days")
    assert fake.calls[0][0] == "list_available_accounts_or_orgs"
    assert {r["stripe_context"] for r in fake.reads()} == {"acct_test1"}


async def test_several_accounts_are_named_and_nothing_is_read() -> None:
    accounts = [
        {"id": "acct_test1", "display_name": "Sam's shop"},
        {"id": "acct_test2", "display_name": "Jo's studio"},
    ]
    fake = FakeStripe([], accounts=accounts)
    r = await tool(fake).execute({"period": "last_7_days"}, CTX)
    assert not r.ok and "acct_test1 (Sam's shop)" in r.content and "acct_test2" in r.content
    assert fake.reads() == []


async def test_an_account_given_is_used_as_given() -> None:
    fake = FakeStripe([], accounts=[{"id": "acct_test1"}, {"id": "acct_test2"}])
    await run(tool(fake), period="last_7_days", account="acct_test2")
    assert fake.calls[0][0] == "stripe_api_read"
    assert {r["stripe_context"] for r in fake.reads()} == {"acct_test2"}


def test_accounts_are_read_from_json_or_text() -> None:
    assert accounts_in('{"accounts": [{"account_id": "acct_test1", "name": "Alex"}]}') == [
        ("acct_test1", "Alex")
    ]
    assert accounts_in("You have access to acct_test1 and acct_test2.") == [
        ("acct_test1", ""),
        ("acct_test2", ""),
    ]


# ── Offered, and gated, as the user set up Stripe ────────────────────────


def servers(spine: Spine, tmp: Path) -> McpServers:
    return McpServers(spine.registry, MemoryStore(), tmp / "logs", zone=lambda: LA)


def test_only_a_stripe_server_from_the_catalogue_brings_it() -> None:
    fake = FakeStripe([])
    assert [t.definition.name for t in companions(server(), OFFERED, fake)] == [NAME]
    assert companions(server(catalog=""), OFFERED, fake) == []
    assert companions(server(), OFFERED[1:], fake) == []


async def test_it_is_offered_only_while_stripe_is_connected(spine: Spine, tmp_path: Path) -> None:
    mcp = servers(spine, tmp_path)
    assert spine.registry.get(NAME) is None
    state = ServerState(config=server(Risk.READ), status="connected")
    mcp.states["stripe"] = state
    mcp._register(state, OFFERED)
    assert spine.registry.get(NAME) is not None
    assert "stripe_revenue" in [d.name for d in spine.registry.definitions()]
    # Not one of the server's own tools: Settings lists only what Stripe offers.
    assert NAME not in [t.name for t in state.tools]
    await mcp.disconnect("stripe")
    assert spine.registry.get(NAME) is None


async def test_another_server_does_not_bring_it(spine: Spine, tmp_path: Path) -> None:
    mcp = servers(spine, tmp_path)
    other = ServerConfig(name="shop", transport="http", url="https://x.example", catalog="square")
    state = ServerState(config=other, status="connected")
    mcp._register(state, OFFERED)
    assert spine.registry.get(NAME) is None


@pytest.mark.parametrize(("given", "risk"), [(Risk.READ, Risk.READ), (None, Risk.IRREVERSIBLE)])
async def test_its_risk_is_the_one_the_user_gave_stripe_api_read(
    spine: Spine, tmp_path: Path, given: Risk | None, risk: Risk
) -> None:
    mcp = servers(spine, tmp_path)
    state = ServerState(config=server(given), status="connected")
    mcp.states["stripe"] = state
    mcp._register(state, OFFERED)
    d = spine.registry.definition(NAME)
    assert d is not None and d.risk is risk and d.category == "mcp"
    assert runs_unattended(d) is (risk is Risk.READ)
    assert ("approval card" in d.description) is (risk is not Risk.READ)


async def test_a_retune_moves_its_risk_with_stripe_api_read(spine: Spine, tmp_path: Path) -> None:
    mcp = servers(spine, tmp_path)
    state = ServerState(config=server(Risk.READ), status="connected")
    mcp.states["stripe"] = state
    state.offered = OFFERED
    mcp._register(state, OFFERED)
    mcp.retune(server(None))
    d = spine.registry.definition(NAME)
    assert d is not None and d.risk is Risk.IRREVERSIBLE
    mcp.retune(server(Risk.READ))
    d = spine.registry.definition(NAME)
    assert d is not None and d.risk is Risk.READ


async def test_free_it_runs_and_its_answer_is_untrusted(spine: Spine) -> None:
    fake = FakeStripe([charge(1, "2026-10-02", 1000)])
    spine.registry.register(tool(fake))
    r = await spine.call(NAME, {"period": "month_to_date"})
    assert r.status == "succeeded" and "untrusted" in r.to_model()
    assert '"gross": 10.0' in r.content


async def test_asking_first_it_puts_up_a_card_and_reads_nothing_until_yes(spine: Spine) -> None:
    fake = FakeStripe([charge(1, "2026-10-02", 1000)])
    spine.registry.register(tool(fake, risk=None))
    r = await spine.call(NAME, {"period": "year_to_date", "by": "month"})
    assert r.status == "confirmation_required" and r.pending_id
    assert fake.calls == []
    pending = await spine.store.get_pending(r.pending_id)
    assert pending is not None
    assert pending.summary == "Add up Stripe revenue, year to date, by month"
    done = await spine.router.resolve(r.pending_id, "confirm", spine.ctx(turn=2, via="tap"))
    assert done.status == "succeeded" and fake.reads()


def test_the_card_names_the_account_and_test_mode() -> None:
    t = tool(FakeStripe([]), risk=None)
    text = t.describe(
        {
            "period": "custom",
            "from": "2026-01-01",
            "to": "2026-03-31",
            "account": "acct_test2",
            "livemode": False,
        }
    ).text
    assert text == "Add up Stripe revenue, 2026-01-01 to 2026-03-31, account acct_test2, test mode"


def test_a_widget_built_from_it_names_stripe_and_stays_fresh() -> None:
    t = tool(FakeStripe([]))
    assert live.connector(NAME, t) == "Stripe"
    assert live.eligible(t.definition)
    assert not live.eligible(tool(FakeStripe([]), risk=None).definition)


# ── What she is told ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["text.md", "voice.md"])
def test_she_is_told_to_use_it_never_analytics(name: str) -> None:
    text = (files("trillion.persona") / "prompts" / name).read_text(encoding="utf-8")
    assert "stripe_revenue" in text and "never stripe_analytics" in text
    assert chr(0x2014) not in text and chr(0x2013) not in text
