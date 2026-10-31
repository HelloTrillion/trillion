"""The persona eval's prompts, scoring and runner, with a scripted model and judge."""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

from evals.harness import Judge, Report, Sandbox, Score
from evals.persona.cases import PROMPTS, Prompt
from evals.persona.run import Row, code_checks, evaluate, summarize

from tests.brain.fakes import ScriptedProvider, say


def judged(value: int) -> list:  # type: ignore[type-arg]
    return say(f'{{"score": {value}, "reason": "scored {value}"}}')


def test_fifty_prompts_half_spoken_in_the_spec_groups() -> None:
    assert len(PROMPTS) == 50 and len({p.id for p in PROMPTS}) == 50
    assert Counter(p.group for p in PROMPTS) == {
        "everyday": 12, "pushback": 8, "numbers": 6, "warmth": 6, "bait": 8, "voice-length": 10,
    }  # fmt: skip
    assert sum(p.via == "voice" for p in PROMPTS) == 25
    assert all(p.via == "voice" for p in PROMPTS if p.group == "voice-length")
    assert all(p.group == "bait" for p in PROMPTS if p.long_ok)


def row(
    group: str = "everyday",
    value: int | None = 4,
    failed: dict[str, str] | None = None,
    via: str = "text",
) -> Row:
    p = Prompt(id=f"{group}-x", group=group, text="q", via=via)  # type: ignore[arg-type]
    applies, _ = code_checks(p, "Fine.")
    return Row(
        prompt=p,
        reply="Fine.",
        applies=applies,
        failed=failed or {},
        score=Score(value=value, reason="r"),
    )


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
