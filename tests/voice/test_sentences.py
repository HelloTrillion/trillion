from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trillion.voice.sentences import MAX_CHARS, SentenceSplitter


def split(text: str, chunk: int = 10_000) -> list[str]:
    s = SentenceSplitter()
    out: list[str] = []
    for i in range(0, len(text), chunk):
        out += s.feed(text[i : i + chunk])
    return out + s.flush()


def test_sentences_come_out_as_soon_as_they_are_certainly_complete() -> None:
    s = SentenceSplitter()
    assert s.feed("The build is green.") == []  # might still become "green.5" or "green. x"
    assert s.feed(" Don't look") == ["The build is green."]
    assert s.feed(" directly at it. ") == []
    assert s.flush() == ["Don't look directly at it."]


BOUNDARIES = [
    ("Ask Dr. Smith about it. He knows.", ["Ask Dr. Smith about it.", "He knows."]),
    ("It costs 3.5 dollars today. Cheap.", ["It costs 3.5 dollars today.", "Cheap."]),
    (
        "Use a tool, e.g. the file reader. Fine.",
        ["Use a tool, e.g. the file reader.", "Fine."],
    ),
    (
        "Written by J. R. Hartley years ago. Good book.",
        ["Written by J. R. Hartley years ago.", "Good book."],
    ),
    (
        "Meet at 4 p.m. sharp at the office. Bring coffee.",
        ["Meet at 4 p.m. sharp at the office.", "Bring coffee."],
    ),
    (
        "Really?! That's a choice. An interesting one.",
        ["Really?! That's a choice.", "An interesting one."],
    ),
    (
        'She said "stop." Then she left the room.',
        ['She said "stop."', "Then she left the room."],
    ),
    ("Well… that happened today. Moving on.", ["Well… that happened today.", "Moving on."]),
]


@pytest.mark.parametrize(("text", "expected"), BOUNDARIES)
def test_boundaries(text: str, expected: list[str]) -> None:
    assert split(text) == expected


def test_short_fragments_ride_with_the_next_sentence() -> None:
    assert split("Yes. Long answer: also yes. Done.") == ["Yes. Long answer: also yes.", "Done."]


def test_a_paragraph_break_ends_a_sentence_without_punctuation() -> None:
    assert split("First thought\n\nSecond thought") == ["First thought", "Second thought"]


def test_a_long_run_without_punctuation_is_still_spoken() -> None:
    parts = split("word " * 200)
    assert len(parts) > 1 and all(len(p) <= MAX_CHARS for p in parts)


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 50])
def test_the_result_does_not_depend_on_how_the_text_arrived(chunk: int) -> None:
    text = (
        "Ask Dr. Smith. It costs 3.5 dollars, e.g. today. Really?! Yes.\n\n"
        "New paragraph here. Done."
    )
    assert split(text, chunk) == split(text)


@given(st.text(alphabet=st.sampled_from(list("ab .!?\n1")), max_size=300), st.integers(1, 9))
def test_nothing_is_lost_or_invented(text: str, chunk: int) -> None:
    spoken = "".join("".join(p.split()) for p in split(text, chunk))
    assert spoken == "".join(text.split())
