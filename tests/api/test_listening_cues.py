"""Listening cues (PER-11, VOI-12): she says "mm-hm" in a real pause while the user
thinks aloud. Tested as the owner runs her: Flux hearing (a fake that judges turns and
times its words), her voice made in the core, and barge-in on."""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.api.test_drafts import judging
from tests.api.test_listening import rt, speak
from tests.api.test_overlap_watch import Steady, steady, timed
from tests.api.test_session import Page, idle
from tests.brain.fakes import say
from tests.voice.audio import room, tone
from tests.voice.fakes import FakeSTT
from trillion.api import session as session_module
from trillion.api.cues import CueBank, trim
from trillion.api.runtime import ListenSettings, Runtime, SpeechSettings
from trillion.api.session import Session
from trillion.brain.types import TextBlock
from trillion.voice import cues as cue_rules
from trillion.voice.providers.system import SystemVoice
from trillion.voice.types import VoiceError, Word

__all__ = ["rt"]

THOUGHT = "so I was thinking about the launch and the budget plan"


class Wire(Page):
    """A page that also keeps the binary frames sent to it."""

    def __init__(self, rt: Runtime) -> None:
        super().__init__(rt)
        self.frames: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.frames.append(data)


@pytest.fixture(autouse=True)
def sure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every pause that may get a cue gets one: the coin is tested on its own.
    monkeypatch.setattr(cue_rules, "CUE_CHANCE", 1.0)
    monkeypatch.setattr(session_module, "NO_AUDIO_S", 30.0)


async def opened(rt: Runtime, *replies: Any) -> tuple[FakeSTT, Wire, Session]:
    stt, _ = judging(rt, *(replies or (say("Sounds like a plan."),)))
    steady(rt)
    page = Wire(rt)
    session = await Session.start(rt, page.send, None, page.send_bytes)
    await rt.cue_bank.wait()
    return stt, page, session


async def thinking_aloud(
    session: Session, stt: FakeSTT, *, confidence: float = 0.1, wordless: bool = False
) -> None:
    """Five seconds of the user's words, then most of a second's pause, as Flux reports
    them: the whole turn so far on each update, with how sure it is the turn is over."""
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(5.0, 0.4))
    words = timed(THOUGHT, 0.3, 5.1)
    stt.stream.say(THOUGHT, final=False, words=words, confidence=confidence)
    await speak(session, room(0.7))
    if wordless:
        # In a pause Flux may report only how sure it is, with no words.
        stt.stream.say("", final=False, confidence=confidence)
    else:
        stt.stream.say(THOUGHT, final=False, words=words, confidence=confidence)
    await speak(session, room(0.1))


def cue_frames(page: Wire) -> list[bytes]:
    return [f for f in page.frames if f[0] == 0x02]


async def test_a_pause_mid_thought_gets_a_cue_in_her_voice(rt: Runtime) -> None:
    stt, page, session = await opened(rt)
    await thinking_aloud(session, stt)
    (play,) = page.all("cue.play")
    assert play["cue"] in ("mm-hm", "mm", "yeah") and play["sample_rate"] == 16_000
    assert set(play) == {"type", "id", "cue", "sample_rate"}
    (frame,) = cue_frames(page)
    assert struct.unpack("<BII", frame[:9]) == (2, play["id"], 0) and len(frame) > 9
    # The announcement comes just before its audio, and it is not her reply.
    assert page.got[-1] is play or page.got.index(play) == len(page.got) - 1
    assert not page.all("reply.sentence") and not page.all("audio.end")
    await session.close()


async def test_an_echo_of_the_cue_is_not_the_users(rt: Runtime) -> None:
    stt, page, session = await opened(rt)
    await thinking_aloud(session, stt)
    (play,) = page.all("cue.play")
    listener = session._listener
    assert listener is not None
    at = listener.sent_s
    await session.handle({"type": "cue.started", "id": play["id"], "in_ms": 0})
    # Her cue comes back faintly, then they go on.
    await speak(session, tone(0.5, 0.02) + tone(1.0, 0.4))
    echo = Word(text=play["cue"], start_s=at + 0.05, end_s=at + 0.4)
    more = timed("next quarter", at + 0.7, at + 1.4)
    said = f"{THOUGHT} {play['cue']} next quarter"
    words = (*timed(THOUGHT, 0.3, 5.1), echo, *more)
    stt.stream.say(said, final=True, turn="done", words=words, confidence=0.9)
    await speak(session, room(0.3))
    await page.until("turn.complete")
    await idle(session)
    (final,) = page.all("transcript.final")
    assert final["text"] == f"{THOUGHT} next quarter"
    last = rt.provider.calls[-1][1]  # type: ignore[union-attr]
    texts = [b.text for b in last[-1].blocks if isinstance(b, TextBlock)]
    assert any(t.endswith(f"{THOUGHT} next quarter") for t in texts)
    await session.close()


