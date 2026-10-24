"""When has the user stopped talking? (VOI-3, VOI-4, VOI-15a)"""

from __future__ import annotations

from tests.voice.audio import dead, frames, room, tone
from trillion.voice.endpoint import (
    BATCH_S,
    DEAD_AFTER_S,
    DONE_S,
    FAST_S,
    HELD_MAX_S,
    HELD_QUIET_S,
    SLOW_S,
    EndOfSpeech,
    leads_on,
    level,
    sounds_finished,
)


def run(rules: EndOfSpeech, pcm: bytes) -> str | None:
    for frame in frames(pcm):
        verdict = rules.audio(frame)
        if verdict is not None:
            return verdict
    return None


def test_level_is_zero_for_silence_and_survives_an_odd_byte() -> None:
    assert level(b"") == 0.0 and level(dead(0.1)) == 0.0
    assert 0.1 < level(tone(0.1)) < 0.2
    assert level(tone(0.1) + b"\x01") == level(tone(0.1))


def test_a_second_of_quiet_ends_a_streaming_turn_with_no_final() -> None:
    rules = EndOfSpeech(streaming=True)
    assert run(rules, room(0.5) + tone(1.0)) is None and rules.spoke
    assert run(rules, room(SLOW_S - 0.2)) is None  # a pause, not the end
    assert run(rules, room(0.4)) == "end"
    assert abs(rules.now - (1.5 + SLOW_S)) < 0.1


def test_without_a_streaming_recognizer_two_seconds_of_quiet_end_the_turn() -> None:
    """Local Whisper hears nothing until the turn is over: quiet is the only sign, so
    a thinking pause of a second or more must not end it."""
    rules = EndOfSpeech(streaming=False)
    assert run(rules, room(0.5) + tone(1.0)) is None and rules.spoke
    assert run(rules, room(1.5)) is None  # thinking, not finished
    assert run(rules, room(BATCH_S - 1.5 + 0.1)) == "end"
    assert abs(rules.now - (1.5 + BATCH_S)) < 0.15


def test_a_pause_mid_thought_does_not_end_it() -> None:
    rules = EndOfSpeech(streaming=False)
    said = room(0.3) + tone(0.8) + room(1.2) + tone(0.8) + room(1.6) + tone(0.5)
    assert run(rules, said) is None
    assert run(rules, room(BATCH_S + 0.2)) == "end"


def test_a_click_or_cough_is_not_speech() -> None:
    rules = EndOfSpeech(streaming=False, patience_s=3.0)
    assert run(rules, room(0.5) + tone(0.03) + room(2.0)) is None and not rules.spoke
    assert run(rules, room(1.0)) == "nothing"


def test_a_dead_microphone_is_called_out_quickly() -> None:
    rules = EndOfSpeech(streaming=True)
    assert run(rules, dead(DEAD_AFTER_S + 0.2)) == "inaudible"
    # A quiet room is not a dead microphone: that one just waits for a voice.
    quiet = EndOfSpeech(streaming=True, patience_s=4.0)
    assert run(quiet, room(DEAD_AFTER_S + 0.5)) is None
    assert run(quiet, room(1.5)) == "nothing"


def test_a_noisy_room_is_learned_and_does_not_count_as_speech() -> None:
    rules = EndOfSpeech(streaming=False, patience_s=5.0)
    assert run(rules, room(4.0, loud=0.006)) is None and not rules.spoke
    assert run(rules, tone(0.5, loud=0.15)) is None and rules.spoke


def test_a_final_transcript_ends_the_turn_sooner() -> None:
    rules = EndOfSpeech(streaming=True)
    assert run(rules, room(0.3) + tone(1.0)) is None
    rules.heard(final=False, has_text=True)
    assert run(rules, room(0.2)) is None
    rules.heard(final=True, has_text=True)
    assert run(rules, room(FAST_S - 0.1)) is None
    assert run(rules, room(0.2)) == "end"
    assert rules.now < 1.3 + 0.2 + SLOW_S  # sooner than quiet alone would have ended it


def test_quiet_is_not_believed_while_words_are_still_arriving() -> None:
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.3) + tone(0.5))
    # The meter loses the voice, but the recognizer keeps reporting words.
    for _ in range(6):
        assert run(rules, room(0.3)) is None
        rules.heard(final=False, has_text=True)
    assert run(rules, room(SLOW_S + 0.2)) == "end"


