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
from typing import NamedTuple

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
from trillion.storage.inspect import _read_only


class Deployed(NamedTuple):
    model: str
    # Its OpenAI-compatible address, or "" for Claude.
    url: str
    # Said when this shell's environment would have the app use another model.
    note: str | None = None


def _from_env(env: Mapping[str, str]) -> tuple[str, str] | None:
    """The model the environment would choose, as the app does, or None."""
    base_url = env.get(OPENAI_BASE_URL, "").strip()
    if base_url:
        model = env.get(MODEL, "").strip()
        if not model:
            raise CannotRun(f"{OPENAI_BASE_URL} is set; {MODEL} must name the model")
        return model, base_url
    if env.get(ANTHROPIC_KEY, "").strip():
        return env.get(MODEL, "").strip() or DEFAULT_MODEL, ""
    return None


def _saved(db: Path) -> ModelSettings | None:
    """The saved model setting, read without changing anything, or None if none is."""
    if not db.is_file():
        return None
    try:
        with closing(_read_only(db)) as conn:
            row = conn.execute(
                "SELECT value_json FROM preferences WHERE owner_id = ? AND key = ?",
                (OWNER, MODEL_PREF),
            ).fetchone()
    except sqlite3.Error as e:
        raise CannotRun(f"the saved model setting can't be read: {e}") from e
    if row is None or row[0] is None:
        return None
    try:
        raw = json.loads(row[0])
    except ValueError as e:
        raise CannotRun("the saved model setting can't be read: it isn't JSON") from e
    if raw is None:
        return None
    settings = ModelSettings.from_json(raw)
    if settings is None:
        raise CannotRun("the saved model setting can't be read: it names no model")
    return settings


def _address(settings: ModelSettings, env: Mapping[str, str]) -> str:
    if settings.kind == "anthropic":
        if not env.get(ANTHROPIC_KEY, "").strip():
            raise CannotRun(f"{settings.model} needs {ANTHROPIC_KEY} set in the environment")
        return ""
    if settings.kind == "openai":
        # The saved setting always wins, so setting the environment can't help here.
        raise CannotRun(
            "grading an OpenAI model is not supported yet: the evals send it no key. "
            "Grade it by hand without --deployed: run the gate with"
            f" --model {settings.model} --url <an OpenAI-compatible address> instead"
        )
    try:
        url = config.endpoint(settings.kind, settings.base_url)
    except ConfigError as e:
        raise CannotRun(f"the saved model's address: {e.code}") from e
    return url or ""


def deployed(db: Path, env: Mapping[str, str]) -> Deployed:
    """The model this machine's Trillion is set to use, and its address. The saved
    setting wins: this shell's environment isn't the app's, and a key set here may be
    only for a Claude judge, so the key is just a credential. With nothing saved, the
    environment decides as the app would. The database is opened read-only, and the
    keychain is never read."""
    settings = _saved(db)
    if settings is None:
        found = _from_env(env)
        if found is not None:
            return Deployed(*found)
        if not db.is_file():
            raise CannotRun(f"no Trillion set up at {db.parent}: nothing is deployed")
        raise CannotRun("this Trillion has no model chosen")
    url = _address(settings, env)
    try:
        would = _from_env(env)
    except CannotRun:
        would = None
    note = None
    if would is not None and would[0] != settings.model:
        note = (
            f"the app would use {would[0]} if started with this environment;"
            f" grading the saved {settings.model}"
        )
    return Deployed(settings.model, url, note)


