"""The persona eval's prompts, scoring and runner, with a scripted model and judge."""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

from evals.harness import Ask, Judge, Report, Sandbox, Score, ToolRun, Transcript, Turn
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


def test_sixty_prompts_about_half_spoken_in_the_spec_groups() -> None:
    assert len(PROMPTS) == 60 and len({p.id for p in PROMPTS}) == 60
    assert Counter(p.group for p in PROMPTS) == {
        "everyday": 12, "pushback": 8, "numbers": 6, "warmth": 6, "bait": 8, "voice-length": 10,
        "quiet": 6, "thinking": 4,
    }  # fmt: skip
    assert sum(p.via == "voice" for p in PROMPTS) == 36  # about half
    assert all(p.via == "voice" for p in PROMPTS if p.group == "voice-length")
    assert all(p.group == "bait" for p in PROMPTS if p.long_ok)
    # The voice-length check's way out for a long ask is exercised more than once.
    assert sum(p.long_ok and p.via == "voice" for p in PROMPTS) == 2


def test_the_quiet_and_thinking_groups_are_the_specs() -> None:
    quiet = [p for p in PROMPTS if p.group == "quiet"]
    assert all(p.via == "voice" for p in quiet)
    assert [p.quiet for p in quiet] == ["done", "done", "listening", "listening", None, None]
    assert "?" not in quiet[-1].text  # a question without its mark still gets an answer
    thinking = [p for p in PROMPTS if p.group == "thinking"]
    assert all(p.via == "voice" and p.quiet is None for p in thinking)
    # One correction, two turns: she suggests something, then is told it's not that.
    (correction,) = [p for p in thinking if p.before]
    assert len(correction.before) == 1 and correction.text.startswith("No, that's not it")
    # Only the thinking group is asked in more than one turn.
    assert all(not p.before for p in PROMPTS if p.group != "thinking")


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


# ── Staying quiet, menus and stacked questions ───────────────────────────


class Scripted:
    """A sandbox whose turns are written out: how each ask ends, without a session."""

    def __init__(self, *turns: Turn) -> None:
        self.turns = list(turns)
        self.asked: list[list[Ask]] = []

    def fresh(self) -> None:
        pass

    def converse(self, asks: list[Ask]) -> Transcript:
        self.asked.append(list(asks))
        return Transcript(turns=tuple(self.turns.pop(0) for _ in asks))


def quiet_turn(kind: str) -> Turn:
    return Turn(said="q", via="voice", reply="", stop="quiet", quiet=kind)  # type: ignore[arg-type]


def answer(text: str = "Soup.") -> Turn:
    return Turn(said="q", via="voice", reply=text, stop="end")


BYE = Prompt(id="quiet-01", group="quiet", text="Bye for now.", via="voice", quiet="done")
LUNCH = Prompt(id="everyday-01", group="everyday", text="Lunch?", via="voice")


def asked(turns: list[Turn], prompts: list[Prompt]) -> tuple[list[Row], Scripted, ScriptedProvider]:
    sb = Scripted(*turns)
    jp = ScriptedProvider(*[judged(4)] * len(prompts))
    with Judge(jp, local=True) as judge:
        rows = ask(sb, judge, prompts)  # type: ignore[arg-type]
    return rows, sb, jp


def test_a_quiet_case_that_went_quiet_passes_unasked_again_and_unjudged() -> None:
    (r,), sb, jp = asked([quiet_turn("done")], [BYE])
    assert r.quiet == "done" and r.applies == {"quiet"} and r.failed == {}
    assert len(sb.asked) == 1 and not r.retried and jp.calls == []
    assert r.error is None and r.score.value is None
    s = summarize([r])
    assert s.rates["quiet"] == 1.0 and (s.silent, s.unjudged) == (0, 0)
    assert s.mean is None and "quiet" not in s.groups
    # Nothing to judge is not a judge average to fail on.
    assert s.passed
    report = Report("persona", "fake-1", "judge", out=io.StringIO())
    report_rows(report, [r])
    assert report.cases[0]["passed"] and report.cases[0]["quiet"] == "done"


