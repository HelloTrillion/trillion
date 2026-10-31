# Quality evals, stage 2: the tonal checkpoint, the model-change gate and drift

**Date:** 2026-10-31 · **Requirements:** PER-4 (P0), EVL-4 (P1), EVL-5 (P1), and EVL-1's "runs when the soul changes" · **Builds on:** [stage 1](2026-10-31-quality-evals.md) (`evals/harness.py`, `evals/persona/`)

Stage 1 gave us a persona eval that anyone can run by hand. Stage 2 makes it count: a change to the soul, the prompts or the default model can't merge without a fresh, comparable run, and long conversations are measured for drift. It also builds the tonal checkpoint (PER-4), which the PRD lists as P0 but which was never built, because the drift eval exists to measure it.

## Decisions

1. **By hand, for anyone.** No model is called in CI. Every eval here runs on a contributor's own machine with a model they choose, including the free local `gemma4:e4b` through Ollama. Nothing depends on a particular person's key or approval.
2. **CI enforces freshness, not a score.** A free CI check fails when the soul or prompts change and no committed baseline matches them. Any model and judge may provide that baseline, so a contributor with only Ollama can satisfy it.
3. **Like is compared with like.** A run is compared only with the baseline from the same model under test *and* the same judge. Scores from different judges are never compared.
4. **A drop is never hidden.** Saving a baseline that is worse than the one it replaces needs `--accept-drop "<reason>"`. The reason is written into the baseline file, so reviewers see it in the diff.
5. **The pass lines are starting values,** as in stage 1. Real runs set them, and the spec is updated with what was measured. A line is never loosened without saying so in the pull request.

## The tonal checkpoint (PER-4)

- From the 5th user message of a conversation onwards, `volatile_context` adds the PRD line inside `<context>`, with the user's name filled in: "Could a default chatbot have written your last line? If yes, it was wrong. Would {user_name} hear it and slightly smile? Be brief. Be witty. Snap back now."
- The line lives in `src/trillion/persona/prompts/checkpoint.md`, like the other prompt fragments.
- `volatile_context` takes a new `turn: int` (the count of user messages in this conversation, this one included). The session passes it. The scheduler's `ask` path passes 0, since a scheduled ask is a single turn.
- It rides with the newest user message only, so the cached prefix is unchanged (PER-5).
- **Tests:** absent at turn 4, present at turn 5 and turn 30, with the name filled in; the cached block is byte-identical with and without it.

## Baselines — `evals/baselines/`

One JSON file per model and judge pair: `persona-<model>--<judge>.json`, with names made safe as `results_path` already does. Each holds:

- `model` (asked for) and `answered_by` (what the provider reported), `judge`, `date`;
- `fingerprint`: SHA-256 over the shipped `persona/SOUL.md` and every file in `src/trillion/persona/prompts/`, in name order, with line endings normalised to LF;
- the persona totals: judge average, average per group, and the pass rate of each code check;
- `accepted_drop`: absent, or the reason given with `--accept-drop`.

Baselines are committed. The `evals/results/` folder stays gitignored.

## The model-change gate — `evals/gate.py` (EVL-4)

```
uv run python -m evals.gate --model gemma4:e4b
uv run python -m evals.gate --deployed
uv run python -m evals.gate --model claude-opus-5-5 --save-baseline
```

- It runs the full persona eval, then compares the result with the baseline for the same model and judge.
- **It fails when**, compared with that baseline:
  - the judge average drops by more than 0.3;
  - any group's average drops by more than 0.5;
  - any code check falls below its stage 1 line, or more than 5 points below its baseline rate.
- **No baseline for this pair:** the run reports its totals, says there is nothing to compare with, and exits 1 unless `--save-baseline` is given. The first baseline for a pair needs no `--accept-drop`.
- **The model it ran on:** each reply's provider-reported model is recorded. If any differs from the one asked for (after the provider's own aliasing, for example a dated snapshot of the same name), the run fails loudly and says which model answered. A provider that reports no model is noted in the report, not failed.
- **`--deployed`** grades the model this machine's Trillion is set to use. It reads the saved model settings and never writes them; the key comes from the environment as for every eval, never from the keychain. If the saved setting needs a key that isn't in the environment, it stops with exit 2 and names the variable to set.
- **`--save-baseline`** writes the baseline after a passing run. After a failing run it refuses unless `--accept-drop "<reason>"` is given.
- Exit codes are stage 1's: 0 pass, 1 fail, 2 could not run.

## The baseline check in CI

`tests/evals/test_baselines.py` runs in the existing Python job and needs no model:

- **Soul or prompts changed:** at least one baseline's fingerprint must match the current files. If none does, it fails with: "The persona changed since the last baseline. Run `uv run python -m evals.gate --model <any model> --save-baseline` and commit the file."
- **Default model changed:** a baseline must exist with `model` equal to `DEFAULT_MODEL` (any judge, any fingerprint). If none does, it fails and names the command. Whoever proposes the new default runs it, with their own key for that model.
- Every baseline file parses and has the fields above.

This check is what "runs when the soul changes" means while no model runs in CI.

**Bootstrap:** this change adds the check, so it must also commit at least one baseline that matches the current prompts and one for `DEFAULT_MODEL`. The pull request carries a `gemma4:e4b` baseline (free) and a `claude-opus-5-5` baseline from whoever runs it with a key.

## Drift — `evals/drift/` (EVL-5)

```
uv run python -m evals.drift.run --model gemma4:e4b
```

- **One long scripted conversation** of 30 user turns in a single session (one `Sandbox`, not `fresh()` per turn). It mixes everyday questions, a pushback, a piece of news, a request for a figure, and small talk, and about a third of the turns are spoken. The turns live in `evals/drift/script.py`.
- **Probes** at turns 1, 5, 10, 15, 20, 25 and 30 are persona-style prompts placed in the script. The judge scores each probe's reply with the persona rubric. The persona code checks run on every reply.
- **Two runs:** the conversation runs once with the checkpoint on and once with it off. The off run patches the checkpoint out inside the eval process only; the product has no setting for it.
- **It fails when, with the checkpoint on:**
  - the average of the probes in the last third (turns 20, 25, 30) is more than 0.5 below the first third (turns 1, 5, 10);
  - any probe after turn 5 scores below 3.0;
  - any code check fails on more than 5% of the replies.
- **The report** shows both runs' scores turn by turn and the gap between them. The gap is information, not a pass line: it says whether the checkpoint is earning its place.
- `--only on|off` runs one of the two. The JSON goes to `evals/results/drift-<model>-<time>.json`.

## Docs

- **PRD:** PER-4, EVL-4 and EVL-5 link to their code; EVL-1's "the run on soul changes is stage 2" becomes a pointer to the baseline check.
- **TESTING.md:** the gate, `--save-baseline`, `--accept-drop` and drift commands, and what the CI baseline check asks of a contributor.
- **CONTRIBUTING.md:** changing the soul or prompts needs a fresh baseline; Ollama's `gemma4:e4b` is enough.
- **architecture.md:** the checkpoint next to the volatile context, and the gate and drift next to the stage 1 evals.

## Platforms

As stage 1: macOS, Windows and Linux, no audio device, paths through `pathlib`, files UTF-8 with LF. The fingerprint normalises line endings so a Windows checkout matches.

## Out of scope

- Running any model in CI, and a nightly run (later, once there is a shared way to pay for it).
- The test identity (stage 3) and the full latency battery (stage 4).
- Grading a user's own edited soul: the gate and drift grade the shipped one.