def test_a_final_does_not_end_a_thought_the_user_carries_on_with() -> None:
    """One message can span several final segments (VOI-15a)."""
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.3) + tone(0.8))
    rules.heard(final=True, has_text=True)
    assert run(rules, room(0.2) + tone(0.8)) is None  # they kept talking
    rules.heard(final=False, has_text=True)
    assert run(rules, room(0.3)) is None
    rules.heard(final=True, has_text=True)
    assert run(rules, room(FAST_S + 0.1)) == "end"


def test_words_alone_count_as_speech_when_the_meter_hears_nothing() -> None:
    rules = EndOfSpeech(streaming=True)
    run(rules, room(1.0))
    rules.heard(final=True, has_text=True)
    assert rules.spoke
    assert run(rules, room(FAST_S + 0.1)) == "end"


def test_an_empty_final_is_not_speech() -> None:
    rules = EndOfSpeech(streaming=True, patience_s=2.0)
    run(rules, room(1.0))
    rules.heard(final=True, has_text=False)
    assert not rules.spoke
    assert run(rules, room(1.2)) == "nothing"


def test_the_end_of_speech_is_when_the_voice_stopped_not_when_words_arrived() -> None:
    """ONB-11: a streaming recognizer reports words after they were said. The turn's
    clock starts when the voice went quiet, or the trace would flatter every turn."""
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.2) + tone(0.6))  # speaking ends 0.8s in
    run(rules, room(0.3))
    rules.heard(final=True, has_text=True)  # the words arrive, late, at 1.1s
    run(rules, room(0.1))
    assert 0.35 <= rules.quiet_s <= 0.45  # quiet since 0.8s, not since 1.1s


def test_a_voice_too_quiet_to_measure_falls_back_to_the_words() -> None:
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.5))
    rules.heard(final=True, has_text=True)
    run(rules, room(0.2))
    assert 0.15 <= rules.quiet_s <= 0.25


def test_a_finished_question_ends_as_soon_as_the_recognizer_hears_the_pause() -> None:
    """Latency: when the words read as finished and the recognizer heard the pause,
    the turn ends then, not 0.4s after the late final words arrive."""
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.3) + tone(1.0))  # the voice stops at 1.3s
    rules.heard(final=False, has_text=True)
    assert run(rules, room(0.5)) is None
    rules.heard(final=True, has_text=True, done=True)  # Deepgram's final, ~0.5s late
    assert run(rules, room(0.04)) == "end"
    assert rules.now < 1.3 + 0.6


def test_a_thought_that_does_not_sound_finished_waits_as_before() -> None:
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.3) + tone(1.0))
    run(rules, room(0.5))
    rules.heard(final=True, has_text=True, done=False)  # "...and I was thinking"
    assert run(rules, room(FAST_S - 0.1)) is None
    assert run(rules, room(0.2)) == "end"


def test_done_is_never_believed_over_a_voice_that_is_still_going() -> None:
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.3) + tone(1.0))
    rules.heard(final=True, has_text=True, done=True)
    assert run(rules, tone(0.5)) is None  # they kept talking
    assert run(rules, room(DONE_S - 0.1)) is None


def test_new_words_after_a_finished_sentence_undo_it() -> None:
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.3) + tone(1.0) + room(0.3))
    rules.heard(final=True, has_text=True, done=True)
    rules.heard(final=False, has_text=True)  # "...and another thing"
    assert run(rules, room(0.3)) is None


def test_an_empty_final_does_not_undo_a_finished_sentence() -> None:
    rules = EndOfSpeech(streaming=True)
    run(rules, room(0.3) + tone(1.0))
    run(rules, room(0.3))
    rules.heard(final=True, has_text=True, done=True)
    rules.heard(final=True, has_text=False)  # the recognizer reporting silence
    assert run(rules, room(0.04)) == "end"


def test_what_sounds_finished() -> None:
    finished = ["What is the tallest mountain in the world?", "Okay.", "Thanks!", 'In "Nepal."']
    unfinished = ["set a timer for", "I want to go and.", "So um.", "Hmm...", "Hmm…", "", "and."]
    assert all(sounds_finished(t) for t in finished)
    assert not any(sounds_finished(t) for t in unfinished)


