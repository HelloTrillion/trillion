"""VOI-14 and SUR-8: one provider down, the other two healthy.

For each leg (her model, her voice, her ears) the turn degrades to text rather
than hanging or crashing, and she says what broke in one sentence: spoken when her
voice is the healthy leg, shown in the banner either way. Also: when her voice
fails after an action already succeeded, the action never runs again.
"""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import json
import struct
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.api.test_server import App, Chat, home, make, only, propose, receive  # noqa: F401
from tests.brain.fakes import say
from tests.voice.fakes import FakeSTT
from tests.voice.test_speaker import FakeVoice
from trillion.api import speech
from trillion.api.runtime import ListenSettings, SpeechSettings
from trillion.brain.types import ProviderError
from trillion.persona.prompt import degraded
from trillion.voice.types import VoiceError


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech, "RETRY_S", 0)


def voiced(app: App, voice: Any | None = None) -> Any:
    app.rt.tts = voice or FakeVoice()
    app.rt.speech = SpeechSettings(kind="elevenlabs", voice="v1")
    return app.rt.tts


def collect(chat: Chat, *until: str) -> tuple[list[dict[str, Any]], list[int]]:
    """Every message and the turn of every audio frame, up to one of `until`."""
    msgs: list[dict[str, Any]] = []
    frames: list[int] = []
    while True:
        raw = receive(chat.ws)
        if raw.get("bytes") is not None:
            frames.append(struct.unpack_from("<BII", raw["bytes"])[1])
            continue
        msgs.append(json.loads(raw["text"]))
        if msgs[-1]["type"] == "confirm.request":  # shown, as the page reports it
            chat.ws.send_json({"type": "confirm.shown", "pending_id": msgs[-1]["pending_id"]})
        if msgs[-1]["type"] in until:
            return msgs, frames


def spoken(msgs: list[dict[str, Any]]) -> list[str]:
    return [m["text"] for m in msgs if m["type"] == "reply.sentence"]


# ── Her model down, her voice up ─────────────────────────────────────────


def test_with_the_model_down_she_says_so_out_loud(make: Any) -> None:
    app = make(ProviderError("unreachable", "connection refused"))
    voice = voiced(app)
    with app.connect() as chat:
        chat.ws.send_json({"type": "turn.text", "text": "hello?"})
        msgs, frames = collect(chat, "turn.complete")
    said = degraded("model", "unreachable")
    assert spoken(msgs) == [said]  # spoken through the healthy voice...
    assert voice.asked == [said] and frames  # ...and actually synthesized
    error = only(msgs, "error")
    assert error["code"] == "provider" and error["sentence"] == said  # ...and in the banner
    assert not [m for m in msgs if m["type"] == "reply.delta"]  # no reply was invented
    assert "connection refused" not in json.dumps(msgs)


def test_with_the_model_down_and_no_voice_the_sentence_is_written(make: Any) -> None:
    app = make(ProviderError("rate_limited"))
    with app.connect() as chat:
        msgs = chat.say("hello?")
    assert only(msgs, "error")["sentence"] == degraded("model", "rate_limited")
    assert not spoken(msgs)


# ── Her voice down, her model up ─────────────────────────────────────────


def test_with_the_voice_down_the_reply_arrives_as_text(make: Any) -> None:
    app = make(say("Roses like sun. They hate wet feet."))
    voice = voiced(app, FakeVoice(fail_on=""))  # every sentence fails
    with app.connect() as chat:
        chat.ws.send_json({"type": "turn.text", "text": "roses?"})
        msgs, frames = collect(chat, "turn.complete")
    reply = "".join(m["text"] for m in msgs if m["type"] == "reply.delta")
    assert reply == "Roses like sun. They hate wet feet."  # nothing of the reply is lost
    errors = [m for m in msgs if m["type"] == "error"]
    assert len(errors) == 1 and errors[0]["code"] == "speech"  # said once, not per sentence
    assert errors[0]["sentence"] == degraded("voice", "rate_limited")
    assert not frames and len(voice.asked) == 2  # one try, one retry, then text only
    assert msgs[-1]["type"] == "turn.complete"


# ── Her ears down, her voice and model up ────────────────────────────────


def test_with_the_ears_down_she_says_so_and_typing_still_works(make: Any) -> None:
    app = make(say("Typed is fine."))
    voice = voiced(app)
    app.rt.stt = FakeSTT(fail="unreachable")
    app.rt.listening = ListenSettings(kind="deepgram", choice="en")
    with app.connect() as chat:
        chat.ws.send_json({"type": "listen.start"})
        msgs, frames = collect(chat, "audio.end")
        said = degraded("hearing", "unreachable")
        assert only(msgs, "error")["sentence"] == said
        assert spoken(msgs) == [said] and voice.asked == [said] and frames
        # The end of that sentence must not reopen the microphone that just failed.
        chat.ws.send_json({"type": "playback.ended", "turn_id": 0, "played_ms": 900})
        chat.ws.send_json({"type": "turn.text", "text": "and typed?"})
        msgs, _ = collect(chat, "turn.complete")
    assert not [m for m in msgs if m["type"] == "mic.start"]
    assert "".join(m["text"] for m in msgs if m["type"] == "reply.delta") == "Typed is fine."
    assert len(app.rt.stt.choices_asked) == 1  # the failing recognizer was tried once


# ── Her voice down after an action already succeeded ─────────────────────


class Hiccup(FakeVoice):
    """A voice service that drops the first request and answers every one after."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        if not self.failed:
            self.failed = True
            self.asked.append(text)
            raise VoiceError("unreachable")
        async for chunk in super().synthesize(text, voice=voice):
            yield chunk


@pytest.mark.parametrize("voice", [Hiccup, lambda: FakeVoice(fail_on="")])
def test_a_voice_failure_after_the_action_never_runs_it_again(make: Any, voice: Any) -> None:
    app = make()
    app.provider._replies += [propose(app), say("Shall I?"), say("Done. The note is there.")]
    tts = voiced(app, voice())
    with app.connect() as chat:
        chat.ws.send_json({"type": "turn.text", "text": "write a note"})
        msgs, _ = collect(chat, "turn.complete")
        card = only(msgs, "confirm.request")
        chat.ws.send_json({"type": "confirm", "pending_id": card["pending_id"]})
        msgs, frames = collect(chat, "turn.complete")
    assert only(msgs, "confirm.resolved")["status"] == "succeeded"
    reply = "".join(m["text"] for m in msgs if m["type"] == "reply.delta")
    assert reply == "Done. The note is there."  # the result is shown whatever the voice did
    if isinstance(tts, Hiccup):
        assert frames and not [m for m in msgs if m["type"] == "error"]  # retried, then heard
    else:
        assert only(msgs, "error")["code"] == "speech" and not frames
    assert (app.granted / "note.txt").read_text() == "hi"
    executions = app.rows("SELECT count(*) FROM decisions WHERE event = 'executing'")
    assert executions == [(1,)]  # one approval, one execution, whatever the voice did
