"""Your conversation at the spend caps (BRN-7, BRN-8): at the hard cap she finishes what
is in flight, says so in one line from the app, and offers a card; the card is
answered only by a tap from the session it was sent to; a switch lasts until the 1st;
the soft cap is mentioned once in her words, and the warning comes once."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import App, home, make  # noqa: F401
from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say, use
from tests.voice.fakes import FakeSTT
from tests.voice.test_speaker import FakeVoice
from trillion.api import runtime as runtime_module
from trillion.api import speech
from trillion.api.runtime import (
    MODEL_PREF,
    OWNER,
    ListenSettings,
    Runtime,
    SpeechSettings,
    open_runtime,
)
from trillion.api.session import Session
from trillion.brain.config import ModelSettings
from trillion.brain.types import ContextBlock, ModelEvent, Msg, TextBlock
from trillion.persona.prompt import capped
from trillion.secrets.store import MemoryStore
from trillion.spend import desk
from trillion.spend.gate import PREF as CAPS_PREF
from trillion.spend.ledger import Row
from trillion.zones import month_start, next_month_start

ANTHROPIC = ModelSettings(kind="anthropic", model="claude-test")
GEMMA = ModelSettings(kind="local", model="gemma", base_url="http://localhost:11434/v1")
GPT = ModelSettings(kind="openai", model="gpt-test")
CAPS = {"soft": 20, "hard": 50, "warn_pct": 80}
MODEL_LINE = (
    "I've reached the $50 cap for Anthropic this month, so I've stopped before spending more."
)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech, "RETRY_S", 0)


@pytest.fixture(autouse=True)
def no_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none() -> str | None:
        return None

    monkeypatch.setattr(runtime_module, "whisper_choice", none)


def _secrets() -> MemoryStore:
    secrets = MemoryStore()
    for name in ("anthropic/api_key", "openai/api_key", "elevenlabs/api_key", "deepgram/api_key"):
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


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    runtime = await _open(tmp_path, _secrets())
    yield runtime
    await runtime.close()


async def on_model(rt: Runtime, settings: ModelSettings, provider: Any) -> None:
    """Choose `settings` as the user would, then put a stand-in in its place: the
    stand-in counts against the cap of what was chosen."""
    await rt.preferences.put(OWNER, MODEL_PREF, settings.to_json(), rt.clock())
    await rt.use_model(settings)
    rt.provider = provider


async def caps(rt: Runtime, **per: dict[str, Any]) -> None:
    await rt.preferences.put(OWNER, CAPS_PREF, per, rt.clock())
    await rt.gate.reload_caps()


def spend(rt: Runtime, pid: str, dollars: float) -> None:
    rt.ledger.add(Row(pid, "conversation", "tokens", 0, dollars, rt.clock()))


class Wire(Page):
    """The page, with the audio frames it is sent counted per sentence."""

    def __init__(self, rt: Runtime) -> None:
        super().__init__(rt)
        self.frames: list[int] = []

    async def send_bytes(self, data: bytes) -> None:
        self.frames.append(struct.unpack_from("<BII", data)[2])


def options(card: dict[str, Any]) -> list[str]:
    return [o["choice"] for o in card["options"]]


def sent_text(provider: ScriptedProvider, call: int = -1) -> str:
    """Everything the user turn of one model call carried, host notes included."""
    last: Msg = provider.calls[call][1][-1]
    return "\n".join(b.text for b in last.blocks if isinstance(b, ContextBlock | TextBlock))


# ── Her model at the hard cap ────────────────────────────────────────────


async def test_at_the_cap_the_turn_is_refused_with_the_line_and_the_card(rt: Runtime) -> None:
    provider = ScriptedProvider(say("never"))
    await on_model(rt, GPT, ScriptedProvider())
    await on_model(rt, GEMMA, ScriptedProvider())
    await on_model(rt, ANTHROPIC, provider)
    await caps(rt, anthropic=CAPS, openai=CAPS)
    spend(rt, "anthropic", 50.0)
    spend(rt, "openai", 50.0)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)

    await session.handle({"type": "turn.text", "text": "hello?"})
    await idle(session)

    assert provider.calls == []  # refused before anything was sent
    (error,) = page.all("error")
    assert error["code"] == "capped" and error["leg"] == "model"
    assert error["provider_id"] == "anthropic" and error["cap"] == 50.0
    assert error["sentence"] == MODEL_LINE
    (card,) = page.all("spend.card")
    assert card["provider_id"] == "anthropic" and card["cap"] == 50.0
    assert card["status"] == "open"
    # The next round step, a model already set up with budget left, then stop. Not
    # OpenAI: it is capped too.
    assert options(card) == ["raise", "switch:local/gemma", "stop"]
    assert card["options"][0]["amount"] == 75
    assert card["options"][1]["label"] == "gemma"
    assert page.all("turn.complete")[-1]["stop"] == "provider_error"
    assert not page.all("reply.delta")


async def test_a_reply_in_flight_finishes_and_the_next_round_ends_the_turn(rt: Runtime) -> None:
    """The gate checks only at a call's start: the round that crosses the cap finishes,
    its text is saved and spoken, and the tool loop's next round is refused."""

    class Crossing(ScriptedProvider):
        async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:
            async for ev in super().stream(**kw):
                yield ev
            spend(rt, "anthropic", 49.0)  # this round's cost takes it over

    provider = Crossing(use("describe_self", {}, "c1", text="Let me look."), say("never"))
    await on_model(rt, ANTHROPIC, provider)
    await caps(rt, anthropic=CAPS)
    spend(rt, "anthropic", 1.0)
    rt.speech = SpeechSettings(kind="elevenlabs", voice="v1")
    rt.tts = voice = FakeVoice()
    page = Wire(rt)
    session = await Session.start(rt, page.send, None, page.send_bytes)

    await session.handle({"type": "turn.text", "text": "what can you do?"})
    await idle(session)

    assert len(provider.calls) == 1
    assert "".join(m["text"] for m in page.all("reply.delta")) == "Let me look."
    assert [m["text"] for m in page.all("reply.sentence")] == ["Let me look.", MODEL_LINE]
    assert voice.asked == ["Let me look.", MODEL_LINE]
    assert page.all("error")[-1]["code"] == "capped"
    assert page.all("spend.card")
    saved = await rt.conversations.history(session.id, OWNER)
    assert any(
        kind == "assistant" and any(getattr(b, "text", "") == "Let me look." for b in m.blocks)
        for kind, m in saved
    )


