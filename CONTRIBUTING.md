# Contributing to Trillion

Thanks for being here. This project is early, which means your work will shape it more than it would in a mature repository.

Read this page before your first pull request. It is short, and two sections of it are not negotiable: **the never list** and **sign-off**.

---

## The three-pillar test

Trillion is a voice-first agent with a face and a safety spine. Every feature has to strengthen at least one pillar:

1. **The Galaxy** — the visual system: orb, landscape, constellation, arcs, celebrations.
2. **The Voice** — real-time conversation and the personality that carries it.
3. **The Spine** — safety enforced in code.

A good idea that strengthens none of them is still a good idea, just not for this repository yet. Open a discussion before you build it, and we'll tell you honestly.

We are deliberately **not** competing on the number of chat integrations, on agents that write their own skills, or on a public skill marketplace.

## The never list

A pull request will be rejected, however good the rest of it is, if it:

1. **Adds a tool without a `risk` level.** There is no default. State it.
   *Enforced:* `ToolDefinition.risk` has no default and the registry refuses anything that isn't a `Risk` — `tests/unit/test_registry.py`, `tests/unit/test_contract.py`.
2. **Marks third-party output as trusted.** Anything from the web, email, a file, a channel, a skill or an MCP server is untrusted data, not instructions.
   *Enforced:* the registry reserves `trusted_output` for first-party tools, and every other result is wrapped — `tests/unit/test_registry.py`, `tests/spine/test_untrusted_default.py`, `tests/spine/test_injection_fixtures.py`.
3. **Lets a confirmation be satisfied with arguments other than the frozen ones**, or lets a confirmation be created and satisfied in the same turn.
   *Enforced:* the confirm tool has no argument fields and the proposal columns are frozen by a database trigger — `tests/spine/test_confirmation.py`, `tests/unit/test_confirm.py`, `tests/spine/test_structure.py`.
4. **Adds a hard-delete path.** Deletion is soft: set `deleted_at`.
   *Enforced:* `DELETE FROM` is refused anywhere in source and migrations, and the decision and audit tables refuse edits — `tests/spine/test_structure.py`.
5. **Makes the identity files writable by the agent.** The agent may never edit its own soul.
   *Enforced:* the deny floor protects Trillion's data folder, including `persona/`, on both the normal and the approved path — `tests/spine/test_deny_floor.py`, `tests/unit/test_deny_floor_rules.py`.
6. **Binds a listener to anything but `127.0.0.1` by default,** or accepts a request without a session token.
   *Enforced for the token:* `tests/api/test_server.py` (no token, wrong origin, rebound hostname). The address is a constant with no option to change it (`HOST` in `src/trillion/api/app.py`); no test pins it yet.
7. **Logs conversation text, prompts, or any secret value.** Key names and lengths only.
   *Enforced for the spine:* every spine test fails if an argument or output value reaches a log — the `no_values_in_logs` fixture in `tests/spine/conftest.py`, plus `tests/spine/test_structure.py`. Other packages need the same fixture as they land.
8. **Adds a runtime dependency on a service the user did not connect.** No CDN, no secrets manager, no hosted database, no licence check, no update ping, no analytics. Vendor it, bundle it, or make it a provider the user chooses. Trillion has to start and run with the network off ("the airplane test").
   *Enforced by* the airplane test, `tests/integration/test_airplane.py` (PRD §4): every connection off the machine is refused, and Trillion still starts, serves its page, opens settings, keeps memory and holds a conversation with a local model.
9. **Hardcodes the agent's name.** Use `{agent_name}`; the brand is Trillion, but the user's agent may be called anything.
   *Enforced:* `scripts/check_agent_name.py` in CI, with its own `--selftest`.
10. **Ships personal data** — a real person's name, path, email, key or account — in code, prompts or fixtures.
   *Enforced:* `scripts/containment_check.py` (with `--selftest`) and gitleaks in CI.
11. **Leaves two implementations live past a decision.** Exactly one code path decides any routing, classification or status. A flag or a shadow run is temporary; when the decision is made, the loser is deleted, not left behind a flag.
   *Enforced by* `scripts/check_one_writer.py` in CI: each decision field (where an action stands, its effect, its outcome) is changed by exactly one function, reached only through the router's one path; the decision record and the audit log are only ever appended to, by one writer each; and any column a statement changes that the check does not know fails until it is classified as a decision or not. Its self-test proves each way of breaking the rule is caught.

The goal is that every rule has a structural enforcement — the credential doesn't exist, the tool has no argument field, the lint fails — and a test, because a rule that is only prose is a rule that goes missing under pressure. Each rule above says which it is today. A new rule arrives with its check, or is marked *not enforced yet* and held in review until its check lands.

If you think one of these rules is wrong, open an issue and argue the case. Don't route around it in a pull request.

## Getting started

```bash
git clone https://github.com/HelloTrillion/trillion.git
cd trillion
uv sync                 # Python 3.13 toolchain and dependencies
uv run pytest           # every Python test
pnpm install            # JS workspace (Node >=22.22.2)
pnpm --filter @hellotrillion/galaxy dev   # the orb, in your browser
```

To run the app itself, see "Try it" in the [README](README.md). While working on the web app, run `uv run trillion run --dev` in one terminal and `pnpm --filter @hellotrillion/web dev` in another, and open the link the first one prints.

Before you push, the same checks CI runs:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest
for s in containment_check check_agent_name check_raw_hex check_one_writer; do
  uv run python scripts/$s.py --selftest && uv run python scripts/$s.py
