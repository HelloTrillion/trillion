# A steadier persona gate

**Date:** 2026-11-01 · **Requirement:** EVL-4 · **Builds on:** [quality evals stage 2](2026-10-31-quality-evals-stage-2.md) (`evals/gate.py`, `evals/baselines.py`)

The gate compares one run of the persona eval with a baseline that was also one run. On a small local model that's a coin toss: with nothing relevant changed, gemma4:e4b's `bait` average went from 4.62 to 3.12 between two runs, three times the gate's limit for a group. A contributor with only a free local model can't tell a real drop from noise. This change averages several runs, and stops one silent reply from sinking a run.

## Decisions

1. **Three runs with a local judge, one with Claude.** A local judge (the `Judge.local` flag the harness already sets) is the noisy case, and running it costs nothing but time. A Claude judge is much steadier, so a Claude run costs what it does today unless someone asks for more. `--runs N` overrides either.
2. **Pool, don't average averages.** Every reply from every run goes into one summary, so the judge average, each group's average and each code check's rate are over all of them. `summarize` already does this; the gate hands it more rows.
3. **A silent prompt gets one more chance.** A prompt with no reply is asked once more in a new conversation. It counts as silent, which is a hard problem, only when that second ask is silent too.
4. **Old baselines still work.** A baseline file without `runs` counts as one run.

## Running it

```
uv run python -m evals.gate --model gemma4:e4b                 # 3 runs: the judge is local
uv run python -m evals.gate --model claude-opus-5-5            # 1 run: the judge is Claude
uv run python -m evals.gate --model gemma4:e4b --runs 5
```

- `--runs` takes a whole number of 1 or more; anything else stops with exit 2 before any model is set up.
- Each run is the full fifty prompts, each in its own new conversation, as today.
- The repeated-opener check stays inside a run: it compares a reply with the ones just before it in that run.
- Each prompt's line is printed when its run finishes. From the second run on, its id carries the run: `bait-06 #2`. The lowest-scored list uses the same labels.
- The verdict line names the size: "3 runs × 50 prompts, judge average 3.62". With one run it reads as today: "50 prompts, judge average 3.62".
- The JSON's `totals` gains `runs`.

## Asked again after no reply

- In the persona eval's asking step, which the gate shares: when a prompt's reply is empty, the sandbox starts a new conversation and asks it once more. The second reply is the one used.
- A prompt asked twice says so on its line ("asked twice"), and its JSON case has `retried: true`.
- The summary counts them. The gate counts in asks, out of every ask in every run, since one flaky prompt can be silent in more than one run: it notes "2 of 150 asks got no reply the first time and were asked again"; it isn't a problem, since the reply that counts is the second.
- If the second ask is silent too, the prompt is silent, exactly as today; the gate fails with "1 of 150 asks got no reply".

## Baselines

- The file gains `runs`: how many runs it pooled. Files without it load as `runs: 1`. A `runs` below 1 is refused as unreadable.
- Saving writes the run count of the run being saved.
- When this run and its baseline pooled a different number of runs, the gate still compares them and adds a note, never a failure:
  - baseline from fewer runs: "the baseline is from 1 run, this from 3; re-save it with --runs 3 for a steadier yardstick";
  - baseline from more: "the baseline is from 3 runs, this from 1".
- The limits (0.3 for the average, 0.5 for a group, 5 points for a check) stay as they are. Fewer false alarms come from steadier numbers, not looser lines.

## The baselines in this change

- `persona-gemma4-e4b--gemma4-e4b.json` is re-saved from a three-run gate. The prompts haven't changed, so CI doesn't need it, but the next contributor compares against a steadier yardstick. A drop against today's one-run file is saved with `--accept-drop`, giving that reason.
- `persona-claude-opus-5-5--claude-opus-5-5.json` stays as it is: one run, which is the default for a Claude judge.

## Docs

- The gate's docstring: `--runs` and its defaults.
- **TESTING.md:** the gate runs three times with a local judge; how long that takes on gemma; `--runs`.
- **CONTRIBUTING.md, "Changing her voice":** the same, briefly; one run of a small local model is noise.

## Out of scope

- Moving the limits, or setting them per judge.
- More runs for drift. It's one long conversation whose cost is per turn; it can come later if its numbers prove as noisy.
- Running prompts in parallel to save time.