def _baseline(path: Path) -> Baseline | None:
    if not path.is_file():
        return None
    try:
        return Baseline.load(path)
    except (OSError, ValueError, TypeError, AttributeError) as e:
        raise CannotRun(f"the baseline {path} can't be read: {e}") from e


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
    deployed_note: str | None = None,
) -> int:
    judge_name = judge.provider.model
    path = folder / baseline_path(model, judge_name).name
    # Everything that can stop the run is read before any model is asked.
    base = _baseline(path)
    stamp = fingerprint()
    s = evaluate(sb, judge, report, prompts)
    # The app's event loop adds to sb.served; everything below reads this one copy.
    served = set(sb.served)
    totals = Totals(mean=s.mean, groups=s.groups, rates=s.rates)
    hard, notes = model_problems(model, served, sb.unreported)
    if s.unjudged:
        hard.append(f"{s.unjudged} replies could not be judged")
    if s.silent:
        hard.append(f"{s.silent} prompts got no reply")
    drops = compare(totals, base) if base is not None else []
    # The persona eval's own lines (s.lines) are printed, but they don't fail the gate:
    # it fails on change against the baseline, and on hard problems.
    failing = hard + drops
    accepted: list[str] = []
    saved, refused = False, None
    if base is None:
        missing = f"no baseline for {model} judged by {judge_name} yet"
        if save:
            notes.append(f"{missing}: this run is the first")
        else:
            failing.append(f"{missing}; run again with --save-baseline to make one")
    if save:
        refused = may_save(hard=hard, drops=drops, accept_drop=accept_drop)
        if refused is None:
            Baseline(
                model=model,
                judge=judge_name,
                answered_by=tuple(sorted(served)),
                date=datetime.now(UTC).isoformat(timespec="seconds"),
                fingerprint=stamp,
                totals=totals,
                # A reason is kept only when there was a drop for it to explain.
                accepted_drop=accept_drop if drops else None,
            ).save(path)
            saved = True
            notes.append(f"saved baseline: {path}")
            # The drops stay in sight; only what would fail the run is cleared.
            accepted = [f"accepted: {d}" for d in drops]
            failing = []
        else:
            failing.append(refused)
    lines = s.measures()
    if s.lines:
        lines += ["persona lines (not gated):", *(f"  {line}" for line in s.lines)]
    persona = {k: v for k, v in s.totals().items() if k != "problems"}
    mean = "n/a" if s.mean is None else f"{s.mean:.2f}"
    return report.finish(
        not failing,
        f"{len(prompts)} prompts, judge average {mean}",
        notes=[*lines, *notes, *accepted, *failing],
        totals={
            **persona,
            "persona_lines": s.lines,
            "baseline": None if base is None else base.to_json(),
            "gate": {
                "hard": hard,
                "drops": drops,
                "notes": notes,
                "saved": saved,
                "refused": refused,
                # Said when this shell's environment would have the app use another model.
                "deployed_note": deployed_note,
            },
        },
        path=json_path,
    )


def _extra(p: argparse.ArgumentParser) -> None:
    p.add_argument("--deployed", action="store_true", help="grade this Trillion's model")
    p.add_argument("--save-baseline", action="store_true", help="keep this run as the baseline")
    p.add_argument(
        "--accept-drop", default=None, metavar="WHY", help="keep a worse run, saying why"
    )
    # None marks "not given", so --deployed can refuse a model named alongside it.
    p.set_defaults(model=None, url=None)


def _main(opts: argparse.Namespace) -> int:
    # Every option is checked before any model is set up or asked.
    if opts.only:
        raise CannotRun("the gate takes no --only: part of the eval can't match a baseline")
    if opts.accept_drop is not None:
        if not opts.save_baseline:
            raise CannotRun("--accept-drop only goes with --save-baseline")
        if not opts.accept_drop.strip():
            raise CannotRun("--accept-drop needs a reason")
    if opts.deployed:
        if opts.model is not None or opts.url is not None:
            raise CannotRun("--deployed grades the saved model: leave out --model and --url")
        model, url, note = deployed(paths.db_path(), os.environ)
        if note is not None:
            sys.stdout.write(note + "\n")
    else:
        note = None
        model = opts.model or harness.LOCAL_MODEL
        url = opts.url or config.LOCAL_URL
    make = harness.model_provider(model, url, os.environ)
    with harness.judge_for(opts.judge_model, opts.judge_url, os.environ) as judge:
        report = Report("gate", model, judge.line)
        with Sandbox(make, patient=True) as sb:
            return check(
                sb,
                judge,
                report,
                PROMPTS,
                model=model,
                save=opts.save_baseline,
                accept_drop=opts.accept_drop,
                json_path=opts.json,
                deployed_note=note,
            )


def main(argv: Sequence[str] | None = None) -> int:
    return harness.run(_main, argv, about=__doc__ or "", extra=_extra)


if __name__ == "__main__":
    sys.exit(main())
