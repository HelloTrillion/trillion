"""VOI-14 and SUR-8: one provider down, the other two healthy.

For each leg (her model, her voice, her ears) the turn degrades to text rather
than hanging or crashing, and the banner names what broke in the app's one line.
Only her own words are ever spoken (PER-11, the seamless spec, decision 2): with her
model down nothing is, and the app's line is shown as the app's, never saved as hers;
with her ears down and her model up, she says what broke in her own words. Also:
when her voice fails after an action already succeeded, the action never runs again.
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
    app.rt.tts = voice = voice or FakeVoice()
    app.rt.speech = SpeechSettings(kind="elevenlabs", voice="v1")
    # The stand-in itself: the runtime holds it metered.
    return voice


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


def test_with_the_model_down_nothing_is_spoken_and_the_app_says_what_broke(make: Any) -> None:
    """Changed deliberately by the seamless spec (decision 2): this used to speak the
    authored line in her voice. With her model down there is nobody to compose a line,
    so nothing is spoken; the app's line is in the banner, and in the conversation as
    the app's status (the page draws it), never saved as her message."""
    app = make(ProviderError("unreachable", "connection refused"))
    voice = voiced(app)
    with app.connect() as chat:
        chat.ws.send_json({"type": "turn.text", "text": "hello?"})
        msgs, frames = collect(chat, "turn.complete")
    shown = degraded("model", "unreachable")
    assert spoken(msgs) == [] and voice.asked == [] and not frames  # nothing spoken
    error = only(msgs, "error")
    assert error["code"] == "provider" and error["sentence"] == shown  # in the banner
    assert not [m for m in msgs if m["type"] == "reply.delta"]  # no reply was invented
    assert "connection refused" not in json.dumps(msgs)
    # Never saved as hers: no message of hers, and the line nowhere in the conversation.
    assert app.rows("SELECT count(*) FROM messages WHERE role = 'assistant'") == [(0,)]
    saved = [row[0] for row in app.rows("SELECT blocks_json FROM messages")]
    assert not any(shown in blocks for blocks in saved)


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
    """Changed deliberately by the seamless spec (decision 2): her sentence for it is
    her own, composed by her model on the app's note, never the authored line spoken
    in her voice. It is spoken, shown and saved as hers; the banner has the app's."""
    mine = "I can't hear you right now, but typing works."
    app = make(say(mine), say("Typed is fine."))
    voice = voiced(app)
    app.rt.stt = FakeSTT(fail="unreachable")
    app.rt.listening = ListenSettings(kind="deepgram", choice="en")
    with app.connect() as chat:
        chat.ws.send_json({"type": "listen.start"})
        msgs, frames = collect(chat, "turn.complete")
        shown = degraded("hearing", "unreachable")
        assert only(msgs, "error")["sentence"] == shown  # the app's line, in the banner
        assert spoken(msgs) == [mine] and voice.asked == [mine] and frames  # hers, aloud
        assert shown not in voice.asked
        # Composed on the app's note, which names what broke.
        _system, sent = app.provider.calls[-1]
        assert shown in "".join(getattr(b, "text", "") for b in sent[-1].blocks)
        # The end of that sentence must not reopen the microphone that just failed.
        turn = next(m["turn"] for m in msgs if m["type"] == "reply.sentence")
        chat.ws.send_json({"type": "playback.ended", "turn_id": turn, "played_ms": 900})
        chat.ws.send_json({"type": "turn.text", "text": "and typed?"})
        msgs, _ = collect(chat, "turn.complete")
    assert not [m for m in msgs if m["type"] == "mic.start"]
    assert "".join(m["text"] for m in msgs if m["type"] == "reply.delta") == "Typed is fine."
    assert len(app.rt.stt.choices_asked) == 1  # the failing recognizer was tried once
    # Hers, saved as hers; the app's line never is.
    saved = [
        row[0] for row in app.rows("SELECT blocks_json FROM messages WHERE role = 'assistant'")
    ]
    assert any(mine in blocks for blocks in saved)
    assert not any(shown in blocks for blocks in saved)


def test_with_the_ears_and_the_model_down_nothing_is_spoken(make: Any) -> None:
    app = make(ProviderError("unreachable"))
    voice = voiced(app)
    app.rt.stt = FakeSTT(fail="unreachable")
    app.rt.listening = ListenSettings(kind="deepgram", choice="en")
    with app.connect() as chat:
        chat.ws.send_json({"type": "listen.start"})
        msgs, frames = collect(chat, "turn.complete")
    errors = [m for m in msgs if m["type"] == "error"]
    assert [e["sentence"] for e in errors] == [
        degraded("hearing", "unreachable"),
        degraded("model", "unreachable"),
    ]
    assert spoken(msgs) == [] and voice.asked == [] and not frames
    assert app.rows("SELECT count(*) FROM messages WHERE role = 'assistant'") == [(0,)]


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
