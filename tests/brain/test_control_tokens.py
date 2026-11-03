"""A local model's chat-template tokens never reach her words."""

from __future__ import annotations

import pytest

from trillion.brain.control_tokens import ControlTokenFilter

BAR = "\uff5c"  # DeepSeek's full-width bar


def run(*parts: str) -> str:
    f = ControlTokenFilter()
    return "".join(f.feed(p) for p in parts) + f.flush()


@pytest.mark.parametrize(
    "token",
    [
        "<channel|>",
        "<|channel|>",
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
        "<eos>",
        "<bos>",
        "</s>",
        "<s>",
        f"<{BAR}end▁of▁sentence{BAR}>",
        f"<{BAR}begin▁of▁sentence{BAR}>",
        f"<{BAR}Assistant{BAR}>",
    ],
)
def test_a_control_token_is_removed(token: str) -> None:
    assert run(f"All done.{token}") == "All done."
    assert run(f"{token}All done.") == "All done."


def test_a_token_split_across_deltas_is_still_removed() -> None:
    assert run("Done.<cha", "nnel", "|>") == "Done."
    assert run("Done.<", "|", "im_end|", ">") == "Done."
    assert run("Done.<end_of", "_turn>") == "Done."


THOUGHT = "<|channel>thought\nI should greet.<channel|>Hello!"


def test_her_thinking_channel_is_dropped_whole() -> None:
    assert run(THOUGHT) == "Hello!"
    assert run(*THOUGHT) == "Hello!"  # one character at a time
    assert run("<|chan", "nel>thought\nI sho", "uld greet.<chan", "nel|>Hello!") == "Hello!"
    assert run("Sure. ", THOUGHT, " Bye.") == "Sure. Hello! Bye."


def test_a_reply_that_ends_inside_her_thinking_drops_the_rest() -> None:
    assert run("Hi.", "<|channel>thought\nstill go", "ing") == "Hi."


def test_a_turn_header_goes_with_its_role_line() -> None:
    for role in ("model", "user", "system"):
        text = f"<start_of_turn>{role}\nHello"
        assert run(text) == "Hello"
        assert run(*text) == "Hello"
    assert run("<start_of_", "turn>mo", "del", "\nHello") == "Hello"
    assert run("<|turn>model\nHello<turn|>") == "Hello"  # gemma 4
    assert run("<start_of_turn>model") == ""
    # Words that only begin like a role are hers.
    assert run("<start_of_turn>modelling is fun") == "modelling is fun"
    assert run("<start_of_turn>Hello") == "Hello"


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
        "<span> and </p> and <sup>2</sup> and <eost>",
        f"a <{BAR} b and c {BAR}> d",
    ],
)
def test_ordinary_angle_brackets_are_kept(text: str) -> None:
    assert run(text) == text
    assert run(*text) == text  # one character at a time


def test_an_unfinished_bracket_at_the_end_is_kept() -> None:
    assert run("compare a <") == "compare a <"
    assert run("then <|im") == "then <|im"
