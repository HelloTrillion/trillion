"""A local model's chat-template tokens never reach her words."""

from __future__ import annotations

import pytest

from trillion.brain.control_tokens import ControlTokenFilter


def run(*parts: str) -> str:
    f = ControlTokenFilter()
    return "".join(f.feed(p) for p in parts) + f.flush()


@pytest.mark.parametrize(
    "token",
    [
        "<channel|>",
        "<|channel|>",
        "<|channel>",
        "<|im_end|>",
        "<|im_start|>",
        "<|eot_id|>",
        "<|end|>",
        "<|endoftext|>",
        "<|start_header_id|>",
        "<end_of_turn>",
        "<start_of_turn>",
        "<turn|>",
        "<|turn>",
    ],
)
def test_a_control_token_is_removed(token: str) -> None:
    assert run(f"All done.{token}") == "All done."
    assert run(f"{token}All done.") == "All done."


def test_a_token_split_across_deltas_is_still_removed() -> None:
    assert run("Done.<cha", "nnel", "|>") == "Done."
    assert run("Done.<", "|", "im_end|", ">") == "Done."
    assert run("Done.<end_of", "_turn>") == "Done."


def test_text_held_back_comes_out_once_it_is_clearly_not_a_token() -> None:
    f = ControlTokenFilter()
    assert f.feed("if a <") == "if a "
    assert f.feed(" b then") == "< b then"
    assert f.flush() == ""


@pytest.mark.parametrize(
    "text",
    [
        "Use <div> and </div> here.",
        "a < b and c > d",
        "List<String> and Map<K, V>",
        "x <|> y is Haskell's choice operator",
        "x |> f pipes in Elixir",
        "<b>bold</b> and <end> and <turn>",
        "a <<= 2 and b << c",
    ],
)
def test_ordinary_angle_brackets_are_kept(text: str) -> None:
    assert run(text) == text
    assert run(*text) == text  # one character at a time


def test_an_unfinished_bracket_at_the_end_is_kept() -> None:
    assert run("compare a <") == "compare a <"
    assert run("then <|im") == "then <|im"