# ── Her voice at the hard cap ────────────────────────────────────────────


async def test_her_voice_at_the_cap_finishes_the_sentence_and_goes_to_text(rt: Runtime) -> None:
    class Crossing(FakeVoice):
        async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
            async for chunk in super().synthesize(text, voice=voice):
                yield chunk
            spend(rt, "elevenlabs", 50.0)

    await on_model(rt, ANTHROPIC, ScriptedProvider(say("One thing. Two things. Three.")))
    await caps(rt, elevenlabs=CAPS)
    rt.speech = SpeechSettings(kind="elevenlabs", voice="v1")
    rt.tts = voice = Crossing()
    page = Wire(rt)
    session = await Session.start(rt, page.send, None, page.send_bytes)

    await session.handle({"type": "turn.text", "text": "count"})
    await idle(session)

    # The sentence in progress finished; nothing after it was asked for.
    assert len(voice.asked) == 1 and set(page.frames) == {1}
    first = voice.asked[0]
    reply = "".join(m["text"] for m in page.all("reply.delta"))
    assert reply == "One thing. Two things. Three."  # nothing of the reply is lost
    (error,) = page.all("error")
    assert error["code"] == "capped" and error["leg"] == "voice"
    assert error["provider_id"] == "elevenlabs"
    assert error["sentence"] == capped("voice", "ElevenLabs", "$50")
    # Not in her own voice, which is capped: the page may say it in the computer's.
    assert error["say"] == "system"
    (card,) = page.all("spend.card")
    assert options(card) == ["raise", "switch:system", "text"]

    # Text only: the next reply is text, with no second line and no second card.
    assert (await rt.spend.choose(card["id"], "text", None, session.id, "tap")).status == "stopped"
    rt.provider = ScriptedProvider(say("Quietly."))
    await session.handle({"type": "turn.text", "text": "again"})
    await idle(session)
    assert voice.asked == [first]
    assert len(page.all("spend.card")) == 1  # the one resolved, shown as chosen
    assert len([e for e in page.all("error") if e["code"] == "capped"]) == 1
    assert page.all("spend.resolved")[-1]["status"] == "stopped"


