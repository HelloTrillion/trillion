"""One utterance, from the microphone opening to the words (VOI-2, VOI-15, VOI-15a)."""

from __future__ import annotations

import asyncio

import pytest

from tests.voice.audio import frames, room, tone
from tests.voice.fakes import FakeSTT
from trillion.api import listening
from trillion.api.listening import Listener, Utterance
from trillion.voice.types import VoiceError


async def feed(listener: Listener, pcm: bytes) -> str | None:
    for frame in frames(pcm):
        verdict = await listener.audio(frame)
        await asyncio.sleep(0)
        if verdict is not None:
            return verdict
    return None


async def nothing(text: str) -> None:
    return None


async def test_several_final_segments_make_one_message() -> None:
    stt = FakeSTT()
    partials: list[str] = []

    async def partial(text: str) -> None:
        partials.append(text)

    listener = Listener(stt, "one", on_partial=partial)
    await listener.start()
    assert listener.open and stt.choices_asked == ["one"]
    assert await feed(listener, room(0.2) + tone(0.6)) is None
    stt.stream.say("remind me", final=False)
    stt.stream.say("remind me to", final=True)
    assert await feed(listener, room(0.2) + tone(0.6)) is None  # a pause, then more
    stt.stream.say("call", final=False)
    await asyncio.sleep(0.01)
    assert listener.text() == "remind me to call"
    stt.stream.say("call Sam, no, call Alex", final=True)  # a self-correction is one thought
    assert await feed(listener, room(0.6)) == "end"
    assert listener.verdict == "end" and await listener.audio(tone(0.1)) is None
    said = await listener.end()
    assert said == Utterance(text="remind me to call Sam, no, call Alex", speaker=None)
    assert partials[0] == "remind me" and partials[-1] == said.text
    assert stt.stream.finished and stt.stream.closed and not listener.open


async def test_audio_sent_while_connecting_is_not_lost() -> None:
    stt = FakeSTT()
    stt.opening.clear()
    listener = Listener(stt, "one", on_partial=nothing)
    starting = asyncio.create_task(listener.start())
    await asyncio.sleep(0)
    first = tone(0.3)
    assert await listener.audio(first) is None and not listener.open
    stt.opening.set()
    await starting
    assert bytes(stt.stream.audio) == first  # the first syllable arrived


async def test_a_batch_recognizer_gives_no_partials_and_its_words_at_the_end() -> None:
    stt = FakeSTT(streaming=False)
    stt.on_finish = ["turn the lights off"]
    partials: list[str] = []

    async def partial(text: str) -> None:
        partials.append(text)

    listener = Listener(stt, "one", on_partial=partial)
    await listener.start()
    assert await feed(listener, tone(0.5) + room(2.3)) == "end"  # BATCH_S of quiet
    assert (await listener.end()).text == "turn the lights off" and partials == []


async def test_words_already_heard_survive_a_recognizer_that_never_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(listening, "STREAMING_WRAP_UP_S", 0.05)
    stt = FakeSTT()
    stt.delay = 5.0
    listener = Listener(stt, "one", on_partial=nothing)
    await listener.start()
    stt.stream.say("good morning")
    await asyncio.sleep(0.01)
    assert (await listener.end()).text == "good morning"


async def test_a_recognizer_failure_is_raised_once_nothing_was_heard() -> None:
    class Broken(FakeSTT):
        async def open(self, *, choice: str):  # type: ignore[no-untyped-def]
            stream = await super().open(choice=choice)

            async def events():  # type: ignore[no-untyped-def]
                raise VoiceError("unreachable")
                yield  # pragma: no cover

            stream.events = events  # type: ignore[method-assign]
            return stream

    listener = Listener(Broken(), "one", on_partial=nothing)
    await listener.start()
    await asyncio.sleep(0.01)
    with pytest.raises(VoiceError) as e:
        await listener.audio(tone(0.1))
    assert e.value.kind == "unreachable"
    with pytest.raises(VoiceError):
        await listener.end()

    unopened = Listener(FakeSTT(fail="unauthorized"), "one", on_partial=nothing)
    with pytest.raises(VoiceError):
        await unopened.start()
    assert (await unopened.end()).text == ""
    await unopened.abandon()


def test_describe_tells_the_page_how_words_arrive() -> None:
    assert listening.describe(None, None, None) is None
    assert listening.describe(FakeSTT(streaming=False), "whisper", "base") == {
        "kind": "whisper",
        "choice": "base",
        "streaming": False,
        "turns": False,
    }


async def test_a_finished_question_ends_the_moment_the_recognizer_hears_the_pause() -> None:
    stt = FakeSTT()
    listener = Listener(stt, "one", on_partial=nothing)
    await listener.start()
    assert await feed(listener, room(0.2) + tone(0.8)) is None
    assert await feed(listener, room(0.3)) is None
    stt.stream.say("What is the tallest mountain?", final=True, pause=True)
    await asyncio.sleep(0.01)
    assert await feed(listener, room(0.04)) == "end"


async def test_a_pause_after_an_unfinished_thought_waits_as_before() -> None:
    """It is the whole message that must sound finished, not the latest piece."""
    stt = FakeSTT()
    listener = Listener(stt, "one", on_partial=nothing)
    await listener.start()
    assert await feed(listener, room(0.2) + tone(0.8)) is None
    assert await feed(listener, room(0.3)) is None
    stt.stream.say("I was thinking that maybe we could and", final=True, pause=True)
    await asyncio.sleep(0.01)
    assert await feed(listener, room(0.2)) is None  # not cut off on "and"


async def test_a_judging_recognizer_ends_the_turn_on_done_and_reports_maybe() -> None:
    stt = FakeSTT(turns=True)
    seen: list[tuple[str, str]] = []

    async def on_turn(kind: str, text: str) -> None:
        seen.append((kind, text))

    listener = Listener(stt, "en", on_partial=nothing, on_turn=on_turn)
    await listener.start()
    assert await listener.audio(tone(0.6)) is None
    stt.stream.say("I need to remember to", final=False, turn="maybe")
    await asyncio.sleep(0)
    stt.stream.say("I need to remember to", final=False, turn="resumed")
    await asyncio.sleep(0)
    assert await listener.audio(room(2.5)) is None  # a pause, not the end
    stt.stream.say("I need to remember to call the dentist", final=True, turn="done")
    await asyncio.sleep(0)
    assert await listener.audio(room(0.04)) == "end"
    assert seen == [("maybe", "I need to remember to"), ("resumed", "I need to remember to")]
    said = await listener.end()
    assert said.text == "I need to remember to call the dentist"
