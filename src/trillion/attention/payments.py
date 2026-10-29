"""The payments watcher (the speaking up spec, Payments): one line when a charge fails,
a dispute opens, or a payment at or over the user's limit comes in.

Every ten minutes it reads Stripe through the connected Stripe server's
`stripe_api_read`, as `stripe_revenue` does, and only while the Stripe tools are there:
it finds that server by the `stripe_revenue` companion, which is registered only while
a Stripe server from the catalogue is connected with `stripe_api_read`, and carries
the risk the user gave that tool. When that risk asks first, the watcher does not
read: a run nobody watches gets only tools that never ask (the reminders spec, D5), so
its check fails as `AsksFirst` and Settings says why.

Two lists, each newest first, from five minutes before its last place (the dedup by
key absorbs the overlap): charges created since (`GetCharges`; Stripe's list can't
filter by status, so failed and large ones are picked here) and disputes created
since (`GetDisputes`). At most three pages of each. The first check after it is
turned on only marks the moment (D2).

It reads in the mode the connection is in: `livemode` is left out, so the server
reads the account or sandbox the user signed in to (or whose key it holds), as
Stripe's MCP tools do by default. A test-mode connection sees its test charges.

What Stripe returns is someone else's words (a customer's name), so it reaches the
model only wrapped as untrusted (`words.line`). A charge's description is free text
anyone paying can shape, so it is never read. Never a card's details, an address or
an email address: a customer is named by the name Stripe has, or not at all.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from trillion.attention.runner import JSON, Find, WatchContext
from trillion.attention.settings import AttentionSettings, PaymentLimit
from trillion.connectors import stripe_revenue
from trillion.connectors.stripe_revenue import StripeRevenue
from trillion.scheduler import when
from trillion.spine.registry import runs_unattended

if TYPE_CHECKING:
    from trillion.spine.registry import ToolRegistry

logger = logging.getLogger(__name__)

NAME = "payments"
TITLE = "Payment"
INTERVAL_S = 600.0
# Spoken only for half an hour after it is found (D9).
FRESH_MS = 30 * 60_000
# Stripe may write an object a moment after it was created: each check looks back.
LAG_MS = 5 * 60_000
# A watcher off its read for long (Stripe asked first, or was not connected) starts
# no further back than this: older news is a backlog, not a heads-up.
MAX_BACK_MS = 24 * 60 * 60_000
PAGE_SIZE = 100
MAX_PAGES = 3
# Under the runner's 20 s limit for the whole check, at most six calls.
CALL_TIMEOUT_S = 6.0
NAME_MAX = 80
# What a Stripe object id looks like ("ch_3Nx..."); anything else is not kept.
_ID = re.compile(r"^[A-Za-z0-9_]{1,80}$")
# A Stripe enum value ("card_declined", "fraudulent").
_CODE = re.compile(r"^[a-z_]{1,40}$")
_KIND_WORDS = {"failed": "Failed payment", "large": "Large payment", "dispute": "Dispute"}


class AsksFirst(Exception):
    """The Stripe read is set to ask first, so a check nobody watches can't use it."""


class StripeError(Exception):
    """Stripe did not answer, or its answer could not be read."""


