"""Month-to-date API usage, read back for the pill (BRN-4)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401
from trillion.api.runtime import OWNER
from trillion.brain.types import Usage
from trillion.persona import setup
from trillion.spend.types import Unit
from trillion.zones import month_start

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
    units: Unit = "tokens",
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
            units=units,
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
        "providers": [],
        "uncounted_before_caps": False,
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
        "provider_id": "anthropic",
        "unit": "tokens",
        "unpriced": 0,
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


def test_unpriced_characters_are_never_shown_as_tokens(make: Any) -> None:
    app = make()
    in_chicago(app)
    spend(app, NOW - MINUTE, cost=None, tokens=(500, 0, 0, 0))
    spend(
        app,
        NOW - MINUTE,
        provider="elevenlabs",
        model="eleven_flash_v2_5",
        cost=None,
        tokens=(1200, 0, 0, 0),
        units="chars",
    )
    usage = read(app)
    assert usage["unpriced_tokens"] == 500
    voice = next(r for r in usage["rows"] if r["unit"] == "chars")
    assert (voice["unpriced_tokens"], voice["unpriced"]) == (0, 1200)


# ── Caps and costs per provider (BRN-7, ONB-9) ───────────────────────────


from trillion.api.runtime import ListenSettings, SpeechSettings  # noqa: E402
from trillion.brain.config import ModelSettings  # noqa: E402
from trillion.spend import desk, prices  # noqa: E402
from trillion.spend.gate import PREF as CAPS_PREF  # noqa: E402
from trillion.spend.ledger import Row  # noqa: E402

SONNET = ModelSettings(kind="anthropic", model="claude-sonnet-5-5")
CAPS = {"soft": 20, "hard": 50, "warn_pct": 80}


def call(app: App, fn: Any, *args: Any) -> Any:
    portal = app.client.portal
    assert portal is not None
    return portal.call(fn, *args)


def set_caps(app: App, **per: Any) -> None:
    call(app, app.rt.preferences.put, OWNER, CAPS_PREF, per, NOW)
    call(app, app.rt.gate.reload_caps)


def recount(app: App) -> None:
    """The ledger reads the rows again, as it does at startup."""
    call(app, app.rt.ledger.load)


def on_model(app: App, settings: ModelSettings) -> None:
    app.rt.settings = settings
    app.rt.source = "settings"
    app.rt.provider = app.provider  # metered against what is chosen now


def providers(app: App) -> dict[str | None, Any]:
    return {(p["provider_id"] or p["leg"]): p for p in read(app)["providers"]}


def test_todays_fields_are_kept_and_nothing_set_up_lists_nothing(make: Any) -> None:
    app = make()
    in_chicago(app)
    usage = read(app)
    assert set(usage) == {
        "month",
        "since_ms",
        "rows",
        "total_cost_usd",
        "unpriced_tokens",
        "providers",
        "uncounted_before_caps",
    }
    assert usage["providers"] == []
    assert usage["uncounted_before_caps"] is False


def test_a_priced_model_shows_its_caps_its_split_and_what_it_costs(make: Any) -> None:
    app = make()
    in_chicago(app)
    on_model(app, SONNET)
    set_caps(app, anthropic=CAPS)
    spend(app, NOW - MINUTE, model="claude-sonnet-5-5", cost=10.0)
    call(
        app,
        partial(
            app.rt.conversations.record_usage,
            owner_id=OWNER,
            conversation_id=None,
            provider="anthropic",
            model="claude-sonnet-5-5",
            usage=Usage(input_tokens=10),
            cost_usd=7.0,
            now_ms=NOW - MINUTE,
            provider_id="anthropic",
            caller="background",
        ),
    )
    recount(app)
    (row,) = read(app)["providers"]
    hour = prices.hourly("anthropic", "claude-sonnet-5-5")
    assert hour is not None
    assert row == {
        "provider_id": "anthropic",
        "leg": "model",
        "name": "Anthropic",
        "model": "claude-sonnet-5-5",
        "spent": 17.0,
        "unit": "dollars",
        "soft": 20.0,
        "hard": 50.0,
        "warn_pct": 80,
        "state": "warned",
        "split": {"conversation": 10.0, "background": 7.0},
        "paused": [],
        "switched": None,
        "unestimated": None,
        "estimated": False,
        "estimate": (
            f"Roughly ${hour:.2f} an hour of talk on Sonnet 5.5 (about 40 short turns), "
            "plus routines."
        ),
        "pricing_url": "https://www.anthropic.com/pricing#api",
    }


def test_unpriced_units_nothing_can_estimate_are_reported(make: Any) -> None:
    app = make()
    in_chicago(app)
    custom = ModelSettings(kind="custom", model="m1", base_url="https://llm.example.com/v1")
    on_model(app, custom)
    set_caps(app, **{"custom:llm.example.com": CAPS})
    call(
        app,
        partial(
            app.rt.conversations.record_usage,
            owner_id=OWNER,
            conversation_id=None,
            provider="custom",
            model="m1",
            usage=Usage(input_tokens=3000, output_tokens=1000),
            cost_usd=None,
            now_ms=NOW - MINUTE,
            provider_id="custom:llm.example.com",
        ),
    )
    recount(app)
    row = providers(app)["custom:llm.example.com"]
    assert row["unestimated"] == {"units": 4000, "unit": "tokens"}
    assert row["spent"] == 0.0 and row["state"] == "ok"
    assert row["split"] == {"conversation": 0.0, "background": 0.0}
    assert row["estimate"].startswith("No price is known for m1 yet")
    assert row["pricing_url"] is None


def test_caps_counted_in_tokens_show_tokens_spent(make: Any) -> None:
    app = make()
    in_chicago(app)
    custom = ModelSettings(kind="custom", model="m1", base_url="https://llm.example.com/v1")
    on_model(app, custom)
    set_caps(app, **{"custom:llm.example.com": {"soft": 5000, "hard": 9000, "unit": "tokens"}})
    call(
        app,
        partial(
            app.rt.conversations.record_usage,
            owner_id=OWNER,
            conversation_id=None,
            provider="custom",
            model="m1",
            usage=Usage(input_tokens=3000, output_tokens=1000),
            cost_usd=None,
            now_ms=NOW - MINUTE,
            provider_id="custom:llm.example.com",
            caller="background",
        ),
    )
    recount(app)
    row = providers(app)["custom:llm.example.com"]
    assert (row["spent"], row["unit"], row["unestimated"]) == (4000, "tokens", None)
    assert row["split"] == {"conversation": 0, "background": 4000}


def test_a_local_model_and_the_computers_voice_cost_nothing_and_have_no_cap(make: Any) -> None:
    app = make()
    in_chicago(app)
    on_model(app, ModelSettings(kind="local", model="gemma", base_url="http://localhost:11434/v1"))
    app.rt.speech = SpeechSettings(kind="system")
    app.rt.listening = ListenSettings(kind="whisper", choice="base.en")
    found = providers(app)
    free = "Runs on this computer: no cost, no cap."
    assert set(found) == {"local", "voice", "hearing"}
    for row in found.values():
        assert row["estimate"] == free
        assert (row["soft"], row["hard"], row["state"]) == (None, None, "ok")
        assert row["pricing_url"] is None
    assert found["local"]["name"] == "A local model"
    assert found["voice"]["provider_id"] is None


def test_voice_and_hearing_are_listed_with_their_own_costs(make: Any) -> None:
    app = make()
    in_chicago(app)
    app.rt.speech = SpeechSettings(kind="elevenlabs", voice="v1")
    app.rt.listening = ListenSettings(kind="deepgram", choice="en")
    set_caps(app, elevenlabs=CAPS)
    found = providers(app)
    assert [p["provider_id"] for p in read(app)["providers"]] == ["elevenlabs", "deepgram"]
    assert found["elevenlabs"]["leg"] == "voice"
    assert found["elevenlabs"]["estimate"] == prices.cost_line("elevenlabs")
    assert found["elevenlabs"]["pricing_url"] == "https://elevenlabs.io/pricing/api"
    # No caps saved for it: none, said plainly by nulls.
    assert (found["deepgram"]["soft"], found["deepgram"]["hard"]) == (None, None)
    assert found["deepgram"]["leg"] == "hearing"


def test_providers_with_caps_or_spend_this_month_are_listed_too(make: Any) -> None:
    app = make()
    in_chicago(app)
    on_model(app, SONNET)
    set_caps(app, anthropic=CAPS, openai=CAPS)
    call(
        app,
        partial(
            app.rt.conversations.record_usage,
            owner_id=OWNER,
            conversation_id=None,
            provider="openai",
            model="gpt-6-sol",
            usage=Usage(input_tokens=10),
            cost_usd=1.0,
            now_ms=NOW - MINUTE,
            provider_id="custom:api.deepseek.com",
        ),
    )
    recount(app)
    ids = [p["provider_id"] for p in read(app)["providers"]]
    # The one in use first, then the rest by id.
    assert ids == ["anthropic", "custom:api.deepseek.com", "openai"]
    deepseek = providers(app)["custom:api.deepseek.com"]
    assert deepseek["name"] == "DeepSeek" and deepseek["model"] == "gpt-6-sol"


def test_a_switch_is_shown_with_its_end(make: Any) -> None:
    app = make()
    in_chicago(app)
    on_model(app, SONNET)
    set_caps(app, anthropic=CAPS)
    until = 1793509200000
    switch = desk.Switch(
        leg="model",
        to="local/gemma",
        label="gemma",
        month="2026-10",
        until_ms=until,
        settings=ModelSettings(
            kind="local", model="gemma", base_url="http://localhost:11434/v1"
        ).to_json(),
    )
    call(app, app.rt.preferences.put, OWNER, desk.SWITCH_PREF, {"anthropic": switch.to_json()}, NOW)
    call(app, app.rt.spend.load)
    found = providers(app)
    assert found["anthropic"]["switched"] == {
        "to": "local/gemma",
        "label": "gemma",
        "until_ms": until,
        # The banner's own words, in the user's timezone.
        "line": "Using gemma until Nov 1",
    }
    # What it was switched to is in use, so it is listed too.
    assert found["local"]["switched"] is None


def test_background_work_paused_at_the_hard_cap_is_listed_by_kind(make: Any) -> None:
    app = make()
    in_chicago(app)
    on_model(app, SONNET)
    set_caps(app, anthropic=CAPS)
    spend(app, NOW - MINUTE, model="claude-sonnet-5-5", cost=50.0)
    recount(app)
    row = providers(app)["anthropic"]
    assert row["state"] == "capped"
    assert row["paused"] == ["titles", "board"]
    app.rt.widgets_fresh = False
    assert providers(app)["anthropic"]["paused"] == ["titles"]


def test_caps_in_tokens_count_priced_tokens_in_the_split_too(make: Any) -> None:
    app = make()
    in_chicago(app)
    custom = ModelSettings(kind="custom", model="m1", base_url="https://llm.example.com/v1")
    on_model(app, custom)
    set_caps(app, **{"custom:llm.example.com": {"soft": 5000, "hard": 9000, "unit": "tokens"}})
    for cost, caller in ((0.01, "conversation"), (None, "background")):
        call(
            app,
            partial(
                app.rt.conversations.record_usage,
                owner_id=OWNER,
                conversation_id=None,
                provider="custom",
                model="m1",
                usage=Usage(input_tokens=3000, output_tokens=1000),
                cost_usd=cost,
                now_ms=NOW - MINUTE,
                provider_id="custom:llm.example.com",
                caller=caller,
            ),
        )
    recount(app)
    row = providers(app)["custom:llm.example.com"]
    assert (row["spent"], row["unestimated"]) == (8000, None)
    assert row["split"] == {"conversation": 4000, "background": 4000}


# ── Ending a switch early: POST /api/spend/switch/{provider_id}/end ─────


def _switched(app: App) -> None:
    in_chicago(app)
    on_model(app, SONNET)
    set_caps(app, anthropic=CAPS)
    switch = desk.Switch(
        leg="model",
        to="local/gemma",
        label="gemma",
        month="2026-10",
        until_ms=1793509200000,
        settings=ModelSettings(
            kind="local", model="gemma", base_url="http://localhost:11434/v1"
        ).to_json(),
    )
    call(app, app.rt.preferences.put, OWNER, desk.SWITCH_PREF, {"anthropic": switch.to_json()}, NOW)
    call(app, app.rt.spend.load)


END = "/api/spend/switch/anthropic/end"


def test_a_switch_ends_early_once_its_provider_has_budget_again(make: Any) -> None:
    app = make()
    _switched(app)
    got = app.client.post(END, json={}, headers=app.auth)
    assert got.status_code == 200
    assert got.json() == {"switches": []}
    assert app.rt.spend.switch("anthropic") is None
    assert call(app, app.rt.preferences.get, OWNER, desk.SWITCH_PREF) == {}
    # Nothing left to end.
    assert app.client.post(END, json={}, headers=app.auth).status_code == 404


def test_a_switch_is_not_ended_while_its_provider_is_still_capped(make: Any) -> None:
    app = make()
    _switched(app)
    app.rt.ledger.add(Row("anthropic", "conversation", "tokens", 0, 50.0, NOW))
    got = app.client.post(END, json={}, headers=app.auth)
    assert got.status_code == 409
    assert got.json() == {"error": "still_capped"}
    assert app.rt.spend.switch("anthropic") is not None


def test_ending_a_switch_needs_the_token_and_our_origin(make: Any) -> None:
    app = make()
    _switched(app)
    assert app.client.post(END, json={}).status_code == 401
    other = {**app.auth, "origin": "https://evil.example"}
    assert app.client.post(END, json={}, headers=other).status_code == 403
    assert app.rt.spend.switch("anthropic") is not None


def test_dollars_that_include_an_estimate_say_so(make: Any) -> None:
    app = make()
    in_chicago(app)
    gpt = ModelSettings(kind="openai", model="gpt-new")
    on_model(app, gpt)
    set_caps(app, openai=CAPS)
    call(
        app,
        partial(
            app.rt.conversations.record_usage,
            owner_id=OWNER,
            conversation_id=None,
            provider="openai",
            model="gpt-new",
            usage=Usage(input_tokens=3000, output_tokens=1000),
            cost_usd=None,
            now_ms=NOW - MINUTE,
            provider_id="openai",
        ),
    )
    recount(app)
    row = providers(app)["openai"]
    assert row["estimated"] is True and row["spent"] > 0


def test_spend_from_before_caps_on_an_openai_compatible_model_is_said_to_be_uncounted(
    make: Any,
) -> None:
    app = make()
    in_chicago(app)
    # As migration 017 left an old row: its provider class, and no address.
    call(
        app,
        partial(
            app.rt.conversations.record_usage,
            owner_id=OWNER,
            conversation_id=None,
            provider="openai-compatible",
            model="m1",
            usage=Usage(input_tokens=3000),
            cost_usd=None,
            now_ms=NOW - MINUTE,
            provider_id="openai-compatible",
        ),
    )
    assert read(app)["uncounted_before_caps"] is True
