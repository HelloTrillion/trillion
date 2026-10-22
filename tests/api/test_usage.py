"""Month-to-date API usage, read back for the pill (BRN-4)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401
from trillion.api.runtime import OWNER
from trillion.api.usage import month_start
from trillion.brain.types import Usage
from trillion.persona import setup

PATH = "/api/usage"
NOW = 1792670400000  # 2026-10-22 12:00 UTC
CHICAGO_OCT_1 = 1790830800000  # 2026-10-01 00:00 in America/Chicago (05:00 UTC)
MINUTE = 60_000


def spend(
    app: App,
    at: int,
    *,
    provider: str = "anthropic",
    model: str = "claude-test",
    cost: float | None = 0.5,
    tokens: tuple[int, int, int, int] = (1000, 200, 0, 0),
) -> None:
    portal = app.client.portal
    assert portal is not None
    store = app.rt.conversations
    if not portal.call(store.exists, "c1", OWNER):
        portal.call(store.create, "c1", OWNER, at)
    portal.call(
        partial(
            store.record_usage,
            owner_id=OWNER,
            conversation_id="c1",
            provider=provider,
            model=model,
            usage=Usage(
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cache_read_tokens=tokens[2],
                cache_write_tokens=tokens[3],
            ),
            cost_usd=cost,
            now_ms=at,
        )
    )


def in_chicago(app: App) -> None:
    app.rt.profile = setup.Profile(user_name="Sam", timezone="America/Chicago")
    app.rt.clock = lambda: NOW


def read(app: App) -> Any:
    got = app.client.get(PATH, headers=app.auth)
    assert got.status_code == 200
    return got.json()


def test_the_month_starts_at_midnight_on_the_first_in_the_users_timezone() -> None:
    assert month_start(NOW, "America/Chicago") == ("2026-10", CHICAGO_OCT_1)
    # 2026-12-31 14:00 UTC is already New Year's Day in Adelaide.
    assert month_start(1798725600000, "Australia/Adelaide") == ("2027-01", 1798723800000)


def test_with_no_timezone_the_month_follows_this_machine() -> None:
    local = datetime.fromtimestamp(NOW / 1000)
    first = datetime(local.year, local.month, 1)
    assert month_start(NOW, "") == (f"{first:%Y-%m}", int(first.timestamp() * 1000))


def test_reading_usage_needs_the_token_and_the_apps_own_page(make: Any) -> None:
    app = make()
    assert app.client.get(PATH).status_code == 401
    elsewhere = app.auth | {"origin": "http://evil.example"}
    assert app.client.get(PATH, headers=elsewhere).status_code == 403


def test_a_month_with_nothing_used_is_empty_not_zero(make: Any) -> None:
    app = make()
    in_chicago(app)
    assert read(app) == {
        "month": "2026-10",
        "since_ms": CHICAGO_OCT_1,
        "rows": [],
        "total_cost_usd": None,
        "unpriced_tokens": 0,
    }


def test_only_this_month_in_the_users_timezone_is_counted(make: Any) -> None:
    app = make()
    in_chicago(app)
    spend(app, CHICAGO_OCT_1 - MINUTE, cost=9.0)  # still September in Chicago
    spend(app, CHICAGO_OCT_1, cost=0.5)  # the first moment of the month counts
    spend(app, CHICAGO_OCT_1 + MINUTE, cost=0.25)
    usage = read(app)
    assert usage["total_cost_usd"] == 0.75
    assert [r["cost_usd"] for r in usage["rows"]] == [0.75]


def test_rows_are_grouped_by_provider_and_model_with_tokens_summed(make: Any) -> None:
    app = make()
    in_chicago(app)
    spend(app, NOW - 2 * MINUTE, cost=0.5, tokens=(1000, 200, 30, 4))
    spend(app, NOW - MINUTE, cost=0.7, tokens=(2000, 100, 0, 0))
    spend(app, NOW - MINUTE, model="claude-other", cost=0.1)
    rows = read(app)["rows"]
    assert rows[0] == {
        "provider": "anthropic",
        "model": "claude-test",
        "input_tokens": 3000,
        "output_tokens": 300,
        "cache_read_tokens": 30,
        "cache_write_tokens": 4,
        "cost_usd": 1.2,
        "unpriced_turns": 0,
        "unpriced_tokens": 0,
    }
    assert [r["model"] for r in rows] == ["claude-test", "claude-other"]


def test_an_unknown_cost_is_never_counted_as_zero(make: Any) -> None:
    app = make()
    in_chicago(app)
    spend(
        app,
        NOW - MINUTE,
        provider="custom",
        model="deepseek-chat",
        cost=None,
        tokens=(300_000, 40_000, 0, 0),
    )
    usage = read(app)
    assert usage["total_cost_usd"] is None
    assert usage["unpriced_tokens"] == 340_000
    assert usage["rows"][0]["cost_usd"] is None
    assert usage["rows"][0]["unpriced_turns"] == 1


def test_a_mixed_month_totals_priced_rows_and_counts_the_rest_as_unpriced(make: Any) -> None:
    app = make()
    in_chicago(app)
    spend(app, NOW - MINUTE, cost=1.2, tokens=(41_000, 9_000, 2_000, 0))
    spend(
        app,
        NOW - MINUTE,
        provider="custom",
        model="deepseek-chat",
        cost=None,
        tokens=(300_000, 40_000, 0, 0),
    )
    usage = read(app)
    assert usage["total_cost_usd"] == 1.2
    assert usage["unpriced_tokens"] == 340_000
    # Priced first, then by tokens: the biggest bill leads.
    assert [r["model"] for r in usage["rows"]] == ["claude-test", "deepseek-chat"]
