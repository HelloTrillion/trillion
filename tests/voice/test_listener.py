"""One utterance, from the microphone opening to the words (VOI-2, VOI-15, VOI-15a)."""

from __future__ import annotations

import asyncio

import pytest

from tests.voice.audio import frames, room, tone
from tests.voice.fakes import FakeSTT
from trillion.api import listening
from trillion.api.listening import Listener, Utterance
from trillion.voice.endpoint import HELD_QUIET_S
from trillion.voice.overlap import decide
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
    await asyncio.sleep(0.01)
    stt.stream.say("I need to remember to", final=False, turn="resumed")
    await asyncio.sleep(0.01)
    assert await listener.audio(room(2.5)) is None  # a pause, not the end
    stt.stream.say("I need to remember to call the dentist", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert await listener.audio(room(0.04)) == "end"
    assert seen == [("maybe", "I need to remember to"), ("resumed", "I need to remember to")]
    said = await listener.end()
    assert said.text == "I need to remember to call the dentist"


async def test_a_failing_turn_handler_does_not_stop_hearing() -> None:
    stt = FakeSTT(turns=True)

    async def broken(kind: str, text: str) -> None:
        raise RuntimeError("boom")

    listener = Listener(stt, "en", on_partial=nothing, on_turn=broken)
    await listener.start()
    await listener.audio(tone(0.6))
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    stt.stream.say("call the dentist", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert await listener.audio(room(0.04)) == "end"
    assert (await listener.end()).text == "call the dentist"


async def test_done_with_no_text_keeps_what_was_heard() -> None:
    stt = FakeSTT(turns=True)
    listener = Listener(stt, "en", on_partial=nothing)
    await listener.start()
    await listener.audio(tone(0.6))
    stt.stream.say("call the dentist", final=False)
    await asyncio.sleep(0.01)
    stt.stream.say("", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert await listener.audio(room(0.04)) == "end"
    assert (await listener.end()).text == "call the dentist"


async def test_nothing_after_done_joins_the_turn() -> None:
    stt = FakeSTT(turns=True)
    listener = Listener(stt, "en", on_partial=nothing)
    await listener.start()
    await listener.audio(tone(0.6))
    stt.stream.say("call the dentist", final=True, turn="done")
    stt.stream.say("and then", final=False)
    stt.stream.say("and then the bank", final=True)
    await asyncio.sleep(0.01)
    assert (await listener.end()).text == "call the dentist"


async def test_a_recognizer_that_does_not_judge_never_calls_on_turn() -> None:
    stt = FakeSTT(turns=False)
    seen: list[str] = []

    async def on_turn(kind: str, text: str) -> None:
        seen.append(kind)

    listener = Listener(stt, "en", on_partial=nothing, on_turn=on_turn)
    await listener.start()
    stt.stream.say("hello", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    assert seen == []
    await listener.abandon()


async def test_a_judged_turn_still_stops_when_it_runs_too_long() -> None:
    stt = FakeSTT(turns=True)
    listener = Listener(stt, "en", on_partial=nothing)
    await listener.start()
    assert await feed(listener, tone(125.0)) == "too_long"
    await listener.abandon()


async def test_a_done_that_leads_on_keeps_listening_and_joins_what_follows() -> None:
    """The recognizer may end a turn on "and"; she waits, and the next turn's words
    join it. A held done reads as probably finished; new words as carrying on."""
    stt = FakeSTT(turns=True)
    seen: list[tuple[str, str]] = []

    async def on_turn(kind: str, text: str) -> None:
        seen.append((kind, text))

    listener = Listener(stt, "en", on_partial=nothing, on_turn=on_turn)
    await listener.start()
    assert await listener.audio(tone(0.6)) is None
    stt.stream.say("I went to the shop and", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert await listener.audio(room(1.0)) is None
    assert listener.text() == "I went to the shop and"
    stt.stream.say("bought", final=False)
    await asyncio.sleep(0.01)
    assert listener.text() == "I went to the shop and bought"
    stt.stream.say("bought some milk", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    assert await listener.audio(tone(0.5) + room(1.0)) is None
    stt.stream.say("bought some milk", final=True, turn="done")
    stt.stream.say("and then", final=False)  # the next turn: not part of this one
    await asyncio.sleep(0.01)
    assert await listener.audio(room(0.04)) == "end"
    assert seen == [
        ("maybe", "I went to the shop and"),
        ("resumed", "I went to the shop and bought"),
        ("maybe", "I went to the shop and bought some milk"),
    ]
    assert (await listener.end()).text == "I went to the shop and bought some milk"


async def test_a_done_that_leads_on_ends_after_more_quiet() -> None:
    stt = FakeSTT(turns=True)
    seen: list[tuple[str, str]] = []

    async def on_turn(kind: str, text: str) -> None:
        seen.append((kind, text))

    listener = Listener(stt, "en", on_partial=nothing, on_turn=on_turn)
    await listener.start()
    assert await listener.audio(tone(0.6)) is None
    stt.stream.say("I went to the shop and um", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert await listener.audio(room(HELD_QUIET_S - 0.1)) is None
    assert await listener.audio(room(0.2)) == "end"
    stt.stream.say("and then", final=False)  # the next thought, while wrapping up
    await asyncio.sleep(0.01)
    assert (await listener.end()).text == "I went to the shop and um"
    assert seen == [("maybe", "I went to the shop and um")]  # no resumed


async def test_an_acknowledgement_over_her_is_done_at_once() -> None:
    """Over her, "yeah" has no full stop but does not lead on: done, so she carries on."""
    stt = FakeSTT(turns=True)
    listener = Listener(stt, "en", on_partial=nothing)
    await listener.start()
    await listener.audio(tone(0.4))
    stt.stream.say("yeah", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert await listener.audio(room(0.04)) == "end"
    decision = decide(listener.text(), done=True, elapsed_s=0.5, playing=["We could leave at six."])
    assert decision == "carry_on"
    await listener.abandon()


async def test_a_word_that_leads_on_over_her_is_decided_by_its_words() -> None:
    """Over her, "so" is held as a turn's end, but deciding goes by the words, not by
    the end: anything but an acknowledgement stops her at once."""
    stt = FakeSTT(turns=True)
    listener = Listener(stt, "en", on_partial=nothing)
    await listener.start()
    await listener.audio(tone(0.4))
    stt.stream.say("so", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert await listener.audio(room(0.04)) is None
    decision = decide(listener.text(), done=False, elapsed_s=0.5, playing=["We could leave."])
    assert decision == "interrupt"
    await listener.abandon()


def test_describe_reports_a_judging_recognizer() -> None:
    described = listening.describe(FakeSTT(turns=True), "deepgram", "x")
    assert described is not None and described["turns"] is True