done
pnpm typecheck && pnpm test && pnpm -r --if-present --filter '!@hellotrillion/desktop' run build
```

On Windows, run the loop in Git Bash, or run the eight `uv run python scripts/…` commands one by one in PowerShell. In PowerShell, type `pnpm.cmd` wherever these steps say `pnpm`: the default execution policy refuses to run the `pnpm.ps1` shim.

[`docs/PRD.md`](docs/PRD.md) is the specification: every requirement carries an ID (`GAL-3`, `VOI-2`, `SPN-5`, …), and issues reference those IDs. [`docs/TESTING.md`](docs/TESTING.md) says how each one is proven. [`docs/architecture.md`](docs/architecture.md) is the map.

## Working agreement

- **Claim an issue before starting** anything larger than a typo, so two people don't build the same thing.
- **One concern per pull request.** A refactor plus a feature is two pull requests.
- **Tests are required** for anything marked P0 in the PRD, and for every bug fix. A bug fix without a test that fails before it is not finished.
- **Match the surrounding code.** No reformatting of files you didn't otherwise change.
- **Write the commit message for the person reading it in a year.** Say why, not just what.
- **Every spec has a Platforms section**: what each part does on macOS, Windows and Linux, and what it falls back to. Update [`docs/platforms.md`](docs/platforms.md) in the same pull request.

### Areas that need a maintainer review

`CODEOWNERS` requires a maintainer on:

- `src/trillion/spine/**` — the safety guarantees
- `src/trillion/persona/**` and `persona/**` — the identity
- `src/trillion/voice/**` capture — hard-won microphone behaviour
- `tests/spine/**` — the tests that prove the guarantees
- `.github/**` — CI, including the checks above

This is not distrust. These are the parts where a subtle change quietly removes a promise we make to users.

## Adding a feature without touching shared files

Many pull requests are open at once, so a feature lives in files of its own and plugs in. It doesn't add a line to a shared list. `scripts/check_plug_in.py` fails CI when a shared file gains a hook, and says what to do instead.

| You're adding | Do this | Not this |
|---|---|---|
| A Settings section | A file in `apps/web/src/features/` that exports `feature` and calls `panel.add({ …, after: '<section id>' })`. See `features/memories.ts`. | A `panel.add` line in `main.ts` |
| Its styles | A CSS file beside it, imported by it (`features/memories.css`) | A block at the end of `app.css` |
| Its icon | `addIcon(name, paths)` in its file | A line in `icons.ts` |
| Session messages it hears | `ctx.on((msg) => …)` in its `setup` | A `handle(msg)` line in `main.ts` |
| HTTP routes | End the router's module with `ROUTES = (create_my_router,)` | An `include_router` line in `app.py` |
| A tool that needs the runtime | `async def attach(self, rt)` on the tool (see `tools/roast.py`) | A `bind(…)` in `runtime.py` |
| A database migration | `YYYYMMDDHHMM_name.sql`, named by the time you wrote it, plus `tests/unit/shipped_migrations/<name>.sha256` | The next three-digit number |
| A test that applies migrations | `names(after="016")` from `trillion.storage.migrate` | A copied list of migration names |

When you move an old hook out of a shared file, lower its number in `scripts/check_plug_in.py` in the same pull request. The numbers only go down.

## Changing her voice

Her voice comes from the soul (`persona/SOUL.md`) and the prompts in `src/trillion/persona/prompts/`. When you change either, CI fails until a persona baseline matches them. CI runs no model: you make the baseline on your own machine and commit it.

```bash
uv run python -m evals.gate --model gemma4:e4b --save-baseline
```

A free local model is enough: with `TRILLION_ANTHROPIC_API_KEY` unset, Ollama's `gemma4:e4b` both answers and judges, with no paid key. With a local judge the gate runs the eval three times and pools them, because one run of a small model is mostly noise; expect about an hour on `gemma4:e4b`. Commit the file it writes in `evals/baselines/`. If the run is worse than the last baseline for the same model and judge, add `--accept-drop "<why>"`. The reason is saved in the file, so reviewers see it in the diff. Changing the default model needs a baseline for the new model, run by whoever proposes it. If you change `checkpoint.md`, the reminder she gets from her fifth message, the gate can't see it: every persona prompt is a one-message conversation. Also run `uv run python -m evals.drift.run --model gemma4:e4b` and put the gap it reports in the pull request. The gate needs a server that reports the model's real name, as Ollama does; a server that answers under another name can't make a baseline. The full rules, and the drift eval, are in [`docs/TESTING.md`](docs/TESTING.md#32-persona-evl-1-evl-4-evl-5).

## Sign-off (DCO)

Every commit must carry a `Signed-off-by` line certifying that you wrote the contribution or have the right to submit it. The full text is in [`DCO`](DCO).

Git adds the line for you:

```bash
git commit -s -m "Your message"
```

Forgot on the last commit?

```bash
git commit --amend -s --no-edit
```

We use a DCO rather than a contributor license agreement, so there is nothing to sign and no paperwork.

## Reporting bugs

Open an issue with what you expected, what happened, and how to reproduce it. For anything voice-related, include the output of `trillion doctor` and your operating system and microphone or audio interface. Microphone bugs are almost always device-specific.

**Never paste an API key, a token or a log containing one.** Redact before you post.

For security vulnerabilities, follow [`SECURITY.md`](SECURITY.md) instead. Do not open a public issue.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Reports go to me@hellotrillion.ai.
