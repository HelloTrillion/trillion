"""A reply spoken as it streams: order, framing, pauses for tools, failure and interruption."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator
from typing import Any

import pytest

from trillion.api import speech
from trillion.api.speech import FRAME_AUDIO, Speaker
from trillion.persona.prompt import degraded
from trillion.voice.providers.system import SystemVoice
from trillion.voice.types import AudioFormat, Voice, VoiceError, Where


class FakeVoice:
    name = "fake"
    where: Where = "core"
    output_format: AudioFormat | None = AudioFormat(sample_rate=16_000)

    def __init__(self, fail_on: str | None = None, slow: bool = False) -> None:
        self.asked: list[str] = []
        self._fail_on = fail_on
        self._slow = slow

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        self.asked.append(text)
        if self._fail_on is not None and self._fail_on in text:
            raise VoiceError("rate_limited", f"echo {text}")
        if self._slow:
            await asyncio.sleep(30)
        yield text.encode().ljust(20_000, b"\x00")  # larger than one frame

    async def voices(self) -> list[Voice]:
        return []


class Wire:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def send(self, kind: str, /, **body: Any) -> None:
        self.events.append({"type": kind, **body})

    async def send_bytes(self, data: bytes) -> None:
        kind, turn, seq = struct.unpack_from("<BII", data)
        assert kind == FRAME_AUDIO and (len(data) - 9) % 2 == 0
        self.events.append(("audio", turn, seq, data[9:]))

    def kinds(self) -> list[str]:
        return [e["type"] if isinstance(e, dict) else f"audio:{e[2]}" for e in self.events]

    def audio(self, seq: int) -> bytes:
        return b"".join(e[3] for e in self.events if isinstance(e, tuple) and e[2] == seq)


def speaker(voice: Any, wire: Wire) -> Speaker:
    return Speaker(voice, "v1", 7, wire.send, wire.send_bytes)


async def test_sentences_are_announced_cleaned_and_spoken_in_order() -> None:
    wire, voice = Wire(), FakeVoice()
    s = speaker(voice, wire)
    for piece in ("**Done.** The file ", "is there. Check `hello.txt` ", "when you can."):
        await s.feed(piece)
    await s.finish()

    said = [e["text"] for e in wire.events if isinstance(e, dict) and e["type"] == "reply.sentence"]
    assert said == ["Done. The file is there.", "Check hello.txt when you can."]
    assert voice.asked == said  # she says what she wrote, minus the markup
    assert wire.audio(1).rstrip(b"\x00") == said[0].encode()
    assert wire.audio(2).rstrip(b"\x00") == said[1].encode()
    # All of sentence one's audio precedes sentence two's, and the end is marked last.
    order = [k for k in wire.kinds() if k.startswith("audio")]
    assert order == sorted(order, key=lambda k: k.split(":")[-1]) and order[-1] == "audio.end"
    assert wire.events[-1] == {"type": "audio.end", "turn": 7, "sentences": 2}


async def test_the_system_voice_sends_words_and_no_audio() -> None:
    wire = Wire()
    s = speaker(SystemVoice(), wire)
    await s.feed("Short answer: yes. ")
    await s.finish()
    assert wire.kinds() == ["reply.sentence", "audio.end"]


async def test_she_says_what_she_has_so_far_before_a_tool_runs() -> None:
    wire = Wire()
    s = speaker(SystemVoice(), wire)
    await s.feed("Let me look at that file")
    await s.pause()
    assert wire.events[0]["text"] == "Let me look at that file"


async def test_a_reply_that_is_only_code_or_markup_is_not_spoken() -> None:
    wire, voice = Wire(), FakeVoice()
    s = speaker(voice, wire)
    await s.feed("```\nprint(1)\n```\n\n🎉")
    await s.finish()
    assert voice.asked == [] and wire.kinds() == ["audio.end"]


async def test_a_failing_voice_is_reported_once_and_the_turn_goes_on_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(speech, "RETRY_S", 0)
    wire, voice = Wire(), FakeVoice(fail_on="second")
    s = speaker(voice, wire)
    await s.feed("The first sentence is fine. The second one fails. The third is skipped. ")
    await s.finish()
    errors = [e for e in wire.events if isinstance(e, dict) and e["type"] == "error"]
    assert errors == [
        {
            "type": "error",
            "code": "speech",
            "kind": "rate_limited",
            "sentence": degraded("voice", "rate_limited"),
        }
    ]
    # A passing fault is tried once more before she gives up on her voice.
    assert sum("second" in t for t in voice.asked) == 2
    assert wire.audio(1) and not wire.audio(2) and not wire.audio(3)
    assert "echo" not in str(wire.events)  # the provider's own words are never passed on


async def test_an_interrupt_stops_synthesis_and_tells_the_page() -> None:
    wire, voice = Wire(), FakeVoice(slow=True)
    s = speaker(voice, wire)
    await s.feed("This sentence will never finish being spoken. And neither will this. ")
    await asyncio.sleep(0)
    await s.stop()
    await s.feed("Nothing after a stop is spoken. ")
    assert wire.events[-1] == {"type": "audio.stop", "turn": 7}
    assert not [e for e in wire.events if isinstance(e, tuple)]


async def test_the_speaker_knows_which_sentences_were_heard() -> None:
    s = speaker(SystemVoice(), Wire())
    await s.feed("We could leave at **six**. Or if traffic is bad, at half past. Or")
    assert s.heard_text() == ""
    s.played(1)
    # What she wrote, markup and all: the history holds her words, not the spoken form.
    assert s.heard_text() == "We could leave at **six**."
    s.played(2)
    assert s.heard_text() == "We could leave at **six**. Or if traffic is bad, at half past."
    s.played(1)  # a late report never takes back what was heard
    assert s.heard_text().endswith("half past.")
    assert s.playing() == ("We could leave at **six**.", "Or if traffic is bad, at half past.")


async def test_heard_text_is_only_this_round() -> None:
    s = speaker(SystemVoice(), Wire())
    await s.feed("Let me look. ")
    s.played(1)
    await s.pause()  # a tool runs between rounds
    await s.feed("I found the train times. It leaves at four.")
    assert s.heard_text() == ""
    s.played(2)
    assert s.heard_text() == "I found the train times."


async def test_a_sentence_with_nothing_to_say_is_never_counted_as_heard() -> None:
    s = speaker(SystemVoice(), Wire())
    await s.feed("```\ncode\n``` ")
    await s.pause()
    await s.feed("I found the train times. And")
    s.played(1)
    # Nothing was sent for the code, so the page's sentence 1 is the next one.
    assert s.turn == 7 and s.heard_text() == "I found the train times."