def test_a_recognizer_that_judges_turns_decides_the_end() -> None:
    """VOI-12: with `judged`, silence never ends the turn; the recognizer's done does."""
    rules = EndOfSpeech(streaming=True, judged=True)
    assert rules.audio(tone(0.6)) is None
    rules.heard(final=False, has_text=True)
    # Three seconds of quiet: the old rules would have ended it at one.
    assert rules.audio(room(3.0)) is None
    rules.turn_over()
    assert rules.audio(room(0.04)) == "end"


def test_judged_rules_still_say_nothing_and_inaudible() -> None:
    assert EndOfSpeech(streaming=True, judged=True, patience_s=1.0).audio(room(1.2)) == "nothing"
    assert EndOfSpeech(streaming=True, judged=True).audio(bytes(16_000 * 2 * 3)) == "inaudible"


def test_what_leads_on() -> None:
    """A recognizer may give no punctuation at all, so only a word or mark that leads
    somewhere counts; a missing full stop does not."""
    leading = ["I went to the shop and", "So um", "and.", "Hmm...", "Hmm\u2026", "first,", "well -"]
    ending = ["I went to the shop", "yeah", "Call the dentist.", "What time is it?", ""]
    # A question or an exclamation is finished, whatever word it ends on.
    ending += ["Who's that?", "What is it for?", "So?", "And!"]
    assert all(leads_on(t) for t in leading)
    assert not any(leads_on(t) for t in ending)


def test_a_held_done_ends_after_more_quiet() -> None:
    """The recognizer said done on words that lead on: more quiet ends it after all."""
    rules = EndOfSpeech(streaming=True, judged=True)
    assert rules.audio(tone(0.6)) is None
    rules.heard(final=True, has_text=True)
    rules.hold()
    assert not rules.is_over
    assert rules.audio(room(HELD_QUIET_S - 0.1)) is None
    assert rules.audio(room(0.2)) == "end"
    assert rules.is_over  # what follows is the next thought


def test_held_quiet_counts_from_when_the_voice_stopped() -> None:
    """An early done waits about 2.5 s of silence in all; a done the recognizer forced
    after a long silence ends at once."""
    early = EndOfSpeech(streaming=True, judged=True)
    early.audio(tone(0.6) + room(0.7))
    early.heard(final=True, has_text=True)
    early.hold()
    assert early.audio(room(HELD_QUIET_S - 0.8)) is None
    assert early.audio(room(0.2)) == "end"
    late = EndOfSpeech(streaming=True, judged=True)
    late.audio(tone(0.6) + room(5.0))
    late.heard(final=True, has_text=True)
    late.hold()
    assert late.audio(room(0.04)) == "end"


def test_a_held_done_ends_within_the_cap_whatever_noise_goes_on() -> None:
    """A breath, a keyboard or a television must not keep a held turn open."""
    rules = EndOfSpeech(streaming=True, judged=True)
    rules.audio(tone(0.6))
    start = rules.now
    rules.heard(final=True, has_text=True)
    rules.hold()
    assert rules.audio((tone(0.3) + room(1.0)) * 6) == "end"
    assert rules.now - start <= HELD_MAX_S + 0.05


def test_an_empty_done_does_not_restart_a_hold() -> None:
    rules = EndOfSpeech(streaming=True, judged=True)
    rules.audio(tone(0.6))
    rules.heard(final=True, has_text=True)
    rules.hold()
    rules.audio((tone(0.3) + room(1.0)) * 3)
    rules.heard(final=True, has_text=False)
    rules.hold()
    assert rules.audio((tone(0.3) + room(1.0)) * 2) == "end"


def test_a_voice_after_a_held_done_starts_the_quiet_again() -> None:
    rules = EndOfSpeech(streaming=True, judged=True)
    rules.audio(tone(0.6))
    rules.heard(final=True, has_text=True)
    rules.hold()
    assert rules.audio(room(2.0) + tone(0.3) + room(2.0)) is None
    assert rules.audio(room(0.6)) == "end"


def test_new_words_after_a_held_done_leave_the_end_to_the_recognizer() -> None:
    rules = EndOfSpeech(streaming=True, judged=True)
    rules.audio(tone(0.6))
    rules.heard(final=True, has_text=True)
    rules.hold()
    rules.heard(final=False, has_text=True)
    assert rules.audio(room(4.0)) is None
