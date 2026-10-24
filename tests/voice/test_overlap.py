"""Talking over her (VOI-9): an acknowledgement lets her carry on, anything else stops her."""

import pytest

from trillion.voice.overlap import (
    ACK_AFTER_S,
    DECIDE_BY_S,
    decide,
    judge_watch,
    judged_prefix,
    keys,
)

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


def test_curly_and_straight_apostrophes_match() -> None:
    playing = ("We don\u2019t need to leave yet.",)
    assert decide("don't need", done=False, elapsed_s=0.4, playing=playing) == "wait"


# ── Watching while she speaks: is it her, nothing, or maybe the user? ──

HER = (
    "No, the train leaves at six.",
    "We could take the bus instead.",
    "It stops right outside the station.",
)


def test_no_words_are_nothing() -> None:
    assert judge_watch("", HER) == "nothing"
    assert judge_watch(" ... ", HER) == "nothing"


def test_her_own_words_coming_back_are_echo() -> None:
    assert judge_watch("we could take the bus", HER) == "echo"
    # The sentence after the one being heard: her audio runs ahead of its reports.
    assert judge_watch("it stops right outside", HER) == "echo"


def test_echo_misheard_is_still_echo() -> None:
    # Most of the words are hers, a few came back garbled.
    assert judge_watch("we could tape the bus instead", HER) == "echo"
    assert judge_watch("the trained leaves at six", HER) == "echo"


def test_her_own_stop_words_do_not_stop_her() -> None:
    # She said "No, the train..." herself: it comes back, and it is still her.
    assert judge_watch("No, the train", HER) == "echo"
    assert judge_watch("no", HER) == "echo"


def test_words_that_are_not_hers_are_a_candidate() -> None:
    assert judge_watch("what about a taxi", HER) == "candidate"
    assert judge_watch("yeah", HER) == "candidate"  # deciding says it is an acknowledgement
    assert judge_watch("hang on", HER) == "candidate"


def test_the_user_amid_her_echo_is_a_candidate_once_mostly_theirs() -> None:
    assert judge_watch("bus wait what about a taxi", HER) == "candidate"
    # One stray word of theirs among hers is not enough.
    assert judge_watch("we could take the bus taxi", HER) == "echo"


def test_with_nothing_playing_every_word_is_a_candidate() -> None:
    assert judge_watch("the bus", ()) == "candidate"


def test_judged_words_are_counted_from_the_start() -> None:
    assert judged_prefix("We could take the bus", ("we", "could")) == 2
    assert judged_prefix("we could, take", ("we", "could")) == 2
    # Revised by the recognizer: nothing can be taken as already judged.
    assert judged_prefix("we would take", ("we", "could")) == 0
    assert judged_prefix("we", ("we", "could")) == 0
    assert judged_prefix("anything", ()) == 0


def test_word_keys_ignore_case_and_punctuation() -> None:
    assert keys("No, the TRAIN.") == ("no", "the", "train")
