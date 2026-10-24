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
from trillion.voice.types import VoiceError, Word


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


# ── Watching while she speaks (VOI-9) ────────────────────────────────────


async def watcher(stt: FakeSTT) -> Listener:
    listener = Listener(stt, "en", on_partial=nothing, watch=True)
    await listener.start()
    return listener


async def test_watching_hears_one_recognizer_turn_at_a_time_and_never_ends() -> None:
    stt = FakeSTT(turns=True)
    listener = await watcher(stt)
    assert listener.watching
    # Her echo, a turn the recognizer ends: nothing ends here, however long it goes.
    assert await feed(listener, tone(10.0)) is None
    stt.stream.say("we could leave", final=False)
    await asyncio.sleep(0.01)
    assert listener.turn_text() == "we could leave"
    stt.stream.say("We could leave at six.", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert listener.turn_text() == "We could leave at six."  # until the next turn starts
    stt.stream.say("or later", final=False)
    await asyncio.sleep(0.01)
    assert listener.turn_text() == "or later" and listener.text() == "or later"
    assert await feed(listener, room(130.0)) is None  # never too long, never nothing
    assert stt.stream.audio  # every frame went to the recognizer
    await listener.abandon()


async def test_hearing_after_watching_ends_on_the_turn_and_keeps_its_words() -> None:
    stt = FakeSTT(turns=True)
    listener = await watcher(stt)
    await feed(listener, tone(3.0))
    stt.stream.say("we could leave at six", final=True, turn="done")
    stt.stream.say("wait", final=False)
    await asyncio.sleep(0.01)
    listener.hear()
    assert not listener.watching and listener.text() == "wait"
    assert await feed(listener, tone(0.3)) is None
    stt.stream.say("wait, make it seven", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert await feed(listener, room(0.1)) == "end"
    assert (await listener.end()).text == "wait, make it seven"


async def test_a_turn_already_done_when_hearing_starts_is_done() -> None:
    stt = FakeSTT(turns=True)
    listener = await watcher(stt)
    stt.stream.say("yeah", final=True, turn="done")
    await asyncio.sleep(0.01)
    listener.hear()
    assert await feed(listener, room(0.1)) == "end"
    assert listener.text() == "yeah"
    await listener.abandon()


async def test_back_to_watching_after_a_decided_turn() -> None:
    stt = FakeSTT(turns=True)
    listener = await watcher(stt)
    stt.stream.say("yeah", final=True, turn="done")
    await asyncio.sleep(0.01)
    listener.hear()
    assert await feed(listener, room(0.1)) == "end"
    listener.watch()
    assert listener.watching and listener.verdict is None
    assert await feed(listener, tone(1.0)) is None
    stt.stream.say("next words", final=False)
    await asyncio.sleep(0.01)
    assert listener.turn_text() == "next words"
    await listener.abandon()


async def test_a_watch_becomes_a_patient_follow_up_window() -> None:
    stt = FakeSTT(turns=True)
    listener = await watcher(stt)
    stt.stream.say("of course", final=False)
    await asyncio.sleep(0.01)
    # Her echo, judged not theirs: the window waits for their words.
    listener.hear(patience_s=3.0, judge=lambda words: [False] * len(words))
    assert listener.text() == "" and listener.raw_text() == "of course"
    assert await feed(listener, room(3.1)) == "nothing"
    await listener.abandon()


async def test_a_watch_knows_whether_the_recognizer_turn_is_still_going() -> None:
    stt = FakeSTT(turns=True)
    listener = await watcher(stt)
    assert not listener.turn_open
    stt.stream.say("third thing", final=False)
    await asyncio.sleep(0.01)
    assert listener.turn_open
    stt.stream.say("third thing to know", final=True, turn="done")
    await asyncio.sleep(0.01)
    assert not listener.turn_open
    await listener.abandon()


async def reported(listener: Listener, text: str) -> None:
    """Wait until the recognizer's report has been read."""
    for _ in range(400):
        if listener.raw_text() == text:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never read: {text!r}")


async def test_a_judge_keeps_only_their_words_spelled_as_heard() -> None:
    """Heard over her (#102): her echo before, between or after their words is left out,
    and their words keep the recognizer's punctuation."""
    stt = FakeSTT(turns=True, word_times=True)
    listener = await watcher(stt)
    said = "or writes What about Tuesday? the rest"
    words = tuple(
        Word(text=t.strip("?"), start_s=i * 0.3, end_s=i * 0.3 + 0.2)
        for i, t in enumerate(said.split())
    )
    stt.stream.say(said, final=False, words=words)
    await reported(listener, said)
    theirs = {"What", "about", "Tuesday"}
    listener.hear(judge=lambda ws: [w.text in theirs for w in ws])
    assert listener.text() == "What about Tuesday?"
    assert listener.raw_text() == said
    await listener.abandon()


async def test_words_without_times_are_placed_at_their_report_less_the_lag() -> None:
    stt = FakeSTT(turns=True)
    listener = await watcher(stt)
    await feed(listener, tone(2.0))
    stt.stream.say("skip", final=False)
    await reported(listener, "skip")
    (skip,) = listener.turn_words()
    assert skip.mid_s == pytest.approx(2.0 - listening.UNTIMED_LAG_S, abs=0.07)
    await feed(listener, tone(1.0))
    stt.stream.say("skip that", final=False)
    await reported(listener, "skip that")
    # The word already placed keeps its time; the new one is placed by its report.
    first, that = listener.turn_words()
    assert first == skip
    assert that.mid_s == pytest.approx(3.0 - listening.UNTIMED_LAG_S, abs=0.07)
    await listener.abandon()


# ── The recognizer's clock, mic levels and timed words (#102) ────────────


async def test_audio_sent_counts_the_backlog_on_the_recognizers_clock() -> None:
    stt = FakeSTT(turns=True, word_times=True)
    stt.opening.clear()
    listener = Listener(stt, "en", on_partial=nothing, watch=True)
    starting = asyncio.create_task(listener.start())
    await asyncio.sleep(0)
    await feed(listener, tone(0.3))
    assert listener.sent_s == 0.0  # kept, not yet sent
    stt.opening.set()
    await starting
    await feed(listener, room(0.5))
    assert listener.sent_s == pytest.approx(0.8)
    assert listener.sent_s == len(stt.stream.audio) / 32_000
    await listener.abandon()


async def test_mic_level_is_the_loudness_of_audio_sent_over_a_span() -> None:
    stt = FakeSTT(turns=True, word_times=True)
    listener = await watcher(stt)
    await feed(listener, room(1.0) + tone(0.5))
    loud, quiet = listener.mic_level(1.1, 1.4), listener.mic_level(0.2, 0.8)
    assert loud == pytest.approx(0.2 / 2**0.5, rel=0.05)
    assert quiet < 0.002 and loud > 50 * quiet
    assert listener.mic_level(5.0, 6.0) == 0.0  # nothing sent then
    await listener.abandon()


async def test_mic_levels_are_kept_for_thirty_seconds() -> None:
    stt = FakeSTT(turns=True, word_times=True)
    listener = await watcher(stt)
    await feed(listener, tone(1.0) + room(31.0))
    assert listener.mic_level(0.0, 1.0) == 0.0  # let go
    assert listener.mic_level(31.0, 32.0) > 0.0
    await listener.abandon()


async def test_turn_words_are_the_latest_of_the_recognizers_turn() -> None:
    stt = FakeSTT(turns=True, word_times=True)
    listener = await watcher(stt)
    assert listener.turn_words() == () and listener.ended_words() == ()
    we = Word(text="we", start_s=0.5, end_s=0.7)
    could = Word(text="could", start_s=0.7, end_s=0.9)
    stt.stream.say("we", final=False, words=(we,))
    await reported(listener, "we")
    assert listener.turn_words() == (we,)
    stt.stream.say("we could", final=False, words=(we, could))  # the whole turn again
    await reported(listener, "we could")
    assert listener.turn_words() == (we, could)
    stt.stream.say("We could.", final=True, turn="done", words=(we, could))
    await reported(listener, "We could.")
    assert listener.turn_words() == (we, could)  # the finished turn's, until the next
    stt.stream.say("hm", final=False)  # the next turn, its words not timed
    await reported(listener, "hm")
    assert [w.text for w in listener.turn_words()] == ["hm"]  # placed by its report
    assert listener.ended_words() == (we, could)
    await listener.abandon()


async def test_a_done_without_words_keeps_the_turns_words() -> None:
    stt = FakeSTT(turns=True, word_times=True)
    listener = await watcher(stt)
    yes = Word(text="yes", start_s=1.0, end_s=1.2)
    stt.stream.say("yes", final=False, words=(yes,))
    await reported(listener, "yes")
    stt.stream.say("", final=True, turn="done")
    for _ in range(100):
        if listener.turn_done:
            break
        await asyncio.sleep(0.005)
    assert listener.turn_done and listener.turn_words() == (yes,)
    await listener.abandon()


async def test_room_level_is_the_quiet_with_no_words_near() -> None:
    stt = FakeSTT(turns=True, word_times=True)
    listener = await watcher(stt)
    assert listener.room_level() == 0.002  # nothing known yet
    await feed(listener, room(1.0) + tone(2.0))
    assert listener.room_level() > 0.1  # the voice, with no words to place it
    words = (Word(text="a", start_s=1.0, end_s=2.0), Word(text="b", start_s=2.0, end_s=3.0))
    stt.stream.say("a b", final=False, words=words)
    await reported(listener, "a b")
    assert listener.room_level() == pytest.approx(0.001 / 2**0.5, rel=0.1)
    # Her playback can be left out too.
    assert listener.room_level(busy=[(0.0, 1.0)]) == 0.002
    await listener.abandon()


async def test_levels_land_in_order_while_the_backlog_is_still_being_sent() -> None:
    """A frame that arrives while the backlog's send is still waiting comes after it."""
    release = asyncio.Event()

    class Slow(FakeSTT):
        async def open(self, *, choice: str):  # type: ignore[no-untyped-def]
            stream = await super().open(choice=choice)
            send, first = stream.send, [True]

            async def slow(pcm: bytes) -> None:
                if first[0]:
                    first[0] = False
                    await release.wait()
                await send(pcm)

            stream.send = slow  # type: ignore[method-assign]
            return stream

    stt = Slow(turns=True, word_times=True)
    stt.opening.clear()
    listener = Listener(stt, "en", on_partial=nothing, watch=True)
    starting = asyncio.create_task(listener.start())
    await asyncio.sleep(0)
    await listener.audio(room(0.3))  # kept until connected
    stt.opening.set()
    await asyncio.sleep(0.01)  # the backlog's send is now waiting
    later = asyncio.create_task(listener.audio(tone(0.1)))
    await asyncio.sleep(0.01)
    release.set()
    await starting
    await later
    assert listener.sent_s == pytest.approx(0.4)
    assert listener.mic_level(0.0, 0.3) < 0.002
    assert listener.mic_level(0.3, 0.4) > 0.1
    await listener.abandon()


async def test_segment_style_turn_words_join_finals_and_the_latest_interim() -> None:
    stt = FakeSTT(word_times=True)
    listener = await watcher(stt)
    a = Word(text="remind", start_s=0.2, end_s=0.4)
    b = Word(text="me", start_s=0.4, end_s=0.5)
    c = Word(text="to", start_s=0.6, end_s=0.7)
    d = Word(text="call", start_s=0.7, end_s=0.9)
    stt.stream.say("remind", final=True, words=(a,))
    stt.stream.say("me", final=True, words=(b,))
    stt.stream.say("to", final=False, words=(c,))
    await reported(listener, "remind me to")
    assert listener.turn_words() == (a, b, c)
    stt.stream.say("to call", final=False, words=(c, d))  # the interim replaced
    await reported(listener, "remind me to call")
    assert listener.turn_words() == (a, b, c, d)
    await listener.abandon()


async def test_room_level_leaves_out_earlier_turns_words() -> None:
    stt = FakeSTT(turns=True, word_times=True)
    listener = await watcher(stt)
    await feed(listener, room(1.0) + tone(2.0) + room(1.0))
    first = (Word(text="a", start_s=1.0, end_s=2.0),)
    stt.stream.say("a", final=True, turn="done", words=first)
    await reported(listener, "a")
    stt.stream.say("b", final=False, words=(Word(text="b", start_s=2.0, end_s=3.0),))
    await reported(listener, "b")
    stt.stream.say("b", final=True, turn="done")
    stt.stream.say("c", final=False)  # now "a" is two turns back
    await reported(listener, "c")
    assert listener.ended_words() != first
    assert listener.room_level() == pytest.approx(0.001 / 2**0.5, rel=0.1)
    await listener.abandon()
