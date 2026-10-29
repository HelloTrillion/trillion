"""The payments watcher against a fake Stripe server and a fake clock: it starts from
the moment it is turned on, reads failed charges, disputes and (with a limit) large
payments through `stripe_api_read`, tells each once, never sends an email address
to the model, and does not read at all while the Stripe read asks first."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from tests.brain.fakes import ScriptedProvider, say
from tests.scheduler.conftest import FakeClock
from tests.unit.attention.test_runner import Door
from trillion.attention.payments import (
    FRESH_MS,
    LAG_MS,
    MAX_PAGES,
    AsksFirst,
    Payments,
    StripeError,
    amount_words,
)
from trillion.attention.runner import AttentionRunner, WatchContext
from trillion.attention.settings import DEFAULT, AttentionSettings, PaymentLimit
from trillion.brain.types import TextBlock
from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.tool import Outcome
from trillion.connectors.stripe_revenue import StripeRevenue
from trillion.spine.contract import Risk
from trillion.spine.registry import ToolRegistry
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.attention_store import SqliteAttentionStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

MIN = 60_000
NOW = 1_800_000_000_000
ON = replace(DEFAULT, watchers={"meetings": False, "email": False, "payments": True})
LIMIT = replace(ON, payment_limit=PaymentLimit(amount_minor=50_000, currency="usd"))
LINE = "A payment from Jo Park just failed."


def charge(
    cid: str,
    *,
    status: str = "failed",
    amount: int = 12_500,
    currency: str = "usd",
    created: int = NOW // 1000 - 60,
    name: str | None = "Jo Park",
    description: str | None = None,
    livemode: bool = True,
) -> dict[str, Any]:
    return {
        "id": cid,
        "object": "charge",
        "livemode": livemode,
        "amount": amount,
        "currency": currency,
        "created": created,
        "status": status,
        "paid": status == "succeeded",
        "captured": status == "succeeded",
        "failure_code": "card_declined" if status == "failed" else None,
        "description": description,
        "receipt_email": "jo@example.com",
        "billing_details": {
            "name": name,
            "email": "jo@example.com",
            "phone": "+1 555 0100",
            "address": {"line1": "1 Main St", "city": "Springfield", "postal_code": "12345"},
        },
        "payment_method_details": {"card": {"brand": "visa", "last4": "4242", "exp_year": 2030}},
    }


def dispute(did: str, *, amount: int = 9_900, created: int = NOW // 1000 - 60) -> dict[str, Any]:
    return {
        "id": did,
        "object": "dispute",
        "amount": amount,
        "currency": "usd",
        "created": created,
        "reason": "fraudulent",
        "status": "needs_response",
        "charge": "ch_disputed",
    }


@dataclass
class FakeStripe:
    """The Stripe server's `stripe_api_read`: answers GetCharges and GetDisputes from
    its lists, newest first, by `created[gte]`, a page at a time. Signed in to a live
    account or a sandbox (`live`): it reads that mode unless `livemode` asks for the
    other, which it has none of."""

    charges: list[dict[str, Any]] = field(default_factory=list)
    disputes: list[dict[str, Any]] = field(default_factory=list)
    page: int = 100
    down: bool = False
    live: bool = True
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def __call__(self, tool: str, args: Any, timeout: float) -> Outcome:
        self.calls.append((tool, json.loads(json.dumps(args))))
        if self.down:
            return Outcome(ok=False, text="Jo Park's card: rate limited")
        assert tool == "stripe_api_read"
        rows = {"GetCharges": self.charges, "GetDisputes": self.disputes}[
            args["stripe_api_operation_id"]
        ]
        p = args["parameters"]
        mode = args.get("livemode", self.live)
        rows = sorted(
            (
                r
                for r in rows
                if r["created"] >= p["created"]["gte"] and r.get("livemode", True) is mode
            ),
            key=lambda r: -r["created"],
        )
        if "starting_after" in p:
            ids = [r["id"] for r in rows]
            rows = rows[ids.index(p["starting_after"]) + 1 :]
        body = {"object": "list", "data": rows[: self.page], "has_more": len(rows) > self.page}
        return Outcome(ok=True, text=json.dumps(body))

    def reads(self) -> list[dict[str, Any]]:
        return [a for _, a in self.calls]


def stripe_server(risk: Risk | None = Risk.READ) -> ServerConfig:
    return ServerConfig(
        name="stripe",
        transport="http",
        url="https://mcp.stripe.example",
        title="Stripe",
        catalog="stripe",
        risks={"stripe_api_read": risk} if risk else {},
    )


def registry_with(fake: FakeStripe, risk: Risk | None = Risk.READ) -> ToolRegistry:
    """A registry with the Stripe companion the connected server brings."""
    reg = ToolRegistry()
    reg.register(StripeRevenue(stripe_server(risk), fake))
    return reg


def watcher(
    fake: FakeStripe, risk: Risk | None = Risk.READ, *, livemode: bool | None = None
) -> Payments:
    return Payments(registry_with(fake, risk), zone=lambda: "America/Denver", livemode=livemode)


def ctx(
    clock: FakeClock,
    *,
    last: int | None = NOW - 10 * MIN,
    cursor: str | None = None,
    settings: AttentionSettings = LIMIT,
) -> WatchContext:
    return WatchContext(
        owner="local",
        now_ms=clock(),
        clock=clock,
        settings=settings,
        notes="",
        cursor=cursor if cursor is not None else (str(last) if last is not None else None),
        last_check_ms=last,
        provider=None,
    )


# ── Reading ───────────────────────────────────────────────────────────


async def test_the_first_check_after_turning_on_starts_from_now() -> None:
    fake = FakeStripe([charge("ch_1")], [dispute("dp_1")])
    c = ctx(FakeClock(NOW), last=None)
    assert await watcher(fake).check(c) == []
    # Nothing asked of Stripe: the backlog is not news (D2).
    assert fake.calls == [] and c.cursor == str(NOW)


async def test_it_reads_charges_and_disputes_since_its_place_through_stripe_api_read() -> None:
    fake = FakeStripe()
    since = NOW - 10 * MIN
    c = ctx(FakeClock(NOW), cursor=str(since))
    assert await watcher(fake).check(c) == []
    assert fake.reads() == [
        {
            "stripe_api_operation_id": op,
            "parameters": {"created": {"gte": since // 1000}, "limit": 100},
        }
        for op in ("GetCharges", "GetDisputes")
    ]
    # Its place moves on, looking back five minutes for what Stripe writes late.
    assert c.cursor == str(NOW - LAG_MS)


@pytest.mark.parametrize("live", [True, False])
async def test_it_reads_in_the_mode_the_connection_is_in(live: bool) -> None:
    # No livemode is sent: a sandbox or test key sees its test charges, a live
    # connection its live ones.
    fake = FakeStripe([charge("ch_live"), charge("ch_test", livemode=False)], live=live)
    (f,) = await watcher(fake).check(ctx(FakeClock(NOW)))
    assert f.key == ("payment:ch_live:failed" if live else "payment:ch_test:failed")
    assert all("livemode" not in r for r in fake.reads())


@pytest.mark.parametrize("livemode", [True, False])
async def test_a_mode_asked_for_is_the_one_read(livemode: bool) -> None:
    fake = FakeStripe([charge("ch_live"), charge("ch_test", livemode=False)], live=True)
    found = await watcher(fake, livemode=livemode).check(ctx(FakeClock(NOW)))
    assert [f.key for f in found] == (
        ["payment:ch_live:failed"] if livemode else ["payment:ch_test:failed"]
    )
    assert all(r["livemode"] is livemode for r in fake.reads())


async def test_its_place_never_moves_back_and_never_reaches_far_back() -> None:
    fake = FakeStripe()
    c = ctx(FakeClock(NOW), cursor=str(NOW - MIN))
    await watcher(fake).check(c)
    assert c.cursor == str(NOW - MIN)
    # Off its read for days: it starts a day back at most.
    fake = FakeStripe()
    await watcher(fake).check(ctx(FakeClock(NOW), cursor=str(NOW - 7 * 24 * 60 * MIN)))
    gte = fake.reads()[0]["parameters"]["created"]["gte"]
    assert gte == (NOW - 24 * 60 * MIN) // 1000


async def test_a_failed_charge_is_found_with_its_amount_name_and_time() -> None:
    fake = FakeStripe([charge("ch_1"), charge("ch_ok", status="succeeded", amount=100)])
    (f,) = await watcher(fake).check(ctx(FakeClock(NOW)))
    assert f.key == "payment:ch_1:failed"
    assert f.facts == {
        "kind": "failed",
        "amount": "125.00 USD",
        "customer": "Jo Park",
        "created": "12:59 am",
        "failure": "card_declined",
    }
    assert f.detail == "Failed payment 125.00 USD — Jo Park"
    assert f.fresh_until_ms == NOW + FRESH_MS and f.link is None


async def test_a_dispute_is_found() -> None:
    fake = FakeStripe(disputes=[dispute("dp_1")])
    (f,) = await watcher(fake).check(ctx(FakeClock(NOW)))
    assert f.key == "payment:dp_1:dispute"
    assert f.facts == {
        "kind": "dispute",
        "amount": "99.00 USD",
        "created": "12:59 am",
        "reason": "fraudulent",
    }
    assert f.detail == "Dispute 99.00 USD"


async def test_a_payment_at_or_over_the_limit_is_found_and_under_it_nothing() -> None:
    fake = FakeStripe(
        [
            charge("ch_big", status="succeeded", amount=75_000),
            charge("ch_at", status="succeeded", amount=50_000),
            charge("ch_small", status="succeeded", amount=49_999),
            # Another currency is not compared with the limit.
            charge("ch_eur", status="succeeded", amount=900_000, currency="eur"),
            # Neither is one still pending.
            charge("ch_pending", status="pending", amount=900_000),
        ]
    )
    found = await watcher(fake).check(ctx(FakeClock(NOW)))
    assert sorted(f.key for f in found) == ["payment:ch_at:large", "payment:ch_big:large"]
    big = next(f for f in found if f.key == "payment:ch_big:large")
    assert (
        big.facts["amount"] == "750.00 USD" and big.detail == "Large payment 750.00 USD — Jo Park"
    )


async def test_with_no_limit_no_payment_is_large_but_failures_and_disputes_still_are() -> None:
    fake = FakeStripe(
        [charge("ch_big", status="succeeded", amount=9_000_000), charge("ch_1")],
        [dispute("dp_1")],
    )
    found = await watcher(fake).check(ctx(FakeClock(NOW), settings=ON))
    assert sorted(f.key for f in found) == ["payment:ch_1:failed", "payment:dp_1:dispute"]


async def test_no_email_address_card_address_or_description_reaches_the_facts() -> None:
    fake = FakeStripe(
        [
            charge("ch_1", description="Order 7 for jo@example.com, call 555 0199"),
            charge("ch_2", name="jo@example.com"),
            charge("ch_3", name=None),
        ]
    )
    found = await watcher(fake).check(ctx(FakeClock(NOW)))
    facts = json.dumps([f.facts for f in found])
    for secret in ("jo@example.com", "4242", "visa", "Main St", "12345", "555", "Order"):
        assert secret not in facts
    by = {f.key: f for f in found}
    # The description is someone's free text: never read at all.
    assert "description" not in by["payment:ch_1:failed"].facts
    # A name that is an address is no name; none is nothing.
    assert "customer" not in by["payment:ch_2:failed"].facts
    assert "customer" not in by["payment:ch_3:failed"].facts
    assert by["payment:ch_3:failed"].detail == "Failed payment 125.00 USD"


async def test_it_pages_up_to_a_few_pages() -> None:
    many = [charge(f"ch_{i}", created=NOW // 1000 - i) for i in range(10)]
    fake = FakeStripe(many, page=2)
    found = await watcher(fake).check(ctx(FakeClock(NOW)))
    charges = [r for r in fake.reads() if r["stripe_api_operation_id"] == "GetCharges"]
    assert len(charges) == MAX_PAGES
    assert charges[1]["parameters"]["starting_after"] == "ch_1"
    assert len(found) == 2 * MAX_PAGES


async def test_a_failed_read_leaves_its_place_and_says_only_its_kind() -> None:
    fake = FakeStripe([charge("ch_1")], down=True)
    c = ctx(FakeClock(NOW), cursor="123000")
    with pytest.raises(StripeError) as e:
        await watcher(fake).check(c)
    assert c.cursor == "123000" and "Jo" not in str(e.value)


@pytest.mark.parametrize("risk", [None, Risk.IRREVERSIBLE, Risk.SPEND])
async def test_while_the_stripe_read_asks_first_nothing_is_read(risk: Risk | None) -> None:
    fake = FakeStripe([charge("ch_1")])
    w = watcher(fake, risk)
    assert w.enabled(ON) and w.asks_first()
    with pytest.raises(AsksFirst):
        await w.check(ctx(FakeClock(NOW)))
    assert fake.calls == []


def test_it_runs_only_when_on_and_stripe_is_connected() -> None:
    fake = FakeStripe()
    assert not watcher(fake).enabled(DEFAULT)
    assert watcher(fake).enabled(ON)
    # No Stripe server connected: no companion registered.
    assert not Payments(ToolRegistry()).enabled(ON)


@pytest.mark.parametrize(
    ("amount", "currency", "words"),
    [
        (12_500, "usd", "125.00 USD"),
        (123_456_789, "usd", "1,234,567.89 USD"),
        (5_000, "jpy", "5,000 JPY"),
        (1_500, "kwd", "1.500 KWD"),
    ],
)
def test_amounts_are_in_major_units(amount: int, currency: str, words: str) -> None:
    assert amount_words(amount, currency) == words


# ── In the runner: once, one notice, asks first in the status ─────────


@dataclass
class Rig:
    tmp: Path
    clock: FakeClock
    fake: FakeStripe
    risk: Risk | None = Risk.READ
    provider: ScriptedProvider | None = None
    door: Door = field(default_factory=Door)
    settings: AttentionSettings = LIMIT
    conns: list[Any] = field(default_factory=list)
    store: SqliteAttentionStore | None = None

    async def runner(self) -> AttentionRunner:
        a, b = await connect(self.tmp / "t.db"), await connect(self.tmp / "t.db")
        await migrate(a)
        self.conns += [a, b]
        self.store = SqliteAttentionStore(a)
        r = AttentionRunner(
            self.store,
            SqliteScheduleStore(b),
            self.door,
            clock=self.clock,
            owner="local",
            provider=lambda: self.provider,
            settings=lambda: self.settings,
        )
        r.add(watcher(self.fake, self.risk))
        return r


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    # Both come in a minute after it is turned on.
    later = NOW // 1000 + 60
    fake = FakeStripe([charge("ch_1", created=later)], [dispute("dp_1", created=later)])
    r = Rig(tmp_path, FakeClock(NOW), fake)
    try:
        yield r
    finally:
        for c in r.conns:
            await c.close()


async def turned_on(rig: Rig) -> AttentionRunner:
    r = await rig.runner()
    await r.tick()
    assert rig.fake.calls == []
    rig.clock.advance(10 * MIN)
    return r


async def test_what_one_check_finds_is_one_notice_told_once(rig: Rig) -> None:
    rig.provider = ScriptedProvider(say(LINE))
    r = await turned_on(rig)
    await r.tick()
    (n,) = rig.door.notices
    assert (n.kind, n.title, n.text) == ("heads_up", "Payment", LINE)
    assert n.detail == "Failed payment 125.00 USD — Jo Park; Dispute 99.00 USD"
    assert n.fresh_until_ms == rig.clock() + FRESH_MS
    # The model saw the finds only inside the untrusted wrap, with no tools.
    ((_, messages),) = rig.provider.calls
    (block,) = messages[0].blocks
    assert isinstance(block, TextBlock) and "<untrusted_tool_result" in block.text
    assert rig.provider.tool_calls == [[]]
    # Once: the overlap reads them again, and nothing more is said.
    rig.clock.advance(10 * MIN)
    await r.tick()
    assert len(rig.door.notices) == 1 and len(rig.provider.calls) == 1
    assert len(rig.fake.calls) == 4


async def test_while_stripe_asks_first_nothing_is_read_and_the_status_says_why(rig: Rig) -> None:
    rig.risk = Risk.IRREVERSIBLE
    r = await rig.runner()
    await r.tick()
    assert rig.fake.calls == [] and rig.door.notices == []
    assert rig.store is not None
    state = await rig.store.state("local", "payments")
    assert state.last_error == "AsksFirst" and state.last_check_ms is None


async def test_with_payments_off_or_stripe_not_connected_nothing_is_read(rig: Rig) -> None:
    rig.settings = DEFAULT
    r = await rig.runner()
    await r.tick()
    assert rig.fake.calls == []
    assert rig.store is not None
    assert (await rig.store.state("local", "payments")).last_error is None
    # Not connected: no companion, so not even the status changes.
    runner = AttentionRunner(
        rig.store,
        SqliteScheduleStore(rig.conns[1]),
        rig.door,
        clock=rig.clock,
        owner="local",
        provider=lambda: None,
        settings=lambda: ON,
    )
    runner.add(Payments(ToolRegistry()))
    await runner.tick()
    assert (await rig.store.state("local", "payments")).last_error is None