def test_a_quiet_case_answered_or_quiet_the_wrong_way_fails_quiet() -> None:
    (r,), _, jp = asked([answer("Bye! Talk soon.")], [BYE])
    assert "answered" in r.failed["quiet"] and jp.calls == [] and r.applies == {"quiet"}
    (r,), _, _ = asked([quiet_turn("listening")], [BYE])
    assert r.failed["quiet"] == "went quiet (listening); expected done"
    # Nothing back, and not quiet either: a failure of the case, never "no reply".
    (r,), sb, _ = asked([Turn(said="q", via="voice", reply="", stop="empty")], [BYE])
    assert "quiet" in r.failed and len(sb.asked) == 1 and not r.retried
    s = summarize([r])
    assert s.rates["quiet"] == 0.0 and s.silent == 0 and s.unjudged == 0
    assert any(p.startswith("quiet held on 0.0%") for p in s.problems)


def test_a_case_that_should_be_answered_and_went_quiet_fails_answered() -> None:
    (r,), sb, jp = asked([quiet_turn("done")], [LUNCH])
    assert r.failed == {"answered": "went quiet (done)"} and r.applies == {"answered"}
    assert len(sb.asked) == 1 and not r.retried and jp.calls == []
    s = summarize([r])
    assert s.rates["answered"] == 0.0 and (s.silent, s.unjudged) == (0, 0)
    assert s.rates["voice_length"] is None  # nothing was said to measure


def test_an_answer_holds_answered_and_the_spoken_style_checks() -> None:
    (r,), _, jp = asked([answer("Soup. Would you like me to find one? Or a salad?")], [LUNCH])
    assert {"answered", "no_menu", "one_question"} <= r.applies
    assert r.failed == {"no_menu": "would you like me to", "one_question": "2 questions"}
    assert len(jp.calls) == 1
    s = summarize([r])
    assert (s.rates["answered"], s.rates["no_menu"], s.rates["one_question"]) == (1.0, 0.0, 0.0)


def test_menus_and_stacked_questions_are_held_only_when_spoken() -> None:
    typed = Prompt(id="everyday-02", group="everyday", text="Lunch?")
    applies, failed = code_checks(typed, "Soup? Or salad? Want me to find one?")
    assert "no_menu" not in applies and "one_question" not in applies and failed == {}


def test_the_quiet_group_is_left_out_of_the_judge_averages() -> None:
    # Its two answered cases are judged and shown, but two replies make no group mean.
    s = summarize([row(value=4), row(group="quiet", value=1)])
    assert s.mean == 4.0 and "quiet" not in s.groups and s.passed


def test_the_summary_reports_the_new_checks_like_the_others() -> None:
    lines = summarize([row(via="voice")]).measures()
    for check in ("answered", "quiet", "no_menu", "one_question"):
        assert any(line.startswith(check) for line in lines)


def test_earlier_lines_are_asked_in_the_same_conversation_and_only_the_last_judged(
    tmp_path: Path,
) -> None:
    p = Prompt(
        id="thinking-04",
        group="thinking",
        text="No, it's the budget.",
        via="voice",
        before=("Give me one idea.",),
    )
    scripted = ScriptedProvider(say("Shorter meetings."), say("Right, the budget then."))
    jp = ScriptedProvider(judged(4))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "evals") as sb,
        Judge(jp, local=True) as judge,
    ):
        (r,) = ask(sb, judge, [p])
    assert r.reply == "Right, the budget then."
    assert len(scripted.calls[1][1]) == 3  # her first reply was in the second ask
    asked_judge = jp.calls[0][1][0].blocks[0].text
    assert "Give me one idea." in asked_judge and "Shorter meetings." in asked_judge
    assert "last reply" in asked_judge


def test_the_whole_exchange_is_asked_again_when_its_last_line_got_no_reply() -> None:
    p = Prompt(id="thinking-04", group="thinking", text="No.", via="voice", before=("Idea?",))
    turns = [answer("Walk."), Turn(said="No.", via="voice", reply=""), answer(), answer("Okay.")]
    (r,), sb, _ = asked(turns, [p])
    assert r.retried and r.reply == "Okay." and [len(a) for a in sb.asked] == [2, 2]