# ── Hearing at the hard cap ──────────────────────────────────────────────


async def test_hearing_at_the_cap_does_not_open_and_offers_typing(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def installed() -> str | None:
        return "base.en"

    monkeypatch.setattr(runtime_module, "whisper_choice", installed)
    await on_model(rt, ANTHROPIC, ScriptedProvider(say("Typed is fine.")))
    await caps(rt, deepgram=CAPS)
    spend(rt, "deepgram", 50.0)
    rt.listening = ListenSettings(kind="deepgram", choice="en")
    rt.stt = stt = FakeSTT()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)

    await session.handle({"type": "listen.start"})

    assert stt.streams == []  # refused before it opened
    assert page.all("mic.stop")[-1]["reason"] == "failed"
    (error,) = page.all("error")
    assert error["code"] == "capped" and error["leg"] == "hearing"
    assert error["sentence"] == capped("hearing", "Deepgram", "$50")
    (card,) = page.all("spend.card")
    assert options(card) == ["raise", "switch:whisper", "type"]
    # Typing still works.
    await session.handle({"type": "turn.text", "text": "typed?"})
    await idle(session)
    assert "".join(m["text"] for m in page.all("reply.delta")) == "Typed is fine."


async def test_no_whisper_option_when_it_is_not_installed(rt: Runtime) -> None:
    await on_model(rt, ANTHROPIC, ScriptedProvider())
    await caps(rt, deepgram=CAPS)
    spend(rt, "deepgram", 50.0)
    rt.listening = ListenSettings(kind="deepgram", choice="en")
    rt.stt = FakeSTT()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    assert options(page.all("spend.card")[0]) == ["raise", "type"]


# ── The card's choices ───────────────────────────────────────────────────


async def capped_card(rt: Runtime) -> tuple[Session, Page, dict[str, Any]]:
    await on_model(rt, GEMMA, ScriptedProvider())
    await on_model(rt, ANTHROPIC, ScriptedProvider())
    await caps(rt, anthropic=CAPS)
    spend(rt, "anthropic", 50.0)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hello?"})
    await idle(session)
    return session, page, page.all("spend.card")[-1]


async def test_raise_writes_the_cap_and_she_can_go_on(rt: Runtime) -> None:
    session, page, card = await capped_card(rt)
    with pytest.raises(desk.Refused) as low:
        await rt.spend.choose(card["id"], "raise", 50.0, session.id, "tap")
    assert low.value.code == "bad_amount"  # not above what is already spent
    with pytest.raises(desk.Refused):
        await rt.spend.choose(card["id"], "raise", -1.0, session.id, "tap")

    done = await rt.spend.choose(card["id"], "raise", 80.0, session.id, "tap")

    assert done.status == "raised"
    assert await rt.preferences.get(OWNER, CAPS_PREF) == {
        "anthropic": {"soft": 20.0, "hard": 80.0, "warn_pct": 80, "unit": "dollars"}
    }
    assert rt.gate.state("anthropic") == "over_soft"
    assert page.all("spend.resolved")[-1] == {
        "type": "spend.resolved",
        "id": card["id"],
        "status": "raised",
    }
    rt.provider = ScriptedProvider(say("Back."))
    await session.handle({"type": "turn.text", "text": "and now?"})
    await idle(session)
    assert "".join(m["text"] for m in page.all("reply.delta")) == "Back."
    # Answered: it cannot be answered again.
    with pytest.raises(desk.Refused) as again:
        await rt.spend.choose(card["id"], "stop", None, session.id, "tap")
    assert again.value.code == "closed"


async def test_a_choice_from_another_session_or_not_by_a_tap_is_refused(rt: Runtime) -> None:
    session, _page, card = await capped_card(rt)
    other = await Session.start(rt, Page(rt).send, None)
    for who, via, code in (
        (other.id, "tap", "wrong_session"),
        ("no-such-session", "tap", "wrong_session"),
        (session.id, "voice", "not_a_tap"),
        (session.id, "text", "not_a_tap"),
    ):
        with pytest.raises(desk.Refused) as refused:
            await rt.spend.choose(card["id"], "raise", 80.0, who, via)
        assert refused.value.code == code
    with pytest.raises(desk.Refused) as unknown:
        await rt.spend.choose("no-such-card", "stop", None, session.id, "tap")
    assert unknown.value.code == "unknown_card"
    with pytest.raises(desk.Refused) as offered:
        await rt.spend.choose(card["id"], "switch:openai/gpt-test", None, session.id, "tap")
    assert offered.value.code == "unknown_choice"  # only what the card offered
    assert rt.gate.caps("anthropic").hard == 50.0


