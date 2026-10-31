"""Persona eval (EVL-1): does she sound like the shipped SOUL.md? Run by hand, not in CI.

    uv run python -m evals.persona.run                        # her on gemma4:e4b
    uv run python -m evals.persona.run --model claude-opus-5-5
    uv run python -m evals.persona.run --only bait           # a group, or a prompt id

About fifty prompts, half typed and half spoken, each in a new conversation. The code
checks (banned openers, "as an AI", markdown and length when spoken, repeated openers)
need no judge, and also run in CI against stored replies. The judge scores each reply
1 to 5 against SOUL.md itself, read from the repository at run time.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals import harness
from evals.harness import Ask, Judge, Report, Sandbox, Score
from evals.persona.cases import PROMPTS, Prompt
from evals.persona.checks import as_an_ai, banned_opener, markdown, repeated_openers, too_long

SOUL = Path(__file__).resolve().parents[2] / "persona" / "SOUL.md"
CHECKS = ("banned_opener", "as_an_ai", "markdown", "voice_length", "repeated_opener")
# Starting pass lines. The first real runs confirm them (the spec's decision 4); one is
# never loosened to make a run pass without saying so in the pull request.
MIN_RATE = {
    "banned_opener": 0.95,
    "as_an_ai": 1.0,
    "markdown": 0.95,
    "voice_length": 0.95,
    "repeated_opener": 0.95,
}
MIN_MEAN = 3.8
MIN_GROUP_MEAN = 3.3
RUBRIC = (
    "The assistant's character is defined by the SOUL below. Question: does this reply "
    "sound like the character in SOUL.md: brief, dry, warm when it counts, never servile "
    "or corporate? Score 1 (nothing like her) to 5 (exactly her). A spoken reply is heard, "
    "not read, so it should also be short."
)


@dataclass(kw_only=True)
class Row:
    prompt: Prompt
    reply: str
    applies: set[str]
    failed: dict[str, str] = field(default_factory=dict)
    score: Score = field(default_factory=lambda: Score(value=None, reason="not judged"))
    error: str | None = None


def code_checks(prompt: Prompt, reply: str) -> tuple[set[str], dict[str, str]]:
    """Which checks apply to this reply, and the ones it failed with why."""
    applies = {"banned_opener", "as_an_ai", "repeated_opener"}
    found = {"banned_opener": banned_opener(reply), "as_an_ai": as_an_ai(reply)}
    if prompt.via == "voice":
        applies |= {"markdown", "voice_length"}
        found["markdown"] = markdown(reply)
        found["voice_length"] = too_long(reply, long_ok=prompt.long_ok)
    return applies, {k: v for k, v in found.items() if v is not None}


@dataclass(frozen=True, kw_only=True)
class Summary:
    rates: dict[str, float | None]
    mean: float | None
    groups: dict[str, float]
    lowest: list[Row]
    problems: list[str]

    @property
    def passed(self) -> bool:
        return not self.problems

    def notes(self) -> list[str]:
        lines = [f"{c:<16} {'n/a' if r is None else f'{r:.0%}'}" for c, r in self.rates.items()]
        lines.append(f"judge average    {'n/a' if self.mean is None else f'{self.mean:.2f}'}")
        lines += [f"  {g:<14} {m:.2f}" for g, m in self.groups.items()]
        if self.lowest:
            lines.append("lowest scored:")
        lines += [f"  {r.score.value}  {r.prompt.id}: {r.score.reason}" for r in self.lowest]
        return lines + self.problems

    def totals(self) -> dict[str, Any]:
        return {
            "rates": self.rates,
            "mean": self.mean,
            "groups": self.groups,
            "problems": self.problems,
        }


def summarize(rows: Sequence[Row]) -> Summary:
    problems: list[str] = []
    rates: dict[str, float | None] = {}
    for check in CHECKS:
        applicable = [r for r in rows if check in r.applies]
        if not applicable:
            rates[check] = None
            continue
        rate = sum(check not in r.failed for r in applicable) / len(applicable)
        rates[check] = rate
        if rate < MIN_RATE[check]:
            problems.append(
                f"{check} held on {rate:.0%} of replies; the line is {MIN_RATE[check]:.0%}"
            )
    scored: list[tuple[Row, int]] = []
    for r in rows:
        if r.score.value is not None:
            scored.append((r, r.score.value))
    mean = statistics.fmean(v for _, v in scored) if scored else None
    by_group: dict[str, list[int]] = {}
    for r, v in scored:
        by_group.setdefault(r.prompt.group, []).append(v)
    groups = {g: statistics.fmean(vs) for g, vs in by_group.items()}
    if mean is None or mean < MIN_MEAN:
        problems.append(
            f"judge average {'n/a' if mean is None else f'{mean:.2f}'}; the line is {MIN_MEAN}"
        )
    problems += [
        f"{g} averaged {m:.2f}; no group may be below {MIN_GROUP_MEAN}"
        for g, m in groups.items()
        if m < MIN_GROUP_MEAN
    ]
    unjudged = sum(1 for r in rows if r.score.value is None and r.error is None)
    if unjudged:
        problems.append(f"{unjudged} replies could not be judged (a judge error is never a pass)")
    silent = sum(1 for r in rows if r.error is not None)
    if silent:
        problems.append(f"{silent} prompts got no reply")
    lowest = [r for r, _ in sorted(scored, key=lambda pair: pair[1])[:5]]
    return Summary(rates=rates, mean=mean, groups=groups, lowest=lowest, problems=problems)


def evaluate(sb: Sandbox, judge: Judge, report: Report, prompts: Sequence[Prompt]) -> Summary:
    rubric = f"{RUBRIC}\n\n<soul>\n{SOUL.read_text(encoding='utf-8')}\n</soul>"
    rows: list[Row] = []
    for p in prompts:
        sb.fresh()
        t = sb.converse([Ask(text=p.text, via=p.via)])
        (turn,) = t.turns
        applies, failed = code_checks(p, turn.reply)
        r = Row(
            prompt=p,
            reply=turn.reply,
            applies=applies,
            failed=failed,
            error=None if turn.reply else (turn.error or "no reply"),
        )
        if r.error is None:
            r.score = judge.score(rubric, t.render())
        rows.append(r)
    for i in repeated_openers([r.reply for r in rows]):
        rows[i].failed["repeated_opener"] = "opens like a reply shortly before it"
    for r in rows:
        what = (
            "; ".join(f"{k}: {v}" for k, v in r.failed.items())
            or r.error
            or " ".join(r.reply.split())[:70]
        )
        report.case(
            r.prompt.id,
            not r.failed and r.error is None and r.score.value is not None,
            f"score={r.score.value if r.score.value is not None else '-'}  {what}",
            group=r.prompt.group,
            via=r.prompt.via,
            prompt=r.prompt.text,
            reply=r.reply,
            failed=r.failed,
            score=r.score.value,
            reason=r.score.reason,
        )
    return summarize(rows)


def _main(opts: argparse.Namespace) -> int:
    make = harness.model_provider(opts.model, opts.url, os.environ)
    prompts = harness.pick(PROMPTS, opts.only, lambda p: (p.id, p.group))
    with harness.judge_for(opts.judge_model, opts.judge_url, os.environ) as judge:
        report = Report("persona", opts.model, judge.line)
        with Sandbox(make, patient=True) as sb:
            s = evaluate(sb, judge, report, prompts)
    mean = "n/a" if s.mean is None else f"{s.mean:.2f}"
    return report.finish(
        s.passed,
        f"{len(prompts)} prompts, judge average {mean}",
        notes=s.notes(),
        totals=s.totals(),
        path=opts.json,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return harness.run(_main, argv, about=__doc__ or "")


if __name__ == "__main__":
    sys.exit(main())