def _one_line(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def amount_words(amount: int, currency: str) -> str:
    """'1,250.00 USD': an amount in the currency's smallest unit, as people write it."""
    c = currency.lower()
    places = (
        0 if c in stripe_revenue.ZERO_DECIMAL else 3 if c in stripe_revenue.THREE_DECIMAL else 2
    )
    major = Decimal(amount).scaleb(-places)
    return f"{major:,.{places}f} {c.upper()}"


def _name(charge: Mapping[str, Any]) -> str:
    """The customer's name as Stripe has it on the charge, or nothing. Never an email
    address: a name that holds one is not taken."""
    billing = charge.get("billing_details")
    raw = billing.get("name") if isinstance(billing, Mapping) else None
    if not isinstance(raw, str) or "@" in raw:
        return ""
    return _one_line(raw, NAME_MAX)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _page_of(text: str) -> Mapping[str, Any]:
    """A Stripe list page from the server's text, or StripeError."""
    try:
        page = json.loads(text)
    except (ValueError, RecursionError):
        raise StripeError from None
    if not isinstance(page, dict) or not isinstance(page.get("data"), list):
        raise StripeError
    return page


class Payments:
    name = NAME
    title = TITLE
    interval_s = INTERVAL_S

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        zone: Callable[[], str | None] = lambda: None,
        livemode: bool | None = None,
    ) -> None:
        self._registry = registry
        self._zone = zone
        # None: left out, so the server reads in the connection's own mode (live, or the
        # sandbox or test key it was given). True or False asks for that mode.
        self._livemode = livemode

    def _stripe(self) -> StripeRevenue | None:
        tool = self._registry.get(stripe_revenue.NAME)
        return tool if isinstance(tool, StripeRevenue) else None

    def enabled(self, settings: AttentionSettings) -> bool:
        # Connected is enough to be on: one that asks first still runs its check, so
        # its status can say why it reads nothing.
        return settings.on(NAME) and self._stripe() is not None

    def asks_first(self) -> bool:
        # The definition as the registry validated it: the user's word on the read.
        d = self._registry.definition(stripe_revenue.NAME)
        return d is None or not runs_unattended(d)

    async def check(self, ctx: WatchContext) -> list[Find]:
        stripe = self._stripe()
        if stripe is None:
            raise StripeError
        if self.asks_first():
            # Nothing is read: the user said Stripe asks first (reminders spec D5).
            raise AsksFirst
        now = ctx.now_ms
        if ctx.last_check_ms is None:
            # Just turned on: from this moment, never the backlog (D2).
            ctx.cursor = str(now)
            return []
        since = max(self._cursor(ctx), now - MAX_BACK_MS)
        charges = await self._list(stripe, "GetCharges", since)
        disputes = await self._list(stripe, "GetDisputes", since)
        found: dict[str, Find] = {}
        for c in charges:
            for f in self._charge(c, ctx.settings.payment_limit, now):
                found.setdefault(f.key, f)
        for d in disputes:
            opened = self._dispute(d, now)
            if opened is not None:
                found.setdefault(opened.key, opened)
        # Only once both lists are read: a failed check reads them again.
        ctx.cursor = str(max(since, now - LAG_MS))
        return list(found.values())

    @staticmethod
    def _cursor(ctx: WatchContext) -> int:
        try:
            return int(ctx.cursor) if ctx.cursor is not None else int(ctx.last_check_ms or 0)
        except ValueError:
            return int(ctx.last_check_ms or 0)

    async def _list(self, stripe: StripeRevenue, operation: str, since_ms: int) -> list[JSON]:
        """Every object created since `since_ms`, newest first, at most `MAX_PAGES`."""
        out: list[JSON] = []
        seen: set[str] = set()
        after = ""
        for _ in range(MAX_PAGES):
            params: JSON = {"created": {"gte": since_ms // 1000}, "limit": PAGE_SIZE}
            if after:
                params["starting_after"] = after
            args: JSON = {"stripe_api_operation_id": operation, "parameters": params}
            if self._livemode is not None:
                args["livemode"] = self._livemode
            reply = await stripe.api_read(args, CALL_TIMEOUT_S)
            if not reply.ok:
                # The kind only: the server's words could quote a customer.
                raise StripeError
            page = _page_of(reply.text)
            data = [o for o in page["data"] if isinstance(o, dict)]
            fresh = [o for o in data if o.get("id") not in seen]
            for o in fresh:
                if isinstance(o.get("id"), str):
                    seen.add(o["id"])
            out += fresh
            last = data[-1].get("id") if data else None
            if page.get("has_more") is not True or not fresh or not isinstance(last, str):
                return out
            after = last
        logger.info("payments: %s stopped at %d pages", operation, MAX_PAGES)
        return out

    def _when(self, created_s: int, now_ms: int) -> str:
        zone = self._zone() or "UTC"
        try:
            return when.due_words(created_s * 1000, zone, now_ms)
        except when.WhenError:
            return when.due_words(created_s * 1000, "UTC", now_ms)

    def _made(
        self,
        oid: str,
        kind: str,
        amount: int,
        currency: str,
        created: int,
        now: int,
        *,
        customer: str = "",
        extra: JSON | None = None,
    ) -> Find:
        money = amount_words(amount, currency)
        facts: JSON = {"kind": kind, "amount": money}
        if customer:
            facts["customer"] = customer
        facts["created"] = self._when(created, now)
        facts.update(extra or {})
        detail = f"{_KIND_WORDS[kind]} {money}" + (f" — {customer}" if customer else "")
        return Find(
            key=f"payment:{oid}:{kind}",
            title=_KIND_WORDS[kind],
            facts=facts,
            fresh_until_ms=now + FRESH_MS,
            detail=detail,
        )

    def _charge(self, c: Mapping[str, Any], limit: PaymentLimit | None, now: int) -> list[Find]:
        oid, amount, created = c.get("id"), _int(c.get("amount")), _int(c.get("created"))
        currency = c.get("currency")
        if (
            not isinstance(oid, str)
            or not _ID.fullmatch(oid)
            or amount is None
            or created is None
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Za-z]{3}", currency)
        ):
            return []
        currency = currency.lower()
        customer = _name(c)
        # Never the description: free text from whoever pays, kept from the model.
        extra: JSON = {}
        status = c.get("status")
        if status == "failed":
            code = c.get("failure_code")
            if isinstance(code, str) and _CODE.fullmatch(code):
                extra["failure"] = code
            return [
                self._made(
                    oid, "failed", amount, currency, created, now, customer=customer, extra=extra
                )
            ]
        if (
            limit is not None
            and status == "succeeded"
            and c.get("paid") is True
            and currency == limit.currency
            and amount >= limit.amount_minor
        ):
            return [
                self._made(
                    oid, "large", amount, currency, created, now, customer=customer, extra=extra
                )
            ]
        return []

    def _dispute(self, d: Mapping[str, Any], now: int) -> Find | None:
        oid, amount, created = d.get("id"), _int(d.get("amount")), _int(d.get("created"))
        currency = d.get("currency")
        if (
            not isinstance(oid, str)
            or not _ID.fullmatch(oid)
            or amount is None
            or created is None
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Za-z]{3}", currency)
        ):
            return None
        extra: JSON = {}
        reason = d.get("reason")
        if isinstance(reason, str) and _CODE.fullmatch(reason):
            extra["reason"] = reason
        # A dispute's charge may come expanded: its customer's name, if Stripe has one.
        charge = d.get("charge")
        customer = _name(charge) if isinstance(charge, Mapping) else ""
        return self._made(
            oid, "dispute", amount, currency.lower(), created, now, customer=customer, extra=extra
        )
