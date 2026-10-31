"""The free CI half of the persona gate (EVL-4): no model runs here. A change to the
soul or a prompt, or to the default model, needs a fresh baseline in evals/baselines/,
which anyone can make with a free local model."""

from __future__ import annotations

from evals.baselines import BASELINES, Baseline, fingerprint, load_all

from trillion.brain.providers.anthropic import DEFAULT_MODEL

HOW = "uv run python -m evals.gate --model {model} --save-baseline"


def test_every_baseline_file_is_whole() -> None:
    files = sorted(BASELINES.glob("*.json"))
    assert files, "evals/baselines/ is empty"
    for path in files:
        Baseline.load(path)


def test_a_baseline_matches_the_soul_and_prompts_as_they_are_now() -> None:
    now = fingerprint()
    assert any(b.fingerprint == now for b in load_all()), (
        "The persona changed since the last baseline. Run "
        f"`{HOW.format(model='gemma4:e4b')}` (free, through Ollama, with any model you like) "
        "and commit the file it writes in evals/baselines/. If you changed checkpoint.md, "
        "also run `uv run python -m evals.drift.run` and put its gap in the pull request: "
        "see CONTRIBUTING.md, Changing her voice."
    )


def test_the_default_model_has_a_baseline() -> None:
    assert any(b.model == DEFAULT_MODEL for b in load_all()), (
        f"No baseline for the default model {DEFAULT_MODEL}. Whoever changes it runs "
        f"`{HOW.format(model=DEFAULT_MODEL)}` and commits the file."
    )