async def test_stop_keeps_the_card_for_the_banner_after_a_reload(rt: Runtime) -> None:
    session, _page, card = await capped_card(rt)
    done = await rt.spend.choose(card["id"], "stop", None, session.id, "tap")
    assert done.status == "stopped"
    page = Page(rt)
    await session.close()
    await Session.start(rt, page.send, session.id)
    (again,) = page.all("spend.card")
    assert again["id"] == card["id"] and again["status"] == "stopped"
    # Still answerable from the banner.
    assert (await rt.spend.choose(card["id"], "raise", 75.0, session.id, "tap")).status == "raised"


async def test_switch_uses_the_model_until_the_1st_and_says_so(rt: Runtime) -> None:
    session, page, card = await capped_card(rt)
    done = await rt.spend.choose(card["id"], "switch:local/gemma", None, session.id, "tap")
    assert done.status == "switched"
    until = next_month_start(rt.clock(), rt.zone() or "")
    assert await rt.preferences.get(OWNER, desk.SWITCH_PREF) == {
        "anthropic": {
            "leg": "model",
            "to": "local/gemma",
            "label": "gemma",
            "until_ms": until,
            "settings": GEMMA.to_json(),
        }
    }
    assert rt.provider is not None and rt.provider.provider_id == "local"
    assert rt.provider.model == "gemma"
    assert rt.settings == ANTHROPIC  # the user's choice is unchanged
    (switch,) = page.all("spend.switch")[-1]["switches"]
    assert switch["provider_id"] == "anthropic" and switch["until_ms"] == until
    assert switch["line"].startswith("Using gemma until ")
    assert page.all("model")[-1]["model"]["model"] == "gemma"


async def test_the_switch_survives_a_restart_and_lapses_on_the_1st(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    secrets = _secrets()
    rt = await _open(tmp_path, secrets)
    try:
        session, _page, card = await capped_card(rt)
        await rt.spend.choose(card["id"], "switch:local/gemma", None, session.id, "tap")
        until = next_month_start(rt.clock(), rt.zone() or "")
    finally:
        await rt.close()

    rt = await _open(tmp_path, secrets)
    try:
        assert rt.provider is not None and rt.provider.provider_id == "local"
        page = Page(rt)
        session = await Session.start(rt, page.send, None)
        assert page.all("spend.switch")[0]["switches"][0]["to"] == "local/gemma"

        # The 1st: the switch lapses, and she is back on the usual model.
        rt.clock = lambda: until + 60_000
        await rt.spend_tick()
        assert rt.provider is not None and rt.provider.provider_id == "anthropic"
        assert await rt.preferences.get(OWNER, desk.SWITCH_PREF) == {}
        assert page.all("spend.switch")[-1]["switches"] == []

        provider = ScriptedProvider(say("Back to normal."), say("Still here."))
        rt.provider = provider
        await session.handle({"type": "turn.text", "text": "hi"})
        await idle(session)
        first = sent_text(provider, 0)
        assert "[host]" in first and "back on claude-test" in first
        await session.handle({"type": "turn.text", "text": "and?"})
        await idle(session)
        assert "back on" not in sent_text(provider, 1)  # said once
    finally:
        await rt.close()


# ── The warning and the soft cap ─────────────────────────────────────────


async def test_the_warning_is_one_notice_once_a_month(rt: Runtime) -> None:
    provider = ScriptedProvider(say("One."), say("Two."))
    await on_model(rt, ANTHROPIC, provider)
    await caps(rt, anthropic=CAPS)
    spend(rt, "anthropic", 16.5)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)

    for text in ("one", "two"):
        await session.handle({"type": "turn.text", "text": text})
        await idle(session)

    assert rt.schedules is not None
    held = [n for n in await rt.schedules.held(OWNER) if n.source_id.startswith("spend:")]
    delivered = [m for m in page.all("notice") if m.get("code") == "schedule"]
    found = [n.title for n in held] + [m["title"] for m in delivered]
    assert found == ["Anthropic has used $16.50 of its $20 soft cap this month"]
    told = await rt.preferences.get(OWNER, desk.TOLD_PREF)
    assert told["warned"] == {"anthropic": month_start(rt.clock(), rt.zone() or "")[0]}
    # Not spoken: it has no words of hers.
    assert all(m["text"] is None for m in delivered)
    assert "soft cap" not in sent_text(provider, 0)  # warned is not over


