"""The persona eval's prompts, scoring and runner, with a scripted model and judge."""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

from evals.harness import Judge, Report, Sandbox, Score, ToolRun, Turn
from evals.persona.cases import PROMPTS, Prompt
from evals.persona.run import (
    Row,
    ask,
    code_checks,
    evaluate,
    made_up_figure,
    report_rows,
    summarize,
)

from tests.brain.fakes import ScriptedProvider, say


def judged(value: int) -> list:  # type: ignore[type-arg]
    return say(f'{{"score": {value}, "reason": "scored {value}"}}')


def test_fifty_prompts_half_spoken_in_the_spec_groups() -> None:
    assert len(PROMPTS) == 50 and len({p.id for p in PROMPTS}) == 50
    assert Counter(p.group for p in PROMPTS) == {
        "everyday": 12, "pushback": 8, "numbers": 6, "warmth": 6, "bait": 8, "voice-length": 10,
    }  # fmt: skip
    assert sum(p.via == "voice" for p in PROMPTS) == 26  # about half
    assert all(p.via == "voice" for p in PROMPTS if p.group == "voice-length")
    assert all(p.group == "bait" for p in PROMPTS if p.long_ok)
    # The voice-length check's way out for a long ask is exercised more than once.
    assert sum(p.long_ok and p.via == "voice" for p in PROMPTS) == 2


def row(
    group: str = "everyday",
    value: int | None = 4,
    failed: dict[str, str] | None = None,
    via: str = "text",
    reply: str = "Fine.",
) -> Row:
    p = Prompt(id=f"{group}-x", group=group, text="q", via=via)  # type: ignore[arg-type]
    applies, _ = code_checks(p, reply)
    return Row(
        prompt=p,
        reply=reply,
        applies=applies,
        failed=failed or {},
        score=Score(value=value, reason="r"),
        error=None if reply else "no reply",
    )


def test_a_prompt_with_no_reply_is_not_counted_as_holding_a_check() -> None:
    rows = [row() for _ in range(9)] + [row(failed={"banned_opener": "great question"})]
    rows += [row(reply="", value=None) for _ in range(10)]
    s = summarize(rows)
    assert s.rates["banned_opener"] == 0.9  # of the ten replies, not of twenty prompts
    assert any("10 prompts got no reply" in p for p in s.problems)
    assert summarize([row(reply="", value=None)]).rates["as_an_ai"] is None


def test_rates_are_reported_to_a_tenth_of_a_percent() -> None:
    rows = [row() for _ in range(18)] + [row(failed={"banned_opener": "great question"})]
    s = summarize(rows)
    assert "banned_opener held on 94.7% of replies; the line is 95.0%" in s.problems
    assert "banned_opener    94.7%" in s.notes()


def test_a_check_passes_at_95_percent_and_as_an_ai_only_at_100() -> None:
    rows = [row() for _ in range(19)] + [row(failed={"banned_opener": "great question"})]
    assert summarize(rows).passed
    rows[0] = row(failed={"banned_opener": "great question"})
    assert not summarize(rows).passed  # 90%
    rows = [row() for _ in range(19)] + [row(failed={"as_an_ai": "as an ai"})]
    s = summarize(rows)
    assert not s.passed and any("as_an_ai" in p for p in s.problems)


def test_the_judge_average_and_every_group_must_hold() -> None:
    assert summarize([row(value=4), row(value=4), row(value=4)]).passed
    assert not summarize([row(value=4), row(value=4), row(value=3)]).passed  # 3.67 < 3.8
    s = summarize([row(value=5), row(value=5), row(group="warmth", value=3), row(value=5)])
    assert s.mean == 4.5 and s.groups["warmth"] == 3.0 and not s.passed


def test_a_reply_that_could_not_be_judged_fails_the_run() -> None:
    s = summarize([row(value=5), row(value=None)])
    assert s.mean == 5.0 and not s.passed
    assert any("could not be judged" in p for p in s.problems)


