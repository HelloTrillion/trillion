"""The persona eval's code checks (EVL-1), against stored good and bad replies."""

from __future__ import annotations

import pytest
from evals.persona.checks import (
    as_an_ai,
    banned_opener,
    markdown,
    repeated_openers,
    sentences,
    too_long,
    words,
)


@pytest.mark.parametrize(
    "reply",
    [
        "Great question! Lunch is soup.",
        "great question, honestly. Soup.",
        "Oh, great question. Soup.",
        "That's a great question. Soup.",
        "I'd be happy to help with that.",
        "I\u2019d be happy to.",
        "I would be happy to look.",
        "I understand. Let me look.",
        "Based on the information available, soup.",
        "Here are three things to consider: cost, time, taste.",
        "Certainly! Soup.",
        "Absolutely! Friday works.",
    ],
)
def test_a_banned_opener_is_caught(reply: str) -> None:
    assert banned_opener(reply) is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Soup. It's cold out.",
        "Soup. Great question, though.",  # an opener in the middle is fine
        "Sure. What breaks first?",
        "I understand the plan. What breaks first?",
        "Absolutely not. Friday is too soon.",
        "Here's the thing: it's Tuesday.",
    ],
)
def test_a_good_opener_passes(reply: str) -> None:
    assert banned_opener(reply) is None


@pytest.mark.parametrize(
    "reply",
    [
        "As an AI, I can't eat.",
        "I'm an AI, so no.",
        "I am an AI.",
        "I'm just a large language model.",
        "I don't have feelings, but congratulations.",
        "I don\u2019t have feelings.",
    ],
)
def test_as_an_ai_is_caught(reply: str) -> None:
    assert as_an_ai(reply) is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Yes. I'm Nova, and I run on your computer.",
        "Congratulations. That's huge.",
        "The AI hype is loud this year.",
    ],
)
def test_talking_about_ai_is_not_as_an_ai(reply: str) -> None:
    assert as_an_ai(reply) is None


@pytest.mark.parametrize(
    "reply",
    [
        "**Soup.**",
        "# Lunch",
        "Use `ls`.",
        "Two:\n- soup\n- salad",
        "Two:\n1. soup\n2. salad",
        "Soup \u2014 it's cold.",
        "Soup \u2013 warm.",
    ],
)
def test_markdown_and_dashes_are_caught_when_spoken(reply: str) -> None:
    assert markdown(reply) is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Soup, it's cold.",
        "Open 9\u20135.",  # a dash inside a number is fine
        "The vote was 2\u20131.",
        "Call 555-0100.",
        "Use three or four cloves, not 3-4 heads.",
        "First soup, then salad.",
    ],
)
def test_plain_speech_passes(reply: str) -> None:
    assert markdown(reply) is None


def test_sentences_and_words_are_counted_as_heard() -> None:
    assert sentences("It's 3.5 percent. Fine! Really?") == ["It's 3.5 percent.", "Fine!", "Really?"]
    assert words("It's twenty-seven hundred, 3.5 percent.") == [
        "It's", "twenty-seven", "hundred", "3", "5", "percent",
    ]  # fmt: skip


def test_a_spoken_reply_is_at_most_three_sentences_and_45_words() -> None:
    assert too_long("Soup. It's cold out. Go.") is None
    assert too_long("Soup. It's cold. Go. Now.") == "4 sentences, 5 words"
    assert too_long(" ".join(["word"] * 46) + ".") == "1 sentences, 46 words"
    assert too_long(" ".join(["word"] * 45) + ".") is None


def test_a_long_bait_may_refuse_in_one_line_or_offer_the_board() -> None:
    one_line = "Five paragraphs is an essay, not a conversation, " + " ".join(["so"] * 50) + "."
    assert too_long(one_line, long_ok=True) is None
    board = "That's better seen than heard. It's on the board. Ten steps. Start with one."
    assert too_long(board, long_ok=True) is None
    assert too_long("One. Two. Three. Four.", long_ok=True) == "4 sentences, 4 words"


def test_no_two_replies_within_20_turns_open_with_the_same_three_words() -> None:
    replies = (
        ["Sure, soup works."]
        + [f"Other answer {i}." for i in range(4)]
        + ["Sure, soup works again."]
    )
    assert repeated_openers(replies) == [5]
    far = ["Sure, soup works."] + [f"Reply number {i}." for i in range(20)] + ["Sure, soup works."]
    assert repeated_openers(far) == []  # 21 apart
    edge = ["Sure, soup works."] + [f"Reply number {i}." for i in range(19)] + ["Sure, soup works."]
    assert repeated_openers(edge) == [20]  # 20 apart
    assert repeated_openers(["Yes.", "Yes.", "Yes."]) == []  # under three words: not compared
