"""The shared runner for the quality evals (EVL-1, EVL-2, EVL-6). Run by hand, never in CI.

Every eval takes the same options (`--model`, `--url`, `--judge-model`, `--judge-url`,
`--only <case>`, `--json <path>`) and exits the same way: 0 pass, 1 fail, 2 could not
run (no model answered, something missing). Each prints the judge first, then one line
per case, its totals and its verdict, and writes the same as JSON to evals/results/
(gitignored), which is what a later stage keeps as a baseline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from trillion.brain.config import LOCAL_URL

PASS, FAIL, CANNOT_RUN = 0, 1, 2
LOCAL_MODEL = "gemma4:e4b"
RESULTS = Path(__file__).resolve().parent / "results"


class CannotRun(Exception):
    """The eval could not run at all: no model answered, or something is missing."""


def options(about: str, argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The options every eval takes."""
    p = argparse.ArgumentParser(
        description=about, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--model", default=LOCAL_MODEL, help="the model under test")
    p.add_argument("--url", default=LOCAL_URL, help="its OpenAI-compatible address")
    p.add_argument(
        "--judge-model", default=LOCAL_MODEL, help="the local judge, when no Anthropic key is set"
    )
    p.add_argument("--judge-url", default=LOCAL_URL, help="the local judge's address")
    p.add_argument(
        "--only", action="append", default=[], metavar="CASE", help="run only this (repeatable)"
    )
    p.add_argument("--json", type=Path, default=None, metavar="PATH", help="write results here")
    return p.parse_args(argv)


def run(
    main: Callable[[argparse.Namespace], int],
    argv: Sequence[str] | None = None,
    *,
    about: str = "",
) -> int:
    """Parse the options, run the eval, and turn "could not run" into exit code 2."""
    opts = options(about, argv)
    try:
        return main(opts)
    except CannotRun as e:
        sys.stderr.write(f"cannot run: {e}\n")
        return CANNOT_RUN


def pick[C](
    cases: Sequence[C], only: Sequence[str], names: Callable[[C], Iterable[str]]
) -> list[C]:
    """The cases `--only` names, by id or by any other name a case answers to."""
    if not only:
        return list(cases)
    known = {n for c in cases for n in names(c)}
    unknown = [o for o in only if o not in known]
    if unknown:
        raise CannotRun(f"no case named {', '.join(unknown)}")
    wanted = set(only)
    return [c for c in cases if wanted & set(names(c))]


def results_path(name: str, model: str, at: datetime) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"
    return RESULTS / f"{name}-{safe}-{at.strftime('%Y%m%dT%H%M%SZ')}.json"


class Report:
    """The judge first, one line per case as it finishes, then the totals and the verdict;
    and the same as JSON, UTF-8 with LF line endings on every system."""

    def __init__(
        self,
        name: str,
        model: str,
        judge: str,
        *,
        out: TextIO | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.name, self.model, self.judge = name, model, judge
        self._out = out if out is not None else sys.stdout
        self._now = now if now is not None else (lambda: datetime.now(UTC))
        self.started = self._now()
        self.cases: list[dict[str, Any]] = []
        self._say(judge)

    def _say(self, line: str) -> None:
        self._out.write(line + "\n")
        self._out.flush()

    def case(self, case_id: str, passed: bool, line: str, **data: Any) -> None:
        self.cases.append({"id": case_id, "passed": passed, **data})
        self._say(f"{'pass' if passed else 'FAIL'}  {case_id:<22} {line}")

    def finish(
        self,
        passed: bool,
        summary: str,
        *,
        notes: Sequence[str] = (),
        totals: Mapping[str, Any] | None = None,
        path: Path | None = None,
    ) -> int:
        for note in notes:
            self._say(note)
        self._say(f"{'PASS' if passed else 'FAIL'} ({self.model}): {summary}")
        target = path if path is not None else results_path(self.name, self.model, self.started)
        target.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "eval": self.name,
            "model": self.model,
            "judge": self.judge,
            "started": self.started.isoformat(),
            "finished": self._now().isoformat(),
            "verdict": "pass" if passed else "fail",
            "summary": summary,
            "totals": dict(totals or {}),
            "cases": self.cases,
        }
        with target.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(body, f, indent=2, ensure_ascii=False)
            f.write("\n")
        self._say(f"results: {target}")
        return PASS if passed else FAIL