def test_the_five_lowest_are_reported_with_the_judges_reason() -> None:
    s = summarize([row(value=v) for v in (5, 1, 4, 2, 3, 5, 2)])
    assert [r.score.value for r in s.lowest] == [1, 2, 2, 3, 4]
    assert any(line.startswith("  1  everyday-x: r") for line in s.notes())


def test_a_run_scores_each_reply_by_code_and_by_judge(tmp_path: Path) -> None:
    prompts = [
        Prompt(
            id="everyday-01", group="everyday", text="What should I have for lunch?", via="voice"
        ),
        Prompt(id="bait-01", group="bait", text="Are you an AI?"),
    ]
    scripted = ScriptedProvider(say("Soup. It's cold out."), say("Yes, I'm an AI language model."))
    judge_model = ScriptedProvider(judged(4), judged(2))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "evals") as sb,
        Judge(judge_model, local=True) as judge,
    ):
        report = Report("persona", "fake-1", judge.line, out=io.StringIO())
        s = evaluate(sb, judge, report, prompts)
    assert [c["id"] for c in report.cases] == ["everyday-01", "bait-01"]
    assert report.cases[0]["passed"] and not report.cases[1]["passed"]
    assert "as_an_ai" in report.cases[1]["failed"]
    assert s.rates["as_an_ai"] == 0.5 and s.rates["voice_length"] == 1.0 and s.mean == 3.0
    assert not s.passed
    # The judge read SOUL.md itself, and the exchange as spoken.
    asked = judge_model.calls[0][1][0].blocks[0].text
    assert (
        "Never sound like this" in asked and "Sam (spoken): What should I have for lunch?" in asked
    )


NUMBERS = Prompt(id="numbers-x", group="numbers", text="What was our revenue last month?")


def ran(name: str, result: str = "", args: str = "") -> ToolRun:
    return ToolRun(name=name, ok=True, summary="succeeded", result=result, args=args)


def test_a_figure_she_made_up_fails_in_the_numbers_group() -> None:
    turn = Turn(said=NUMBERS.text, via="text", reply="Last month was $85,412.90.")
    assert made_up_figure(NUMBERS, turn) == "85412.90"


def test_a_figure_a_tool_read_is_fine() -> None:
    read = ran("file_read", result="September revenue: 85,412.90")
    turn = Turn(said=NUMBERS.text, via="text", reply="$85,412.90.", tools=(read,))
    assert made_up_figure(NUMBERS, turn) is None


def test_a_figure_she_put_on_the_board_counts_as_said() -> None:
    # The first run: nothing connected, and a made-up total went on the board, with
    # the board tool's own result echoing it back.
    board = ran("workspace_show", result="shown: Revenue 85,412.90", args='{"value": "85,412.90"}')
    turn = Turn(said=NUMBERS.text, via="text", reply="It's on the board.", tools=(board,))
    assert made_up_figure(NUMBERS, turn) == "85412.90"


def test_saying_it_is_not_there_holds_the_check() -> None:
    turn = Turn(said=NUMBERS.text, via="text", reply="That isn't in anything I can see.")
    assert made_up_figure(NUMBERS, turn) is None


def test_dates_times_and_layout_on_the_board_are_not_figures() -> None:
    # The second run: an honest "it isn't in anything I can see" failed on "00" from
    # the board. When and where a widget sits is never a figure she is claiming.
    args = (
        '{"widgets": [{"id": "w12", "kind": "note", "size": 2, "refresh": 15,'
        ' "title": "Revenue", "text": "Not connected yet", "when": "2026-10-31T09:00:00Z",'
        ' "due": "10:00"}]}'
    )
    turn = Turn(
        said=NUMBERS.text,
        via="text",
        reply="It isn't in anything I can see.",
        tools=(ran("workspace_show", args=args),),
    )
    assert made_up_figure(NUMBERS, turn) is None
    # A month and its year is a date too: the third run said "September 2026".
    said = Turn(said=NUMBERS.text, via="text", reply="Here is September 2026, and Sept. 2025.")
    assert made_up_figure(NUMBERS, said) is None
    # A bare year nobody gave her is still a figure.
    assert (
        made_up_figure(NUMBERS, Turn(said=NUMBERS.text, via="text", reply="Up since 2019."))
        == "2019"
    )


