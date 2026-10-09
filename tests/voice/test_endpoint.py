"""When has the user stopped talking? (VOI-3, VOI-4, VOI-15a)"""

from __future__ import annotations

from tests.voice.audio import dead, frames, room, tone
from trillion.voice.endpoint import DEAD_AFTER_S, FAST_S, SLOW_S, EndOfSpeech, level


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


def test_a_second_of_quiet_after_speech_ends_the_turn() -> None:
    rules = EndOfSpeech(streaming=False)
    assert run(rules, room(0.5) + tone(1.0)) is None and rules.spoke
    assert run(rules, room(SLOW_S - 0.2)) is None  # a pause, not the end
    assert run(rules, room(0.4)) == "end"
    assert abs(rules.now - (1.5 + SLOW_S)) < 0.1


def test_a_pause_mid_thought_does_not_end_it() -> None:
    rules = EndOfSpeech(streaming=False)
    said = room(0.3) + tone(0.8) + room(0.7) + tone(0.8) + room(0.7) + tone(0.5)
    assert run(rules, said) is None
    assert run(rules, room(1.2)) == "end"


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
