"""Drift eval (EVL-5): does she still sound like herself thirty turns in? Run by hand.

    uv run python -m evals.drift.run                  # her on gemma4:e4b
    uv run python -m evals.drift.run --model claude-opus-5-5
    uv run python -m evals.drift.run --only on        # just the run with the checkpoint

One scripted conversation of thirty turns in one session, run twice: with the tonal
checkpoint (PER-4) on, as shipped, and with it turned off inside this process. Seven
probe replies are judged against SOUL.md; the persona code checks run on every reply.
With the checkpoint on, it fails when the last three probes average more than 0.5
below the first three, any probe after the checkpoint starts scores below 3, or a code
check fails on more than 5% of replies. The gap between the two runs says whether
the checkpoint is earning its place; it is reported, not a pass line. The run without
the checkpoint alone (--only off) is reported without a verdict.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any

from evals import harness
from evals.drift.script import PROBES, STEPS, Step
from evals.harness import Ask, CannotRun, Judge, Report, Sandbox, Score, Transcript
from evals.persona.cases import Prompt
from evals.persona.checks import repeated_openers
from evals.persona.run import RUBRIC, SOUL, code_checks
from trillion.persona import prompt

SIDES = ("on", "off")
MAX_SLIDE = 0.5
LATE_FLOOR = 3
MAX_FAIL_RATE = 0.05
CHECKS = ("banned_opener", "as_an_ai", "markdown", "voice_length", "repeated_opener")


@dataclass(frozen=True, kw_only=True)
class Leg:
    """One run of the conversation: every reply, the probes' scores, and which code
    checks applied to each reply and which it failed."""

    checkpoint: bool
    replies: dict[int, str]
    scores: dict[int, Score]
    applies: dict[int, set[str]]
    failed: dict[int, dict[str, str]]


@contextmanager
def checkpoint_off() -> Iterator[None]:
    """The tonal checkpoint, turned off in this process only: the product has no switch."""
    saved = prompt.CHECKPOINT_FROM
    prompt.CHECKPOINT_FROM = sys.maxsize
    try:
        yield
    finally:
        prompt.CHECKPOINT_FROM = saved


def converse(
    sb: Sandbox, judge: Judge, report: Report, steps: Sequence[Step], *, checkpoint: bool
) -> Leg:
    """The whole conversation in one session, with the checkpoint on or off."""
    rubric = f"{RUBRIC}\n\n<soul>\n{SOUL.read_text(encoding='utf-8')}\n</soul>"
    replies: dict[int, str] = {}
    scores: dict[int, Score] = {}
    applies: dict[int, set[str]] = {}
    failed: dict[int, dict[str, str]] = {}
    switch: AbstractContextManager[None] = nullcontext() if checkpoint else checkpoint_off()
    with switch:
        # One fresh app per run, then every step in it: drift needs the long session.
        sb.fresh()
        for s in steps:
            (turn,) = sb.converse([Ask(text=s.text, via=s.via)]).turns
            replies[s.n] = turn.reply
            p = Prompt(id=f"drift-{s.n:02d}", group="drift", text=s.text, via=s.via)
            applies[s.n], failed[s.n] = code_checks(p, turn.reply)
            if s.probe:
                # Judged one exchange at a time, as the persona eval does: the judge
                # scores this reply's voice, not the conversation around it.
                scores[s.n] = (
                    judge.score(rubric, Transcript(turns=(turn,)).render())
                    if turn.reply
                    else Score(value=None, reason=turn.error or "no reply")
                )
    order = [s.n for s in steps]
    for i in repeated_openers([replies[n] for n in order]):
        failed[order[i]]["repeated_opener"] = "opens like a reply shortly before it"
    side = "on" if checkpoint else "off"
    for s in steps:
        if not s.probe:
            continue
        sc = scores[s.n]
        what = "; ".join(f"{k}: {v}" for k, v in failed[s.n].items()) or (
            " ".join(replies[s.n].split())[:70] or sc.reason
        )
        report.case(
            f"{side}-{s.n:02d}",
            sc.value is not None and not failed[s.n],
            f"score={sc.value if sc.value is not None else '-'}  {what}",
            checkpoint=checkpoint,
            turn=s.n,
            via=s.via,
            prompt=s.text,
            reply=replies[s.n],
            score=sc.value,
            reason=sc.reason,
            failed=failed[s.n],
        )
    return Leg(
        checkpoint=checkpoint, replies=replies, scores=scores, applies=applies, failed=failed
    )


def rates(leg: Leg) -> dict[str, float | None]:
    """Each code check's pass rate over the replies it applies to. A turn with no reply
    holds no check: it is counted on its own in `verdict`."""
    out: dict[str, float | None] = {}
    for check in CHECKS:
        held = [n for n, a in leg.applies.items() if check in a and leg.replies.get(n, "").strip()]
        out[check] = (
            sum(check not in leg.failed.get(n, {}) for n in held) / len(held) if held else None
        )
    return out


def verdict(leg: Leg, probes: Sequence[int]) -> list[str]:
    """What fails the run with the checkpoint on; nothing when it passes."""
    problems: list[str] = []
    silent = sum(1 for r in leg.replies.values() if not r.strip())
    if silent:
        problems.append(f"{silent} turns got no reply")
    unjudged = [n for n in probes if leg.scores[n].value is None]
    if unjudged:
        problems.append(
            f"{len(unjudged)} probes could not be judged or got no reply"
            " (a judge error is never a pass)"
        )
    value = {n: v for n in probes if (v := leg.scores[n].value) is not None}
    first = [value[n] for n in probes[:3] if n in value]
    last = [value[n] for n in probes[-3:] if n in value]
    if first and last:
        a, b = statistics.fmean(first), statistics.fmean(last)
        # Rounded, so a slide of exactly the limit isn't failed by float noise.
        if round(a - b, 9) > MAX_SLIDE:
            problems.append(
                f"the last three probes averaged {b:.2f}, down from {a:.2f} in the first"
                f" three; it may slide {MAX_SLIDE} at most"
            )
    start = prompt.CHECKPOINT_FROM
    problems += [
        f"turn {n} scored {v}; no probe after turn {start} may be below {LATE_FLOOR}"
        for n, v in value.items()
        if n > start and v < LATE_FLOOR
    ]
    for check, rate in rates(leg).items():
        if rate is not None and round(1 - rate, 9) > MAX_FAIL_RATE:
            problems.append(
                f"{check} held on {rate:.1%} of replies; it may fail on {MAX_FAIL_RATE:.0%} at most"
            )
    return problems


def mean(leg: Leg) -> float | None:
    """The average of the probes the judge scored, or None if it scored none."""
    vs = [s.value for s in leg.scores.values() if s.value is not None]
    return statistics.fmean(vs) if vs else None


def gap(on: Leg, off: Leg) -> float | None:
    """How much higher the probes scored with the checkpoint on than off."""
    a, b = mean(on), mean(off)
    return None if a is None or b is None else a - b


def _main(opts: argparse.Namespace) -> int:
    # Checked before any model is set up or asked.
    unknown = sorted(set(opts.only) - set(SIDES))
    if unknown:
        raise CannotRun(f"--only takes on or off, not {', '.join(unknown)}")
    sides = [side for side in SIDES if side in opts.only] or list(SIDES)
    make = harness.model_provider(opts.model, opts.url, os.environ)
    with harness.judge_for(opts.judge_model, opts.judge_url, os.environ) as judge:
        report = Report("drift", opts.model, judge.line)
        with Sandbox(make, patient=True) as sb:
            legs = {
                side: converse(sb, judge, report, STEPS, checkpoint=side == "on") for side in sides
            }
    # The lines hold her to the shipped product, the checkpoint on; the run without it
    # is there to compare with, so alone it is reported without a verdict.
    judged = "on" in legs
    problems = verdict(legs["on"], PROBES) if judged else []
    notes: list[str] = []
    for n in PROBES:
        cells = []
        for side, leg in legs.items():
            v = leg.scores[n].value
            cells.append(f"{side} {'-' if v is None else v}")
        notes.append(f"turn {n:>2}: " + "  ".join(cells))
    g = gap(legs["on"], legs["off"]) if len(legs) == 2 else None
    if g is not None:
        notes.append(f"the checkpoint is worth {g:+.2f} on average")
    if not judged:
        notes.append("only the run without the checkpoint: reported, not judged against the lines")
    totals: dict[str, Any] = {
        side: {
            "mean": mean(leg),
            "scores": {str(n): leg.scores[n].value for n in PROBES},
            "rates": rates(leg),
        }
        for side, leg in legs.items()
    }
    totals |= {"gap": g, "judged": judged, "problems": problems}
    return report.finish(
        not problems,
        f"{len(STEPS)} turns, {len(PROBES)} probes, checkpoint {' and '.join(legs)}",
        notes=[*notes, *problems],
        totals=totals,
        path=opts.json,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return harness.run(_main, argv, about=__doc__ or "")


if __name__ == "__main__":
    sys.exit(main())