async def test_past_the_soft_cap_her_next_turn_carries_one_host_note(rt: Runtime) -> None:
    provider = ScriptedProvider(say("Heads up, we passed it."), say("Sure."))
    await on_model(rt, ANTHROPIC, provider)
    await caps(rt, anthropic=CAPS)
    spend(rt, "anthropic", 21.0)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)

    await session.handle({"type": "turn.text", "text": "hi"})
    await idle(session)
    await session.handle({"type": "turn.text", "text": "go on"})
    await idle(session)

    first = sent_text(provider, 0)
    assert "[host]" in first and "Anthropic" in first and "$20 soft cap" in first
    assert "in your own words" in first
    assert "soft cap" not in sent_text(provider, 1)
    told = await rt.preferences.get(OWNER, desk.TOLD_PREF)
    assert told["over_soft"] == {"anthropic": month_start(rt.clock(), rt.zone() or "")[0]}


# ── The endpoint ─────────────────────────────────────────────────────────


def test_the_endpoint_answers_a_tap_from_the_cards_session_only(make: Any) -> None:
    app: App = make(say("never"))
    portal = app.client.portal
    assert portal is not None

    async def setup() -> None:
        app.secrets.set("anthropic/api_key", "test-key")
        await on_model(app.rt, ANTHROPIC, app.provider)
        await caps(app.rt, anthropic=CAPS)
        spend(app.rt, "anthropic", 50.0)

    portal.call(setup)
    with app.connect() as chat:
        chat.ws.send_json({"type": "turn.text", "text": "hello?"})
        card = chat.until("spend.card")[-1]
        sid = chat.session["id"]
        body = {"id": card["id"], "choice": "raise", "amount": 75, "via": "tap"}
        url = "/api/spend/choice"
        assert app.client.post(url, json=body | {"session": sid}).status_code == 401
        headers = app.auth | {"origin": "http://evil.example"}
        refused = app.client.post(url, json=body | {"session": sid}, headers=headers)
        assert refused.status_code == 403
        wrong = app.client.post(url, json=body | {"session": "other"}, headers=app.auth)
        assert (wrong.status_code, wrong.json()) == (403, {"error": "wrong_session"})
        spoken = app.client.post(
            url, json=body | {"session": sid, "via": "voice"}, headers=app.auth
        )
        assert (spoken.status_code, spoken.json()) == (403, {"error": "not_a_tap"})
        low = app.client.post(url, json=body | {"session": sid, "amount": 10}, headers=app.auth)
        assert (low.status_code, low.json()) == (400, {"error": "bad_amount"})
        ok = app.client.post(url, json=body | {"session": sid}, headers=app.auth)
        assert ok.status_code == 200 and ok.json()["status"] == "raised"
        assert app.rt.gate.caps("anthropic").hard == 75.0
        assert chat.until("spend.resolved")[-1]["status"] == "raised"


async def test_month_of_and_the_friendly_words() -> None:
    assert desk.friendly("anthropic") == "Anthropic"
    assert desk.friendly("custom:openrouter.ai") == "OpenRouter"
    assert desk.friendly("custom:models.example.com") == "models.example.com"
    assert desk.cap_words(50.0, "dollars") == "$50"
    assert desk.cap_words(12.5, "dollars") == "$12.50"
    assert desk.cap_words(12_000_000, "tokens") == "12 million token"
    assert desk.cap_words(90_000, "seconds") == "1,500 minute"
    assert desk.next_step(50.0, "dollars") == 75.0
    assert desk.next_step(60.0, "dollars") == 75.0
    assert desk.next_step(75.0, "dollars") == 100.0
    assert desk.next_step(12_000_000, "tokens") == 18_000_000
    await asyncio.sleep(0)