async def test_a_stop_is_not_a_pause_and_a_sure_end_gets_nothing(rt: Runtime) -> None:
    stt, page, session = await opened(rt)
    await thinking_aloud(session, stt, confidence=0.6)
    assert page.all("cue.play") == [] and cue_frames(page) == []
    await session.close()


@pytest.mark.parametrize("why", ["off", "system_voice", "whisper", "multi", "muted"])
async def test_no_cues(rt: Runtime, why: str) -> None:
    stt, page, session = await opened(rt)
    if why == "off":
        rt.listen_cues = False
    elif why == "system_voice":
        rt.tts, rt.speech = SystemVoice(), SpeechSettings(kind="system", voice="v1")
    elif why == "whisper":
        stt = FakeSTT(streaming=False)
        rt.stt = stt
    elif why == "multi":
        rt.listening = ListenSettings(kind="deepgram", choice="multi")
    else:
        await session.handle({"type": "speech.mute", "muted": True})
    await thinking_aloud(session, stt)
    assert page.all("cue.play") == [] and cue_frames(page) == []
    await session.close()


async def test_no_cues_over_her_reply(rt: Runtime) -> None:
    """While she answers, the page watches over her: that is barge-in, not a cue. Only
    once their words have stopped her and become their turn may one play."""
    stt, provider = judging(rt, say("First thing. Second thing."), say("Okay."))
    steady(rt)
    held = provider.hold(0)
    page = Wire(rt)
    session = await Session.start(rt, page.send, None, page.send_bytes)
    await rt.cue_bank.wait()
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("tell me the plan", final=True, turn="done")
    await speak(session, room(0.1))
    for _ in range(400):
        if provider.running == 1:
            break
        await asyncio.sleep(0.005)
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is not None
    # Only watching: a pause's report, sure the turn goes on, is nothing to answer.
    await speak(session, room(5.0))
    stt.stream.say("", final=False, confidence=0.05)
    await speak(session, room(0.7))
    stt.stream.say("", final=False, confidence=0.05)
    await speak(session, room(0.1))
    assert page.all("cue.play") == [] and session._task is not None
    # Their words over her: deciding, then their turn. Any cue comes only after.
    await speak(session, tone(5.0, 0.4))
    at = session._overlap.listener.sent_s if session._overlap else 0.0
    words = timed(THOUGHT, at - 4.9, at - 0.1)
    stt.stream.say(THOUGHT, final=False, words=words, confidence=0.1)
    await speak(session, room(0.7))
    stt.stream.say(THOUGHT, final=False, words=words, confidence=0.1)
    await speak(session, room(0.1))
    kinds = [m["type"] for m in page.got]
    if "cue.play" in kinds:
        first = kinds.index("cue.play")
        assert "mic.start" in kinds[:first] and kinds[:first].count("mic.start") >= 2
    held.set()
    await session.close()


async def test_the_cues_are_made_once_per_voice(rt: Runtime) -> None:
    _, _page, session = await opened(rt)
    voice = rt.tts
    assert isinstance(voice, Steady)
    assert sorted(voice.asked) == sorted(["mm-hm", "mm", "yeah", "go on"])
    other = Wire(rt)
    second = await Session.start(rt, other.send, None, other.send_bytes)
    await rt.cue_bank.wait()
    assert len(voice.asked) == 4
    # A new voice makes its own.
    rt.speech = SpeechSettings(kind="elevenlabs", voice="v2")
    await second.settings_changed()
    await rt.cue_bank.wait()
    assert len(voice.asked) == 8
    made = rt.cue_bank.get(voice, "v2")
    assert made is not None and set(made) == {"mm-hm", "mm", "yeah", "go on"}
    await session.close()
    await second.close()


class Broken(Steady):
    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        self.asked.append(text)
        yield tone(0.1)
        raise VoiceError("rate_limited", "secret audio words")


async def test_a_cue_that_fails_to_be_made_means_no_cues(
    rt: Runtime, caplog: pytest.LogCaptureFixture
) -> None:
    stt, _ = judging(rt, say("Okay."))
    rt.tts, rt.speech = Broken(), SpeechSettings(kind="elevenlabs", voice="v1")
    page = Wire(rt)
    with caplog.at_level(logging.WARNING):
        session = await Session.start(rt, page.send, None, page.send_bytes)
        await rt.cue_bank.wait()
    assert rt.cue_bank.get(rt.tts, "v1") is None
    assert "rate_limited" in caplog.text and "secret" not in caplog.text
    await thinking_aloud(session, stt)
    await asyncio.sleep(0.01)
    assert page.all("cue.play") == []
    # Not asked again on every report while they talk: once, until a while has passed
    # or the voice is changed.
    voice = rt.tts
    assert isinstance(voice, Broken) and voice.asked == ["mm-hm"]
    await rt.use_speech(SpeechSettings(kind="elevenlabs", voice="v1"))
    rt.tts = voice
    await session.settings_changed()
    await rt.cue_bank.wait()
    assert voice.asked == ["mm-hm", "mm-hm"]
    await session.close()


