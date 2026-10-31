"""The persona gate (EVL-4): has a change to the soul, the prompts or the model made
her sound less like herself? Run by hand, with any model, a free local one included.

    uv run python -m evals.gate --model gemma4:e4b
    uv run python -m evals.gate --deployed                 # the model this Trillion uses
    uv run python -m evals.gate --model gemma4:e4b --save-baseline
    uv run python -m evals.gate --model gemma4:e4b --save-baseline --accept-drop "why"

It runs the whole persona eval, then compares it with the committed baseline for the
same model and judge (evals/baselines/). It fails when the judge average falls more
than 0.3, a group's more than 0.5, or a code check's pass rate more than 5 points;
and loudly when a model other than the one named answered. --save-baseline keeps a
passing run as the new baseline; a worse one needs --accept-drop with a reason, which
is saved in the file for reviewers to see.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from evals import harness
from evals.baselines import (
    BASELINES,
    Baseline,
    Totals,
    baseline_path,
    compare,
    fingerprint,
    may_save,
    model_problems,
)
from evals.harness import CannotRun, Judge, Report, Sandbox
from evals.persona.cases import PROMPTS, Prompt
from evals.persona.run import evaluate
from trillion import paths
from trillion.api.runtime import MODEL_PREF, OWNER
from trillion.brain import config
from trillion.brain.config import (
    ANTHROPIC_KEY,
    MODEL,
    OPENAI_BASE_URL,
    ConfigError,
    ModelSettings,
)
from trillion.brain.providers.anthropic import DEFAULT_MODEL


def deployed(db: Path, env: Mapping[str, str]) -> tuple[str, str]:
    """The model this machine's Trillion uses, and its address ("" for Claude), chosen
    as the app chooses: the environment first, then the saved setting. The database is
    opened read-only, and the keychain is never read."""
    base_url = env.get(OPENAI_BASE_URL, "").strip()
    if base_url:
        model = env.get(MODEL, "").strip()
        if not model:
            raise CannotRun(f"{OPENAI_BASE_URL} is set; {MODEL} must name the model")
        return model, base_url
    if env.get(ANTHROPIC_KEY, "").strip():
        return env.get(MODEL, "").strip() or DEFAULT_MODEL, ""
    if not db.is_file():
        raise CannotRun(f"no Trillion set up at {db.parent}: nothing is deployed")
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT value_json FROM preferences WHERE owner_id = ? AND key = ?",
            (OWNER, MODEL_PREF),
        ).fetchone()
    settings = ModelSettings.from_json(json.loads(row[0])) if row else None
    if settings is None:
        raise CannotRun("this Trillion has no model chosen")
    if settings.kind == "anthropic":
        if not env.get(ANTHROPIC_KEY, "").strip():
            raise CannotRun(f"{settings.model} needs {ANTHROPIC_KEY} set in the environment")
        return settings.model, ""
    if settings.kind == "openai":
        raise CannotRun(
            "grading an OpenAI model is not supported yet: the evals send it no key. "
            f"Set {OPENAI_BASE_URL} and {MODEL} for an OpenAI-compatible address instead"
        )
    try:
        url = config.endpoint(settings.kind, settings.base_url)
    except ConfigError as e:
        raise CannotRun(f"the saved model's address: {e.code}") from e
    return settings.model, url or ""


def check(
    sb: Sandbox,
    judge: Judge,
    report: Report,
    prompts: Sequence[Prompt],
    *,
    model: str,
    folder: Path = BASELINES,
    save: bool = False,
    accept_drop: str | None = None,
    json_path: Path | None = None,
) -> int:
    s = evaluate(sb, judge, report, prompts)
    # The app's event loop adds to sb.served; everything below reads this one copy.
    served = set(sb.served)
    totals = Totals(mean=s.mean, groups=s.groups, rates=s.rates)
    hard, notes = model_problems(model, served, sb.unreported)
    if s.unjudged:
        hard.append(f"{s.unjudged} replies could not be judged")
    if s.silent:
        hard.append(f"{s.silent} prompts got no reply")
    judge_name = judge.provider.model
    path = folder / baseline_path(model, judge_name).name
    base = Baseline.load(path) if path.is_file() else None
    drops = compare(totals, base) if base is not None else []
    # The persona eval's own lines (s.problems) are printed through s.notes(), but they
    # don't fail the gate: it fails on change against the baseline, and on hard problems.
    problems = hard + drops
    if base is None:
        notes.append(f"no baseline for {model} judged by {judge_name}: nothing to compare with")
        if not save:
            problems.append("no baseline yet; run again with --save-baseline to make one")
    if save:
        refused = may_save(hard=hard, drops=drops, accept_drop=accept_drop)
        if refused is None:
            Baseline(
                model=model,
                judge=judge_name,
                answered_by=tuple(sorted(served)),
                date=datetime.now(UTC).isoformat(timespec="seconds"),
                fingerprint=fingerprint(),
                totals=totals,
                accepted_drop=accept_drop,
            ).save(path)
            notes.append(f"saved baseline: {path}")
            problems = []
        else:
            problems.append(refused)
    mean = "n/a" if s.mean is None else f"{s.mean:.2f}"
    return report.finish(
        not problems,
        f"{len(prompts)} prompts, judge average {mean}",
        notes=[*s.notes(), *notes, *problems],
        totals={**s.totals(), "baseline": None if base is None else base.to_json()},
        path=json_path,
    )


def _extra(p: argparse.ArgumentParser) -> None:
    p.add_argument("--deployed", action="store_true", help="grade this Trillion's model")
    p.add_argument("--save-baseline", action="store_true", help="keep this run as the baseline")
    p.add_argument(
        "--accept-drop", default=None, metavar="WHY", help="keep a worse run, saying why"
    )


def _main(opts: argparse.Namespace) -> int:
    if opts.only and opts.save_baseline:
        raise CannotRun("a baseline is the whole eval: --only and --save-baseline don't mix")
    prompts = harness.pick(PROMPTS, opts.only, lambda p: (p.id, p.group))
    model, url = deployed(paths.db_path(), os.environ) if opts.deployed else (opts.model, opts.url)
    make = harness.model_provider(model, url, os.environ)
    with harness.judge_for(opts.judge_model, opts.judge_url, os.environ) as judge:
        report = Report("gate", model, judge.line)
        with Sandbox(make, patient=True) as sb:
            return check(
                sb,
                judge,
                report,
                prompts,
                model=model,
                save=opts.save_baseline,
                accept_drop=opts.accept_drop,
                json_path=opts.json,
            )


def main(argv: Sequence[str] | None = None) -> int:
    return harness.run(_main, argv, about=__doc__ or "", extra=_extra)


if __name__ == "__main__":
    sys.exit(main())
