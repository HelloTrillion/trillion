"""Persona baselines (EVL-4): what a model scored, with one judge, on the soul and
prompts as they were. Committed under evals/baselines/, so anyone can compare a run
with the last one for the same model and judge, using any model, a free local one
included. Scores from different judges are never compared."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.harness import safe_name

ROOT = Path(__file__).resolve().parents[1]
SOUL = ROOT / "persona" / "SOUL.md"
PROMPTS = ROOT / "src" / "trillion" / "persona" / "prompts"
BASELINES = Path(__file__).resolve().parent / "baselines"

# How far a run may fall below its baseline before the gate fails. Starting values: the
# first real runs confirm them, and one is never loosened without saying so in the PR.
MAX_MEAN_DROP = 0.3
MAX_GROUP_DROP = 0.5
MAX_RATE_DROP = 0.05

_FIELDS = ("model", "judge", "answered_by", "date", "fingerprint", "mean", "groups", "rates")


def fingerprint(soul: Path = SOUL, prompts: Path = PROMPTS) -> str:
    """SHA-256 over the shipped soul and every prompt file, in name order, with line
    endings made LF, so a Windows checkout gets the same answer."""
    h = hashlib.sha256()
    for path in [soul, *sorted(p for p in prompts.iterdir() if p.is_file())]:
        text = path.read_bytes().replace(b"\r\n", b"\n")
        h.update(path.name.encode() + b"\0" + text + b"\0")
    return h.hexdigest()


@dataclass(frozen=True, kw_only=True)
class Totals:
    mean: float | None
    groups: dict[str, float]
    rates: dict[str, float | None]


@dataclass(frozen=True, kw_only=True)
class Baseline:
    model: str
    judge: str
    answered_by: tuple[str, ...]
    date: str
    fingerprint: str
    totals: Totals
    accepted_drop: str | None = field(default=None)

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "eval": "persona",
            "model": self.model,
            "judge": self.judge,
            "answered_by": list(self.answered_by),
            "date": self.date,
            "fingerprint": self.fingerprint,
            "mean": self.totals.mean,
            "groups": self.totals.groups,
            "rates": self.totals.rates,
        }
        if self.accepted_drop is not None:
            body["accepted_drop"] = self.accepted_drop
        return body

    @classmethod
    def from_json(cls, raw: Any) -> Baseline:
        if not isinstance(raw, dict):
            raise ValueError("a baseline is a JSON object")
        for key in _FIELDS:
            if key not in raw:
                raise ValueError(f"a baseline needs {key!r}")
        mean = raw["mean"]
        accepted = raw.get("accepted_drop")
        return cls(
            model=str(raw["model"]),
            judge=str(raw["judge"]),
            answered_by=tuple(str(m) for m in raw["answered_by"]),
            date=str(raw["date"]),
            fingerprint=str(raw["fingerprint"]),
            totals=Totals(
                mean=None if mean is None else float(mean),
                groups={str(k): float(v) for k, v in raw["groups"].items()},
                rates={str(k): None if v is None else float(v) for k, v in raw["rates"].items()},
            ),
            accepted_drop=None if accepted is None else str(accepted),
        )

    @classmethod
    def load(cls, path: Path) -> Baseline:
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(self.to_json(), f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")


def baseline_path(model: str, judge: str) -> Path:
    return BASELINES / f"persona-{safe_name(model)}--{safe_name(judge)}.json"


def load_all(folder: Path = BASELINES) -> list[Baseline]:
    return [Baseline.load(p) for p in sorted(folder.glob("*.json"))]


def compare(now: Totals, base: Baseline) -> list[str]:
    """How this run fell past the lines, compared with its baseline. A group or a check
    either side lacks is not compared."""
    was = base.totals
    problems: list[str] = []
    if now.mean is not None and was.mean is not None and was.mean - now.mean > MAX_MEAN_DROP:
        problems.append(
            f"judge average {now.mean:.2f}, down from {was.mean:.2f};"
            f" it may fall {MAX_MEAN_DROP} at most"
        )
    for g, m in now.groups.items():
        if g in was.groups and was.groups[g] - m > MAX_GROUP_DROP:
            problems.append(
                f"{g} averaged {m:.2f}, down from {was.groups[g]:.2f};"
                f" a group may fall {MAX_GROUP_DROP} at most"
            )
    for c, r in now.rates.items():
        before = was.rates.get(c)
        if r is not None and before is not None and before - r > MAX_RATE_DROP:
            problems.append(
                f"{c} held on {r:.1%}, down from {before:.1%};"
                f" a check may fall {MAX_RATE_DROP:.0%} at most"
            )
    return problems


def same_model(asked: str, served: str) -> bool:
    """True when the provider's name for what answered is the model asked for: the same
    name, a dated snapshot of it, or Ollama's ":latest" for a name with no tag."""
    if served == asked or re.fullmatch(re.escape(asked) + r"-\d{8}", served):
        return True
    return ":" not in asked and served == f"{asked}:latest"


def model_problems(
    asked: str, served: set[str], unreported: int = 0
) -> tuple[list[str], list[str]]:
    """A run answered by another model fails; replies that never said which model
    answered are noted, not failed."""
    if not served:
        return [], ["the provider never said which model answered"]
    notes = [f"{unreported} replies didn't say which model answered"] if unreported else []
    others = sorted(s for s in served if not same_model(asked, s))
    if others:
        return [f"asked for {asked}, but {', '.join(others)} answered"], notes
    return [], notes


def may_save(*, hard: list[str], drops: list[str], accept_drop: str | None) -> str | None:
    """Why this run may not become the baseline, or None. A wrong model or a reply that
    was never judged can't be accepted; a drop can, with a reason the file keeps."""
    if accept_drop is not None and not accept_drop.strip():
        return "--accept-drop needs a reason"
    if hard:
        return "this run can't be a baseline: " + "; ".join(hard)
    if drops and accept_drop is None:
        return (
            "this run is worse than the baseline; to keep it anyway, pass "
            '--accept-drop "<why>" and the reason is saved with it'
        )
    return None