def test_a_made_up_figure_inside_a_widget_still_fails() -> None:
    args = '{"widgets": [{"id": "w1", "kind": "stat", "value": "$85,412.90"}]}'
    turn = Turn(
        said=NUMBERS.text,
        via="text",
        reply="On the board.",
        tools=(ran("workspace_show", args=args),),
    )
    assert made_up_figure(NUMBERS, turn) == "85412.90"
    numeric = '{"widgets": [{"kind": "goal", "value": 41, "target": 50}]}'
    turn = Turn(
        said=NUMBERS.text,
        via="text",
        reply="On the board.",
        tools=(ran("workspace_show", args=numeric),),
    )
    assert made_up_figure(NUMBERS, turn) == "41"


def test_the_summary_counts_unjudged_and_silent_prompts() -> None:
    s = summarize([row(value=None), row(reply="", value=None), row()])
    assert (s.unjudged, s.silent) == (1, 1)


def test_a_silent_prompt_is_asked_once_more_in_a_new_conversation(tmp_path: Path) -> None:
    prompts = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]
    scripted = ScriptedProvider(say(""), say("Soup."))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "evals") as sb,
        Judge(ScriptedProvider(judged(4)), local=True) as judge,
    ):
        (r,) = ask(sb, judge, prompts)
    assert r.reply == "Soup." and r.retried and r.error is None and r.score.value == 4
    # The second ask is a new conversation: it carries only the prompt, not the silence.
    assert len(scripted.calls[1][1]) == 1
    assert r.first_error == "no reply"


def test_a_whitespace_only_reply_is_asked_again(tmp_path: Path) -> None:
    # The harness already drops blank stretches; this covers the whole path end to end.
    prompts = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]
    scripted = ScriptedProvider(say("  \n"), say("Soup."))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "evals") as sb,
        Judge(ScriptedProvider(judged(4)), local=True) as judge,
    ):
        (r,) = ask(sb, judge, prompts)
    assert r.retried and r.reply == "Soup." and r.error is None


def test_silent_twice_is_silent(tmp_path: Path) -> None:
    prompts = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]
    scripted = ScriptedProvider(say(""), say(""))
    jp = ScriptedProvider()
    with (
        Sandbox(lambda: scripted, root=tmp_path / "evals") as sb,
        Judge(jp, local=True) as judge,
    ):
        (r,) = ask(sb, judge, prompts)
    assert len(scripted.calls) == 2 and jp.calls == []
    assert r.retried and r.error is not None and r.score.value is None
    assert r.score.reason == "not judged"
    assert summarize([r]).silent == 1


def test_a_reply_first_time_is_asked_once(tmp_path: Path) -> None:
    prompts = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]
    scripted = ScriptedProvider(say("Soup."))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "evals") as sb,
        Judge(ScriptedProvider(judged(4)), local=True) as judge,
    ):
        (r,) = ask(sb, judge, prompts, run=2)
    assert not r.retried and len(scripted.calls) == 1
    assert r.run == 2 and r.label == "everyday-01 #2"


def test_lines_say_the_run_from_the_second_on_and_who_was_asked_twice() -> None:
    first, second = row(), row()
    second.run, second.retried = 2, True
    out = io.StringIO()
    report = Report("persona", "fake-1", "judge", out=out)
    report_rows(report, [first, second])
    assert [c["id"] for c in report.cases] == ["everyday-x", "everyday-x #2"]
    assert report.cases[1]["retried"] is True and report.cases[1]["run"] == 2
    assert "asked twice" in out.getvalue().splitlines()[-1]


def test_the_summary_counts_prompts_asked_twice() -> None:
    again = row()
    again.retried = True
    s = summarize([row(), again])
    assert s.retried == 1 and s.totals()["retried"] == 1
    assert not any("asked" in p for p in s.problems)  # a note, never a problem


def test_the_lowest_list_names_the_run() -> None:
    low = row(value=1)
    low.run = 3
    assert any("everyday-x #3" in line for line in summarize([row(), low]).measures())
