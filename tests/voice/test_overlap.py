"""Talking over her (VOI-9): an acknowledgement lets her carry on, anything else stops her."""

import pytest

from trillion.voice.overlap import ACK_AFTER_S, DECIDE_BY_S, decide

PLAYING = ("We could leave at six.", "Or if traffic is bad, at half past.")


@pytest.mark.parametrize(
    "words",
    [
        "yeah",
        "mm-hm",
        "mm-hmm",
        "Mm-hmm.",
        "mmhmm",
        "mhm",
        "Right.",
        "okay",
        "got it",
        "i see",
        "uh-huh yeah",
        "Uh-huh.",
        "uh huh",
        "uh huh yeah",
    ],
)
def test_acknowledgements_carry_on_once_done_or_after_a_moment(words: str) -> None:
    assert decide(words, done=False, elapsed_s=0.3, playing=PLAYING) == "wait"
    assert decide(words, done=True, elapsed_s=0.3, playing=PLAYING) == "carry_on"
    assert decide(words, done=False, elapsed_s=ACK_AFTER_S, playing=PLAYING) == "carry_on"


@pytest.mark.parametrize(
    "words",
    [
        "wait",
        "no",
        "stop",
        "yeah but",
        "actually",
        "tell me more",
        "forgot it",
        "got it wrong",
        "i seem lost",
        "uh huh no",
    ],
)
def test_anything_else_interrupts_at_once(words: str) -> None:
    assert decide(words, done=False, elapsed_s=0.1, playing=PLAYING) == "interrupt"


def test_four_acknowledgements_are_more_than_an_acknowledgement() -> None:
    assert decide("yeah yeah yeah yeah", done=True, elapsed_s=0.5, playing=PLAYING) == "interrupt"
    assert decide("yeah yeah yeah", done=True, elapsed_s=0.5, playing=PLAYING) == "carry_on"
    assert decide("uh huh uh huh uh huh uh huh", done=True, elapsed_s=0.5, playing=PLAYING) == (
        "interrupt"
    )


def test_no_words_carry_on_only_at_the_limit() -> None:
    assert decide("", done=False, elapsed_s=0.5, playing=PLAYING) == "wait"
    assert decide("", done=False, elapsed_s=DECIDE_BY_S, playing=PLAYING) == "carry_on"


def test_her_own_voice_coming_back_is_ignored() -> None:
    assert decide("traffic is bad", done=False, elapsed_s=0.4, playing=PLAYING) == "wait"
    assert decide("leave at six", done=True, elapsed_s=0.4, playing=PLAYING) == "wait"
    assert decide("leave at six", done=False, elapsed_s=DECIDE_BY_S, playing=PLAYING) == "carry_on"


def test_an_acknowledgement_she_also_said_still_ends_in_carry_on() -> None:
    playing = ("Okay, we could leave at six.",)
    assert decide("okay", done=False, elapsed_s=DECIDE_BY_S, playing=playing) == "carry_on"


def test_a_stop_word_is_never_taken_for_echo() -> None:
    playing = ("You can stop the timer any time.",)
    assert decide("stop", done=False, elapsed_s=0.1, playing=playing) == "interrupt"
    assert decide("no", done=False, elapsed_s=0.1, playing=("No, the train.",)) == "interrupt"


def test_a_growing_transcript_changes_its_answer() -> None:
    assert decide("yeah", done=False, elapsed_s=0.3, playing=PLAYING) == "wait"
    assert decide("yeah but", done=False, elapsed_s=0.6, playing=PLAYING) == "interrupt"


def test_nothing_playing_means_nothing_is_echo() -> None:
    assert decide("six", done=False, elapsed_s=0.1, playing=()) == "interrupt"
    assert decide("yeah", done=True, elapsed_s=0.1, playing=()) == "carry_on"


@pytest.mark.parametrize("words", ["停", "нет", "10", "don't"])
def test_words_in_any_script_or_digits_are_words(words: str) -> None:
    assert decide(words, done=False, elapsed_s=0.1, playing=PLAYING) == "interrupt"


def test_another_language_interrupts() -> None:
    assert decide("oui", done=True, elapsed_s=0.3, playing=PLAYING) == "interrupt"
