"""The shared runner for the quality evals (EVL-1, EVL-2, EVL-6). Run by hand, never in CI.

Every eval takes the same options (`--model`, `--url`, `--judge-model`, `--judge-url`,
`--only <case>`, `--json <path>`) and exits the same way: 0 pass, 1 fail, 2 could not
run (no model answered, something missing). Each prints the judge first, then one line
per case, its totals and its verdict, and writes the same as JSON to evals/results/
(gitignored), which is what a later stage keeps as a baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import httpx

from trillion.brain import config
from trillion.brain.config import ANTHROPIC_KEY, LOCAL_URL, ConfigError, ModelSettings
from trillion.brain.providers.anthropic import DEFAULT_MODEL, AnthropicProvider
from trillion.brain.types import (
    ModelProvider,
    Msg,
    ProviderError,
    SystemBlock,
    TextBlock,
    TextDelta,
)
from trillion.secrets.store import MemoryStore

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


JUDGE_SYSTEM = (
    "You grade one exchange with a personal assistant against the rubric you are given. "
    "Answer with JSON only, nothing before or after it: "
    '{"score": <a whole number from 1 to 5>, "reason": "<one short sentence>"}'
)
JUDGE_MAX_TOKENS = 2000
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True, kw_only=True)
class Score:
    # 1 to 5, or None when the judge never gave a usable answer: a judge error, which is
    # reported and never counted as a pass.
    value: int | None
    reason: str

    @property
    def error(self) -> bool:
        return self.value is None


def parse_score(text: str) -> Score | None:
    """The judge's JSON, or None if it is not exactly a score from 1 to 5 and a reason."""
    try:
        body = json.loads(_FENCE.sub("", text.strip()))
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    value, reason = body.get("score"), body.get("reason")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        return None
    if not isinstance(reason, str):
        return None
    return Score(value=value, reason=reason.strip())


class Judge:
    """Scores a transcript against a rubric. Its own event loop for its whole life: a
    provider's connections belong to the loop that opened them."""

    def __init__(self, provider: ModelProvider, *, local: bool) -> None:
        self.provider = provider
        self.local = local
        self._runner = asyncio.Runner()

    @property
    def line(self) -> str:
        tail = " (local judge: scores are less reliable)" if self.local else ""
        return f"judge: {self.provider.model}{tail}"

    def score(self, rubric: str, transcript: str) -> Score:
        said = ""
        for _ in range(2):  # an answer that is not a score is asked for once more
            try:
                said = self._runner.run(self._ask(rubric, transcript))
            except ProviderError as e:
                said = f"(the judge failed: {e.kind})"
                continue
            found = parse_score(said)
            if found is not None:
                return found
        return Score(value=None, reason=f"judge error: {said[:200]!r}")

    async def _ask(self, rubric: str, transcript: str) -> str:
        asked = f"{rubric}\n\n<transcript>\n{transcript}\n</transcript>"
        text: list[str] = []
        async for ev in self.provider.stream(
            system=[SystemBlock(text=JUDGE_SYSTEM)],
            messages=[Msg(role="user", blocks=(TextBlock(text=asked),))],
            tools=[],
            max_tokens=JUDGE_MAX_TOKENS,
        ):
            if isinstance(ev, TextDelta):
                text.append(ev.text)
        return "".join(text)

    def close(self) -> None:
        self._runner.close()

    def __enter__(self) -> Judge:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


Get = Callable[..., httpx.Response]


def reachable(url: str, *, get: Get = httpx.get) -> None:
    """Something answers at this OpenAI-compatible address, or the eval cannot run."""
    try:
        answer = get(f"{url.rstrip('/')}/models", timeout=3.0)
    except httpx.HTTPError as e:
        raise CannotRun(f"nothing answered at {url}: is Ollama running?") from e
    if answer.status_code >= 400:
        raise CannotRun(f"{url} answered {answer.status_code}")


def _local(model: str, url: str) -> ModelProvider:
    try:
        # An empty store held in memory: an eval never reads or writes the keychain.
        return config.build(ModelSettings(kind="custom", model=model, base_url=url), MemoryStore())
    except ConfigError as e:
        raise CannotRun(f"{url}: {e.code}") from e


def model_provider(
    model: str, url: str, env: Mapping[str, str], *, get: Get = httpx.get
) -> Callable[[], ModelProvider]:
    """How the sandbox makes the model under test, one per conversation. A `claude-`
    model needs the key in the environment; anything else is an OpenAI-compatible
    address that must answer now."""
    if model.startswith("claude-"):
        key = env.get(ANTHROPIC_KEY, "").strip()
        if not key:
            raise CannotRun(f"{model} needs {ANTHROPIC_KEY} set in the environment")
        return lambda: AnthropicProvider(api_key=lambda: key, model=model)
    reachable(url, get=get)
    _local(model, url)  # once now, so a bad address fails before anything starts
    return lambda: _local(model, url)


def judge_for(model: str, url: str, env: Mapping[str, str], *, get: Get = httpx.get) -> Judge:
    """Claude when the key is in the environment, else the local model. Never the keychain."""
    key = env.get(ANTHROPIC_KEY, "").strip()
    if key:
        return Judge(AnthropicProvider(api_key=lambda: key, model=DEFAULT_MODEL), local=False)
    reachable(url, get=get)
    return Judge(_local(model, url), local=True)
