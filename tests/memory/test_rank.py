"""Which memories a question is about (MEM-2, keyword path)."""

from __future__ import annotations

from trillion.memory.rank import rank, words
from trillion.memory.types import Memory


def mem(i: int, text: str, as_of: int = 0) -> Memory:
    return Memory(
        id=f"mem_{i:012x}", type="user", text=text, source="user_statement", moves=False,
        created_at=as_of, as_of=as_of,
    )  # fmt: skip


BOOK = [
    mem(1, "I take my coffee black."),
    mem(2, "My sister Ana lives in Lisbon."),
    mem(3, "The quarterly report is in the Finance folder on the shared drive."),
    mem(4, "I am allergic to peanuts."),
    mem(5, "Prefers short answers without bullet points."),
    mem(6, "Order number 48213 is the standing coffee subscription."),
]


def texts(query: str, limit: int = 3) -> list[str]:
    return [m.text for m in rank(BOOK, query, limit)]


def test_words_drop_filler_and_join_plurals() -> None:
    assert words("How do I take my coffee?") == ["take", "coffee"]
    assert words("coffees, Coffee; COFFEE") == ["coffee"] * 3
    assert words("glass class 48213 a I") == ["glass", "class", "48213"]
    assert words("Où habite ma sœur") == ["où", "habite", "ma", "sœur"]


def test_a_question_finds_the_memory_that_answers_it() -> None:
    assert texts("How do I take my coffee?")[0] == "I take my coffee black."
    assert texts("where does my sister live")[0] == "My sister Ana lives in Lisbon."
    assert texts("Where is the quarterly report?")[0].startswith("The quarterly report")
    assert texts("what's order 48213")[0].startswith("Order number 48213")


def test_nothing_shared_means_nothing_recalled() -> None:
    assert texts("what is the weather in Tokyo") == []
    assert texts("") == [] and texts("how do you do") == []
    assert rank([], "coffee", 3) == []


def test_the_limit_holds_and_newer_wins_a_tie() -> None:
    old, new = mem(7, "The gym code is 1234.", as_of=1), mem(8, "The gym code is 1234.", as_of=2)
    assert rank([old, new], "gym code", 5) == [new, old]
    assert len(rank(BOOK, "coffee", 1)) == 1