def test_a_cue_is_trimmed_of_silence() -> None:
    window = 640  # 20 ms at 16 kHz
    pcm = room(0.3, 0.0001) + tone(0.4, 0.3) + room(0.5, 0.0001)
    cut = trim(pcm, window)
    assert 0.38 * 32_000 <= len(cut) <= 0.44 * 32_000
    assert trim(room(0.2, 0.0), window) == b""


async def test_a_bank_is_closed_with_nothing_left_running() -> None:
    bank = CueBank()

    class Slow(Steady):
        async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
            await asyncio.sleep(30)
            yield b""

    bank.ensure(Slow(), "v1")
    await asyncio.sleep(0)
    await bank.close()
    assert bank.get(Slow(), "v1") is None


async def test_a_wordless_update_in_the_pause_brings_the_cue(rt: Runtime) -> None:
    stt, page, session = await opened(rt)
    await thinking_aloud(session, stt, wordless=True)
    (play,) = page.all("cue.play")
    assert play["cue"] in ("mm-hm", "mm", "yeah")
    await session.close()


async def test_speech_is_counted_from_their_first_word(rt: Runtime) -> None:
    """Four seconds of the microphone open in silence is not four seconds of talk."""
    stt, page, session = await opened(rt)
    await session.handle({"type": "listen.start"})
    await speak(session, room(4.5) + tone(1.5, 0.4))
    words = timed("the launch plan", 4.6, 6.0)
    stt.stream.say("the launch plan", final=False, words=words, confidence=0.1)
    await speak(session, room(0.7))
    stt.stream.say("the launch plan", final=False, words=words, confidence=0.1)
    await speak(session, room(0.1))
    assert page.all("cue.play") == []
    await session.close()


async def test_cue_levels_are_as_loud_as_it_plays() -> None:
    from trillion.api.cues import CUE_GAIN
    from trillion.api.speech import loudness

    bank = CueBank()
    voice = Steady()
    bank.ensure(voice, "v1")
    await bank.wait()
    made = bank.get(voice, "v1")
    assert made is not None
    cue = made["mm-hm"]
    full = max(loudness(cue.pcm, 640))
    assert abs(max(cue.levels) - full * CUE_GAIN) < 1e-9 and CUE_GAIN == 0.5


async def cue_heard(rt: Runtime) -> tuple[FakeSTT, Wire, Session, Word]:
    """A cue played in their pause, and its echo as the recognizer heard it."""
    stt, page, session = await opened(rt)
    await thinking_aloud(session, stt)
    (play,) = page.all("cue.play")
    listener = session._listener
    assert listener is not None
    at = listener.sent_s
    await session.handle({"type": "cue.started", "id": play["id"], "in_ms": 0})
    await speak(session, tone(0.5, 0.02))
    return stt, page, session, Word(text=play["cue"], start_s=at + 0.05, end_s=at + 0.4)


async def test_a_turn_flux_ends_on_the_cue_echo_is_held_not_ended(rt: Runtime) -> None:
    stt, page, session, echo = await cue_heard(rt)
    said = f"{THOUGHT} {echo.text}"
    words = (*timed(THOUGHT, 0.3, 5.1), echo)
    stt.stream.say(said, final=True, turn="done", words=words, confidence=0.9)
    await speak(session, room(1.0))
    # They had only paused: still listening.
    assert page.all("transcript.final") == [] and session._listener is not None
    await speak(session, room(2.0))
    await page.until("transcript.final")
    (final,) = page.all("transcript.final")
    assert final["text"] == THOUGHT
    await idle(session)
    await session.close()


async def test_a_resumed_turn_with_only_the_cue_echo_keeps_the_draft(rt: Runtime) -> None:
    from tests.api.test_drafts import until

    stt, _page, session, echo = await cue_heard(rt)
    words = timed(THOUGHT, 0.3, 5.1)
    stt.stream.say(THOUGHT, final=False, turn="maybe", words=words, confidence=0.6)
    await speak(session, room(0.1))
    await until(lambda: session._draft is not None)
    draft = session._draft
    stt.stream.say(f"{THOUGHT} {echo.text}", final=False, turn="resumed", words=(*words, echo))
    await speak(session, room(0.1))
    assert session._draft is draft
    # Words of theirs after it do let it go.
    listener = session._listener
    assert listener is not None
    more = timed("for next year", listener.sent_s, listener.sent_s + 0.6)
    await speak(session, tone(0.7, 0.4))
    stt.stream.say(
        f"{THOUGHT} {echo.text} for next year",
        final=False,
        turn="resumed",
        words=(*words, echo, *more),
    )
    await speak(session, room(0.1))
    await until(lambda: session._draft is None)
    await session.close()
