"""Caps and prices in Settings, the defaults a new key and an upgrade start with, and the
one note that says so (BRN-7, ONB-9, ONB-10, PER-11)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import SAMPLE_KEY, App, home, make, offers  # noqa: F401
from trillion.api.runtime import (
    MODEL_PREF,
    OWNER,
    SPEECH_PREF,
    SPEND_SEEDED_PREF,
    Runtime,
    open_runtime,
)
from trillion.brain.config import ModelSettings
from trillion.brain.types import Usage
from trillion.secrets.store import MemoryStore
from trillion.spend import prices
from trillion.spend.desk import KNOWN_PREF
from trillion.spend.gate import PREF as CAPS_PREF
from trillion.spend.gate import Caps
from trillion.spend.ledger import Row
from trillion.spend.metered import PREF as PRICES_PREF

DEFAULTS = {"soft": 20.0, "hard": 50.0, "warn_pct": 80, "unit": "dollars"}
TOKENS = {"soft": 5_000_000, "hard": 12_000_000, "warn_pct": 80, "unit": "tokens"}


def call(app: App, fn: Any, *args: Any) -> Any:
    portal = app.client.portal
    assert portal is not None
    return portal.call(fn, *args)


def stored(app: App, key: str) -> Any:
    return call(app, app.rt.preferences.get, OWNER, key)


def put(app: App, path: str, body: Any, **kw: Any) -> Any:
    return app.client.put(path, json=body, headers=kw.pop("headers", app.auth), **kw)


def known(app: App, *pids: str) -> None:
    """Providers with spend this month: caps may be set for them."""
    for pid in pids:
        app.rt.ledger.add(Row(pid, "conversation", prices.unit_of(pid), 1, None, app.rt.clock()))


# ── PUT /api/settings/spend/{provider_id} ────────────────────────────────


def test_caps_are_saved_and_the_gate_holds_to_them_at_once(make: Any) -> None:
    app = make()
    known(app, "anthropic")
    r = put(app, "/api/settings/spend/anthropic", {"soft": 30, "hard": 60, "warn_pct": 90})
    assert r.status_code == 200
    body = r.json()
    assert body["provider_id"] == "anthropic"
    assert (body["soft"], body["hard"], body["warn_pct"], body["unit"]) == (30, 60, 90, "dollars")
    assert stored(app, CAPS_PREF) == {
        "anthropic": {"soft": 30.0, "hard": 60.0, "warn_pct": 90, "unit": "dollars"}
    }
    assert app.rt.gate.caps("anthropic") == Caps(30.0, 60.0, 90)


def test_null_removes_a_cap_and_says_so(make: Any) -> None:
    app = make()
    known(app, "openai")
    r = put(app, "/api/settings/spend/openai", {"soft": None, "hard": None})
    assert r.status_code == 200
    assert (r.json()["soft"], r.json()["hard"]) == (None, None)
    # Kept as an entry: a removed cap is a choice, never filled in again by a default.
    assert stored(app, CAPS_PREF)["openai"]["hard"] is None
    assert put(app, "/api/settings/spend/openai", {"soft": 10, "hard": None}).status_code == 200
    assert app.rt.gate.caps("openai") == Caps(10.0, None, 80)


def test_other_providers_caps_are_left_as_they_were(make: Any) -> None:
    app = make()
    known(app, "anthropic", "openai")
    put(app, "/api/settings/spend/anthropic", {"soft": 1, "hard": 2})
    put(app, "/api/settings/spend/openai", {"soft": 3, "hard": 4})
    assert set(stored(app, CAPS_PREF)) == {"anthropic", "openai"}


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ({"soft": 60, "hard": 50}, 400, "hard_below_soft"),
        ({"soft": -1, "hard": 50}, 422, "bad_request"),
        ({"soft": 1, "hard": "50"}, 422, "bad_request"),
        ({"soft": True, "hard": 50}, 422, "bad_request"),
        ({"soft": 1, "hard": 50, "warn_pct": 0}, 422, "bad_request"),
        ({"soft": 1, "hard": 50, "warn_pct": 101}, 422, "bad_request"),
        ({"soft": 1, "hard": 50, "warn_pct": 80.5}, 422, "bad_request"),
        ({"soft": 1, "hard": 50, "unit": "euros"}, 422, "bad_request"),
        ({"soft": 1, "hard": 50, "unit": "chars"}, 400, "bad_unit"),
        ({"soft": 1, "hard": 1_000_000}, 400, "too_high"),
        ({"soft": 1}, 422, "bad_request"),
        ({"soft": 1, "hard": 50, "extra": 1}, 422, "bad_request"),
    ],
)
def test_caps_are_checked_before_anything_is_saved(
    make: Any, body: dict[str, Any], status: int, error: str
) -> None:
    app = make()
    known(app, "anthropic")
    r = put(app, "/api/settings/spend/anthropic", body)
    assert (r.status_code, r.json()["error"]) == (status, error)
    assert stored(app, CAPS_PREF) is None


def test_numbers_that_are_not_finite_are_refused(make: Any) -> None:
    app = make()
    known(app, "anthropic")
    for raw in ('{"soft": 1, "hard": Infinity}', '{"soft": NaN, "hard": 5}'):
        r = app.client.put(
            "/api/settings/spend/anthropic",
            content=raw,
            headers=app.auth | {"content-type": "application/json"},
        )
        assert r.status_code == 422, raw
    assert stored(app, CAPS_PREF) is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/settings/spend/custom:api.deepseek.com",
        "/api/settings/spend/custom%3Aapi.deepseek.com",
    ],
)
def test_a_provider_id_with_a_colon_and_dots_is_routed(make: Any, path: str) -> None:
    app = make()
    known(app, "custom:api.deepseek.com")
    r = put(app, path, {"soft": 5, "hard": 10})
    assert r.status_code == 200, r.text
    assert r.json()["provider_id"] == "custom:api.deepseek.com"
    assert app.rt.gate.caps("custom:api.deepseek.com") == Caps(5.0, 10.0, 80)


def test_an_unpriced_provider_can_be_capped_in_its_own_unit(make: Any) -> None:
    app = make()
    known(app, "custom:llm.example.com", "openai-voice:tts.example.com")
    ok = put(app, "/api/settings/spend/custom:llm.example.com", {"soft": 1e6, "hard": 2e6})
    # With no unit given, a new entry takes the unit of its defaults.
    assert ok.json()["unit"] == "tokens"
    voice = put(
        app, "/api/settings/spend/openai-voice:tts.example.com", {"soft": 1, "hard": 2}
    ).json()
    assert voice["unit"] == "chars"
    # Dollars only once something can price it (M6).
    no_price = put(
        app,
        "/api/settings/spend/custom:llm.example.com",
        {"soft": 1, "hard": 2, "unit": "dollars"},
    )
    assert (no_price.status_code, no_price.json()["error"]) == (400, "no_price")
    put(
        app,
        "/api/settings/spend/prices",
        {"custom:llm.example.com": {"*": {"input": 1, "output": 2}}},
    )
    back = put(
        app,
        "/api/settings/spend/custom:llm.example.com",
        {"soft": 1, "hard": 2, "unit": "dollars"},
    )
    assert back.json()["unit"] == "dollars"
    # Once given, the unit stays when the next change leaves it out.
    again = put(app, "/api/settings/spend/custom:llm.example.com", {"soft": 3, "hard": 4})
    assert again.json()["unit"] == "dollars"


@pytest.mark.parametrize(
    ("pid", "error"),
    [
        ("local", "never_capped"),
        ("nobody", "unknown_provider"),
        ("custom:", "unknown_provider"),
        ("custom:Bad Host", "unknown_provider"),
        ("prices.extra", "unknown_provider"),
        # A provider that is not set up, has no caps and has spent nothing.
        ("custom:never.example.com", "unknown_provider"),
    ],
)
def test_only_a_provider_that_can_be_capped_takes_caps(make: Any, pid: str, error: str) -> None:
    app = make()
    r = put(app, f"/api/settings/spend/{pid}", {"soft": 1, "hard": 2})
    assert (r.status_code, r.json()["error"]) == (400, error)


@pytest.mark.parametrize("path", ["/api/settings/spend/anthropic", "/api/settings/spend/prices"])
def test_spend_settings_need_the_token_and_the_apps_own_page(make: Any, path: str) -> None:
    app = make()
    body = {"soft": 1, "hard": 2} if path.endswith("anthropic") else {}
    assert app.client.put(path, json=body).status_code == 401
    wrong = {"authorization": "Bearer nope"}
    assert app.client.put(path, json=body, headers=wrong).status_code == 401
    evil = app.auth | {"origin": "https://evil.example"}
    assert app.client.put(path, json=body, headers=evil).status_code == 403
    form = app.client.put(path, data={"soft": "1"}, headers=app.auth)
    assert form.status_code == 415
    assert stored(app, CAPS_PREF) is None and stored(app, PRICES_PREF) is None
    assert app.client.get("/api/settings/spend/prices").status_code == 401


# ── PUT /api/settings/spend/prices ───────────────────────────────────────


def test_user_prices_are_saved_and_price_the_next_call(make: Any) -> None:
    app = make()
    mine = {
        "custom:llm.example.com": {"m1": {"input": 1.5, "output": 6}},
        "elevenlabs": {"*": {"unit": "chars", "per_unit": 0.02}},
    }
    r = put(app, "/api/settings/spend/prices", mine)
    assert r.status_code == 200
    assert r.json() == {"prices": mine}
    assert stored(app, PRICES_PREF) == mine
    rates = app.rt.meter.prices.rates()
    assert rates[("custom:llm.example.com", "m1")].output == 6.0
    assert app.client.get("/api/settings/spend/prices", headers=app.auth).json() == {"prices": mine}
    # And the estimate reads them.
    assert prices.cost_line("custom:llm.example.com", "m1", rates).startswith("Roughly $")
    # An empty map clears them.
    assert put(app, "/api/settings/spend/prices", {}).json() == {"prices": {}}
    assert app.rt.meter.prices.rates() == {}


@pytest.mark.parametrize(
    "body",
    [
        {"openai": {"gpt-x": {"input": 1}}},  # no output rate
        {"openai": {"gpt-x": {"input": -1, "output": 1}}},
        {"openai": {"gpt-x": {"input": "1", "output": 1}}},
        {"openai": {"gpt-x": {"input": 1, "output": 1, "per_minute": 2}}},
        {"openai": {"gpt-x": {"unit": "chars", "per_unit": 1}}},  # a model is per token
        {"elevenlabs": {"*": {"input": 1, "output": 1}}},  # a voice is per character
        {"openai": {"": {"input": 1, "output": 1}}},
        {"openai": ["gpt-x"]},
        {"local": {"gemma": {"input": 1, "output": 1}}},
        {"nobody": {"m": {"input": 1, "output": 1}}},
    ],
)
def test_a_price_it_would_drop_is_refused_and_nothing_is_saved(make: Any, body: Any) -> None:
    app = make()
    r = put(app, "/api/settings/spend/prices", body)
    assert r.status_code in (400, 422), r.text
    assert stored(app, PRICES_PREF) is None


# ── Saving a key gives its provider the defaults, if it has no caps ──────


def test_saving_a_model_gives_its_provider_the_defaults(make: Any, offers: list) -> None:
    app = make(model=False)
    r = put(
        app, "/api/settings/model", {"kind": "anthropic", "model": "model-a", "api_key": SAMPLE_KEY}
    )
    assert r.status_code == 200
    assert stored(app, CAPS_PREF) == {"anthropic": DEFAULTS}
    assert app.rt.gate.caps("anthropic") == Caps(20.0, 50.0, 80)


def test_saving_a_key_never_overwrites_caps_already_there(make: Any, offers: list) -> None:
    app = make(model=False)
    known(app, "anthropic")
    put(app, "/api/settings/spend/anthropic", {"soft": None, "hard": 5})
    put(
        app, "/api/settings/model", {"kind": "anthropic", "model": "model-a", "api_key": SAMPLE_KEY}
    )
    assert stored(app, CAPS_PREF) == {
        "anthropic": {"soft": None, "hard": 5.0, "warn_pct": 80, "unit": "dollars"}
    }


def test_an_unpriced_model_starts_with_caps_in_tokens_and_a_local_one_with_none(
    make: Any, offers: list
) -> None:
    app = make(model=False)
    custom = {"kind": "custom", "model": "model-a", "base_url": "https://llm.example.com/v1"}
    assert put(app, "/api/settings/model", custom | {"api_key": SAMPLE_KEY}).status_code == 200
    local = {"kind": "local", "model": "model-a", "base_url": "http://localhost:11434/v1"}
    assert put(app, "/api/settings/model", local).status_code == 200
    assert stored(app, CAPS_PREF) == {"custom:llm.example.com": TOKENS}


def test_saving_a_voice_or_hearing_gives_it_the_defaults(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.voice.providers.deepgram import CHOICES, Deepgram
    from trillion.voice.providers.elevenlabs import ElevenLabs
    from trillion.voice.types import Choice, Voice

    async def voices(self: ElevenLabs) -> list[Voice]:
        return [Voice(id="v1", name="Ada")]

    async def choices(self: Deepgram) -> list[Choice]:
        return list(CHOICES)

    monkeypatch.setattr(ElevenLabs, "voices", voices)
    monkeypatch.setattr(Deepgram, "choices", choices)
    app = make(model=False)
    put(app, "/api/settings/speech", {"kind": "elevenlabs", "voice": "v1", "api_key": SAMPLE_KEY})
    assert stored(app, CAPS_PREF) == {"elevenlabs": DEFAULTS}
    put(app, "/api/settings/listening", {"kind": "deepgram", "choice": "en", "api_key": SAMPLE_KEY})
    assert stored(app, CAPS_PREF) == {"elevenlabs": DEFAULTS, "deepgram": DEFAULTS}
    # The computer's own voice costs nothing and gets no caps.
    put(app, "/api/settings/speech", {"kind": "system"})
    assert set(stored(app, CAPS_PREF)) == {"elevenlabs", "deepgram"}


# ── What a key step shows before the key is saved (ONB-9, ONB-10) ────────


def test_the_model_step_shows_each_kinds_cost_and_default_caps(make: Any) -> None:
    app = make(model=False)
    costs = app.client.get("/api/settings/model", headers=app.auth).json()["costs"]
    assert set(costs) == {"anthropic", "openai", "custom", "local"}
    assert costs["anthropic"]["provider_id"] == "anthropic"
    assert costs["anthropic"]["estimate"].startswith("Roughly $")
    assert costs["anthropic"]["pricing_url"] == "https://www.anthropic.com/pricing#api"
    assert costs["anthropic"]["caps"] == DEFAULTS
    assert costs["local"]["estimate"] == "Runs on this computer: no cost, no cap."
    assert costs["local"]["caps"] is None
    # A custom address is known only once it is typed: the model list says then.
    assert costs["custom"]["provider_id"] is None and costs["custom"]["caps"] is None


def test_the_model_list_says_what_each_model_costs(make: Any, offers: list) -> None:
    app = make(model=False)
    known(app, "custom:llm.example.com")
    put(app, "/api/settings/spend/custom:llm.example.com", {"soft": 1, "hard": 2})
    body = {"kind": "custom", "base_url": "https://llm.example.com/v1", "api_key": SAMPLE_KEY}
    found = app.client.post("/api/settings/model/models", json=body, headers=app.auth).json()
    assert found["models"][0]["estimate"] == prices.cost_line("custom:llm.example.com", "model-a")
    cost = found["cost"]
    assert cost["provider_id"] == "custom:llm.example.com"
    # Caps already saved are what the step shows.
    assert cost["caps"] == {"soft": 1.0, "hard": 2.0, "warn_pct": 80, "unit": "tokens"}
    assert app.secrets.values() == []  # still nothing saved


def test_voice_and_hearing_steps_show_each_options_cost(make: Any) -> None:
    app = make(model=False)
    speech = app.client.get("/api/settings/speech", headers=app.auth).json()["options"]
    by_kind = {o["kind"]: o["cost"] for o in speech}
    assert by_kind["system"]["estimate"] == "Runs on this computer: no cost, no cap."
    assert by_kind["elevenlabs"]["estimate"] == prices.cost_line("elevenlabs")
    assert by_kind["elevenlabs"]["caps"] == DEFAULTS
    hearing = app.client.get("/api/settings/listening", headers=app.auth).json()["options"]
    by_kind = {o["kind"]: o["cost"] for o in hearing}
    assert by_kind["whisper"]["caps"] is None
    assert by_kind["deepgram"]["pricing_url"] == "https://deepgram.com/pricing"
    assert by_kind["deepgram"]["estimate"].startswith("Roughly $")


# ── Existing installs: the defaults once, and one note ───────────────────


def _secrets() -> MemoryStore:
    secrets = MemoryStore()
    for name in ("anthropic/api_key", "elevenlabs/api_key"):
        secrets.set(name, "test-key")
    return secrets


async def _open(tmp_path: Path, secrets: MemoryStore) -> Runtime:
    return await open_runtime(
        share_rules=lambda _p: None,
        granted_roots=(),
        persona=tmp_path,
        secrets=secrets,
        env={},
    )


async def _before_caps(tmp_path: Path, secrets: MemoryStore, **prefs: Any) -> None:
    """An install from before spend caps: its settings, and no sign it was seeded."""
    rt = await _open(tmp_path, secrets)
    for key in (CAPS_PREF, SPEND_SEEDED_PREF, "spend_told"):
        await rt.preferences.put(OWNER, key, None, rt.clock())
    for key, value in prefs.items():
        await rt.preferences.put(OWNER, key, value, rt.clock())
    await rt.close()


@pytest.fixture
def where(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    return tmp_path


async def test_the_upgrade_gives_every_provider_in_use_the_defaults(where: Path) -> None:
    secrets = _secrets()
    gpt = ModelSettings(kind="openai", model="gpt-6-sol")
    await _before_caps(
        where,
        secrets,
        **{
            MODEL_PREF: ModelSettings(kind="anthropic", model="claude-sonnet-5-5").to_json(),
            SPEECH_PREF: {"kind": "elevenlabs", "voice": "v1"},
            KNOWN_PREF: {"openai": gpt.to_json()},
        },
    )
    rt = await _open(where, secrets)
    try:
        # Only what is in use: a model known from before is given caps when it is
        # chosen again, or switched to.
        assert await rt.preferences.get(OWNER, CAPS_PREF) == {
            "anthropic": DEFAULTS,
            "elevenlabs": DEFAULTS,
        }
        assert rt.gate.caps("anthropic") == Caps(20.0, 50.0, 80)
        assert await rt.preferences.get(OWNER, SPEND_SEEDED_PREF) is True
        ((key, note),) = rt.spend.notes([])
        assert key == "once:caps_on"
        assert note.startswith("[host] Spending limits are now on")
        assert "soft cap" not in note and "hard cap" not in note
        for words in (
            "Anthropic: a warning at $20 (an early heads-up at 80% of it) and a stop at $50",
            "Settings, under Usage and limits",
            "ElevenLabs",
            "in your own words",
        ):
            assert words in note
    finally:
        await rt.close()


async def test_the_note_says_once_that_earlier_openai_compatible_use_is_not_counted(
    where: Path,
) -> None:
    secrets = _secrets()
    await _before_caps(
        where,
        secrets,
        **{MODEL_PREF: ModelSettings(kind="anthropic", model="claude-sonnet-5-5").to_json()},
    )
    rt = await _open(where, secrets)
    try:
        ((_key, note),) = rt.spend.notes([])
        assert "not counted" not in note
    finally:
        await rt.close()
    # Again, on an install that used an OpenAI-compatible model earlier this month.
    rt = await _open(where, secrets)
    try:
        await rt.conversations.record_usage(
            owner_id=OWNER,
            conversation_id=None,
            provider="openai-compatible",
            model="m1",
            usage=Usage(input_tokens=3000),
            cost_usd=None,
            now_ms=rt.clock() - 1000,
            provider_id="openai-compatible",
        )
    finally:
        await rt.close()
    await _before_caps(where, secrets)
    rt = await _open(where, secrets)
    try:
        ((_key, note),) = rt.spend.notes([])
        assert (
            "Use of OpenAI-compatible models earlier this month, before the limits were on, "
            "is not counted toward them."
        ) in note
    finally:
        await rt.close()


async def test_the_upgrade_never_overwrites_caps_and_seeds_only_once(where: Path) -> None:
    secrets = _secrets()
    mine = {"soft": None, "hard": 5.0, "warn_pct": 50, "unit": "dollars"}
    await _before_caps(
        where,
        secrets,
        **{
            MODEL_PREF: ModelSettings(kind="anthropic", model="claude-sonnet-5-5").to_json(),
            CAPS_PREF: {"anthropic": mine},
        },
    )
    rt = await _open(where, secrets)
    try:
        # Already capped: kept as the user set it, and nothing new to tell.
        assert await rt.preferences.get(OWNER, CAPS_PREF) == {"anthropic": mine}
        assert [k for k, _ in rt.spend.notes([])] == []
        # Removed later, as Settings removes them: the next start does not give them back.
        await rt.write_caps("anthropic", Caps(None, None, 80))
    finally:
        await rt.close()
    rt = await _open(where, secrets)
    try:
        assert await rt.preferences.get(OWNER, CAPS_PREF) == {
            "anthropic": {"soft": None, "hard": None, "warn_pct": 80, "unit": "dollars"}
        }
        assert rt.gate.caps("anthropic") == Caps(None, None, 80)
    finally:
        await rt.close()


async def test_a_model_set_by_the_environment_later_is_capped_on_its_next_start(
    where: Path,
) -> None:
    rt = await _open(where, MemoryStore())
    try:
        assert await rt.preferences.get(OWNER, CAPS_PREF) is None
    finally:
        await rt.close()
    rt = await open_runtime(
        share_rules=lambda _p: None,
        granted_roots=(),
        persona=where,
        secrets=MemoryStore(),
        env={"TRILLION_ANTHROPIC_API_KEY": "test-key"},
    )
    try:
        assert rt.source == "env"
        assert await rt.preferences.get(OWNER, CAPS_PREF) == {"anthropic": DEFAULTS}
        assert rt.gate.caps("anthropic") == Caps(20.0, 50.0, 80)
        # The note is for the upgrade only: this install had nothing to upgrade.
        assert rt.spend.notes([]) == []
    finally:
        await rt.close()


async def test_the_note_rides_with_one_turn_and_never_again(where: Path) -> None:
    secrets = _secrets()
    await _before_caps(
        where,
        secrets,
        **{MODEL_PREF: ModelSettings(kind="anthropic", model="claude-sonnet-5-5").to_json()},
    )
    rt = await _open(where, secrets)
    try:
        keys = [k for k, _ in rt.spend.notes([])]
        assert keys == ["once:caps_on"]
        await rt.spend.said(keys)
        assert rt.spend.notes([]) == []
    finally:
        await rt.close()
    rt = await _open(where, secrets)
    try:
        assert rt.spend.notes([]) == []
    finally:
        await rt.close()


async def test_a_fresh_install_is_marked_seeded_with_nothing_to_tell(where: Path) -> None:
    rt = await _open(where, MemoryStore())
    try:
        assert await rt.preferences.get(OWNER, SPEND_SEEDED_PREF) is True
        assert await rt.preferences.get(OWNER, CAPS_PREF) is None
        assert rt.spend.notes([]) == []
    finally:
        await rt.close()


async def test_the_caps_are_saved_even_if_the_note_cannot_be(
    where: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.spend.desk import SpendDesk

    secrets = _secrets()
    await _before_caps(
        where,
        secrets,
        **{MODEL_PREF: ModelSettings(kind="anthropic", model="claude-sonnet-5-5").to_json()},
    )

    async def broken(self: SpendDesk, name: str, note: str) -> None:
        raise OSError("disk")

    monkeypatch.setattr(SpendDesk, "tell_once", broken)
    rt = await _open(where, secrets)
    try:
        assert (await rt.preferences.get(OWNER, CAPS_PREF))["anthropic"] == DEFAULTS
    finally:
        await rt.close()


# ── Review fixes: prices move caps to dollars, one writer, bounds ────────


def test_a_price_moves_caps_still_at_their_unit_defaults_to_dollars(make: Any) -> None:
    app = make()
    call(app, app.rt.seed_caps, ["custom:llm.example.com", "custom:other.example.com"])
    known(app, "custom:other.example.com")
    put(app, "/api/settings/spend/custom:other.example.com", {"soft": 10, "hard": 20})
    mine = {
        "custom:llm.example.com": {"m1": {"input": 1, "output": 2}},
        "custom:other.example.com": {"m1": {"input": 1, "output": 2}},
    }
    assert put(app, "/api/settings/spend/prices", mine).status_code == 200
    caps = stored(app, CAPS_PREF)
    assert caps["custom:llm.example.com"] == DEFAULTS
    # Caps the user chose are never changed.
    assert caps["custom:other.example.com"]["unit"] == "tokens"
    assert app.rt.gate.caps("custom:llm.example.com") == Caps(20.0, 50.0, 80)


def test_openrouter_starts_in_dollars(make: Any) -> None:
    app = make()
    call(app, app.rt.seed_caps, ["custom:openrouter.ai"])
    assert stored(app, CAPS_PREF) == {"custom:openrouter.ai": DEFAULTS}


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({f"custom:h{i}.example.com": {} for i in range(51)}, "too_many"),
        ({"openai": {"gpt-x": {"input": 10**400, "output": 1}}}, "bad_price"),
        ({"openai": {"gpt-x": {"input": 1e7, "output": 1}}}, "bad_price"),
        ({"openai": {"gpt-x": {"input": 0, "output": 0}}}, "zero_price"),
        ({"custom:openrouter.ai": {"*": {"input": 0, "output": 0}}}, "zero_price"),
    ],
)
def test_prices_are_bounded(make: Any, body: Any, error: str) -> None:
    app = make()
    r = put(app, "/api/settings/spend/prices", body)
    assert (r.status_code, r.json()["error"]) == (400, error)
    assert stored(app, PRICES_PREF) is None


def test_a_voice_on_a_plan_may_be_free(make: Any) -> None:
    app = make()
    body = {"elevenlabs": {"*": {"unit": "chars", "per_unit": 0}}}
    assert put(app, "/api/settings/spend/prices", body).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        # Would move its caps from tokens to dollars, then count nothing.
        {"custom:llm.example.com": {"m1": {"input": 0, "output": 0}}},
        {"openai-voice:tts.example.com": {"*": {"unit": "chars", "per_unit": 0}}},
    ],
)
def test_a_free_price_that_would_count_nothing_is_refused(make: Any, body: Any) -> None:
    app = make()
    call(app, app.rt.seed_caps, list(body))
    r = put(app, "/api/settings/spend/prices", body)
    assert (r.status_code, r.json()["error"]) == (400, "zero_price")
    assert stored(app, PRICES_PREF) is None
    (pid,) = body
    assert stored(app, CAPS_PREF)[pid]["unit"] != "dollars"


def test_caps_are_bounded(make: Any) -> None:
    app = make()
    known(app, "anthropic")
    for body in ({"soft": 1, "hard": 1e10}, {"soft": 10**400, "hard": None}):
        r = put(app, "/api/settings/spend/anthropic", body)
        assert r.status_code == 422, body
    assert stored(app, CAPS_PREF) is None


def test_a_malformed_preference_keeps_every_set_up_provider_at_its_defaults(
    make: Any,
) -> None:
    from trillion.api.runtime import SpeechSettings

    app = make()
    app.rt.settings = ModelSettings(kind="anthropic", model="claude-sonnet-5-5")
    app.rt.source = "settings"
    app.rt.speech = SpeechSettings(kind="elevenlabs", voice="v1")
    call(app, app.rt.preferences.put, OWNER, CAPS_PREF, "garbage", 0)
    call(app, app.rt.gate.reload_caps)
    assert put(app, "/api/settings/spend/anthropic", {"soft": 1, "hard": 2}).status_code == 200
    assert stored(app, CAPS_PREF) == {
        "anthropic": {"soft": 1.0, "hard": 2.0, "warn_pct": 80, "unit": "dollars"},
        "elevenlabs": DEFAULTS,
    }


@pytest.fixture
async def rt(where: Path) -> Any:
    runtime = await _open(where, _secrets())
    runtime.settings = ModelSettings(kind="anthropic", model="claude-sonnet-5-5")
    runtime.source = "settings"
    yield runtime
    await runtime.close()


async def test_a_raise_on_the_card_goes_through_the_one_writer(rt: Runtime) -> None:
    from trillion.api.runtime import SpeechSettings

    rt.speech = SpeechSettings(kind="elevenlabs", voice="v1")
    await rt.preferences.put(OWNER, CAPS_PREF, "garbage", rt.clock())
    await rt.gate.reload_caps()
    await rt.spend._raise("anthropic", 75.0)
    caps = await rt.preferences.get(OWNER, CAPS_PREF)
    assert caps["anthropic"]["hard"] == 75.0
    assert caps["elevenlabs"] == DEFAULTS


async def test_a_raise_and_a_settings_change_at_once_both_hold(rt: Runtime) -> None:
    await rt.preferences.put(OWNER, CAPS_PREF, {"anthropic": DEFAULTS}, rt.clock())
    await rt.gate.reload_caps()
    await asyncio.gather(
        rt.spend._raise("anthropic", 75.0),
        rt.write_caps("openai", Caps(1.0, 2.0, 80)),
        rt.write_caps("elevenlabs", Caps(3.0, 4.0, 80)),
    )
    caps = await rt.preferences.get(OWNER, CAPS_PREF)
    assert caps["anthropic"]["hard"] == 75.0
    assert caps["openai"]["hard"] == 2.0 and caps["elevenlabs"]["hard"] == 4.0


def test_a_voice_and_a_model_at_one_host_are_told_apart() -> None:
    from trillion.spend.desk import friendly

    assert friendly("custom:x.example.com") == "x.example.com"
    assert friendly("openai-voice:x.example.com") == "x.example.com (voice)"
    assert friendly("openai-voice:api.openai.com") == "OpenAI (voice)"


async def test_what_a_switch_puts_in_use_gets_the_defaults(rt: Runtime) -> None:
    from trillion.spend import desk

    target = ModelSettings(kind="custom", model="m1", base_url="https://api.deepseek.com/v1")
    switch = desk.Switch(
        leg="model",
        to="deepseek/m1",
        label="m1",
        month=rt.spend.month(),
        until_ms=rt.spend.until(),
        settings=target.to_json(),
    )
    await rt.preferences.put(OWNER, desk.SWITCH_PREF, {"anthropic": switch.to_json()}, rt.clock())
    await rt.spend.load()
    await rt._spend_changed([], frozenset({"model"}))
    caps = await rt.preferences.get(OWNER, CAPS_PREF)
    assert caps["custom:api.deepseek.com"] == DEFAULTS


# ── A removed price puts caps back on units, never left counting nothing ──


def test_removing_the_last_price_puts_default_caps_back_on_units(make: Any) -> None:
    app = make()
    pid = "custom:llm.example.com"
    call(app, app.rt.seed_caps, [pid])
    put(app, "/api/settings/spend/prices", {pid: {"m1": {"input": 1, "output": 2}}})
    assert stored(app, CAPS_PREF)[pid] == DEFAULTS
    assert put(app, "/api/settings/spend/prices", {}).status_code == 200
    assert stored(app, CAPS_PREF)[pid] == TOKENS
    assert app.rt.gate.caps(pid).unit == "tokens"


def test_removing_a_price_keeps_chosen_dollar_caps_and_reports_what_is_uncounted(
    make: Any,
) -> None:
    app = make()
    pid = "custom:llm.example.com"
    known(app, pid)
    put(app, "/api/settings/spend/prices", {pid: {"m1": {"input": 1, "output": 2}}})
    assert (
        put(
            app, f"/api/settings/spend/{pid}", {"soft": 7, "hard": 9, "unit": "dollars"}
        ).status_code
        == 200
    )
    put(app, "/api/settings/spend/prices", {})
    caps = stored(app, CAPS_PREF)[pid]
    assert (caps["soft"], caps["hard"], caps["unit"]) == (7.0, 9.0, "dollars")
    app.rt.ledger.add(Row(pid, "conversation", "tokens", 500, None, app.rt.clock()))
    # Counted toward nothing, and said so, so Settings can ask for a price again.
    assert app.rt.gate.unestimated(pid) == (501, "tokens")


def test_a_provider_still_priced_another_way_keeps_dollars(make: Any) -> None:
    app = make()
    call(app, app.rt.seed_caps, ["openai"])
    put(app, "/api/settings/spend/prices", {"openai": {"gpt-x": {"input": 1, "output": 2}}})
    put(app, "/api/settings/spend/prices", {})
    assert stored(app, CAPS_PREF)["openai"] == DEFAULTS


# ── A price given mid-month counts what came before it ───────────────────


def test_a_price_counts_tokens_used_before_it_toward_the_dollar_caps(make: Any) -> None:
    app = make()
    pid = "custom:llm.example.com"
    call(app, app.rt.seed_caps, [pid])
    app.rt.ledger.add(Row(pid, "conversation", "tokens", 2_000_000, None, app.rt.clock()))
    assert app.rt.gate.unestimated(pid) is None  # counted in tokens, its own unit
    put(app, "/api/settings/spend/prices", {pid: {"m1": {"input": 1, "output": 22}}})
    assert app.rt.gate.caps(pid).unit == "dollars"
    # (20 * $1 + $22) / 21 = $2 a million: the 2M tokens from before are $4.
    assert app.rt.gate.counted(pid) == pytest.approx(4.0)
    assert app.rt.gate.unestimated(pid) is None
    (row,) = [
        p
        for p in app.client.get("/api/usage", headers=app.auth).json()["providers"]
        if p["provider_id"] == pid
    ]
    assert row["unestimated"] is None
    assert row["spent"] == pytest.approx(4.0)


# ── A card's switch never sends one host's key to another ────────────────


def test_a_card_offers_only_the_custom_host_the_key_was_saved_for(make: Any, offers: list) -> None:
    app = make()
    for host, key in (("a.example.com", "key-for-a"), ("b.example.com", "key-for-b")):
        body = {"kind": "custom", "model": "model-a", "base_url": f"https://{host}/v1"}
        r = app.client.put("/api/settings/model", json=body | {"api_key": key}, headers=app.auth)
        assert r.status_code == 200, r.json()
    # Back on Anthropic: both custom hosts are known, one key slot holds B's key.
    app.rt.secrets.set("anthropic/api_key", "test-key")
    body = {"kind": "anthropic", "model": "model-a"}
    assert app.client.put("/api/settings/model", json=body, headers=app.auth).status_code == 200
    choices = call(app, app.rt.model_choices, "anthropic")
    assert [pid for pid, _ in choices] == ["custom:b.example.com"]
    # A key saved before the address was recorded fits only the custom model in use:
    # with none in use, no custom model is offered.
    call(app, app.rt.preferences.put, OWNER, "custom_key_for", None, 0)
    assert call(app, app.rt.model_choices, "anthropic") == []
