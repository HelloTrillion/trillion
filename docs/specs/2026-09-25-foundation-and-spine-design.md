# Foundation (Track F) and core spine (Track C) — design

**Date:** 2026-09-25 · **Status:** approved in design review, awaiting spec review
**Implements:** PRD §9 Track F in full; Track C requirements SPN-1…7, 9, 12, 14, 16, 17, 17a, 17b, 19 (SPN-2a–c included). Also CON-3's file tools.
**Proven by:** `docs/TESTING.md` §1.1 and §1.2, plus the scenarios listed in §7 below.

This cycle builds the base that every other track depends on: a Python and JS toolchain with CI that enforces the never list, and a spine that makes it impossible for a tool to run anywhere except through the router.

---

## 1. Decisions taken in design review

| # | Decision | Consequence |
|---|---|---|
| D1 | The first cycle is Track F, then the core spine. | Galaxy, Voice, Brain and App start from a green CI and a working router. |
| D2 | The wake word is **Phase 1** (VOI-10a). | §9 done item 3 drops "saying Hello Trillion still wakes it". The `wake.phrase` / `wake.model` settings still exist in Phase 0, separate from `agent_name`. |
| D3 | This cycle's spine excludes network (SPN-11, 11a, 11b) and secrets (SEC-*). | Session binding (SPN-14, 16) is enforced on a `session_id` carried in `ToolContext`; real tokens arrive with the API. |
| D4 | **Every file write asks first.** `file_create`, `file_overwrite` and `file_append` are all `irreversible`. | §9 done item 7 works as written. Overwrite is also unrecoverable, so it needs a tap (SPN-14). |
| D5 | Architecture: a pure core with a thin async layer, repository interfaces, and plain numbered SQL migrations. | No ORM. The SQL that carries the guarantees is visible in the repository. |

## 2. PRD consistency fixes (in the same pull request)

1. §9 item 3: remove the wake-word clause (D2). §13 "Wake word" points to VOI-10a and says it is Phase 1.
2. §9 "Out of scope": remove "Linux" (SUR-3a makes it P0); keep Windows. §7 Portability: macOS 13+ and Ubuntu 22.04+ are P0, Windows 11 is P1.
3. ONB-3: ask for keys in ladder order: model, then text-to-speech, then speech-to-text. ONB-4: works with no keys at rung 0 (P0); the no-paid-keys *voice* tier is VOI-11b (P1).
4. §8.4: `connector_credentials` holds **metadata only** (name, added_at, last_used, store kind), never a value (SEC-4). Migrations are added per track as each lands, rather than all core tables existing in 001.
5. §9 Track B adds VOI-9 (tap barge-in) and VOI-10 (push-to-talk). Track D lists PER-1…6, 8…11 (PER-7 is P1).
6. §11 License: settled, not "pending". §10 Phase 1: remove the duplicate "wake word".
7. CON-3 / SPN-1: file writes are split into create, overwrite and append, all `irreversible` (D4).

---

## 3. Track F: repository and CI

### 3.1 Local checkout
The working copy is a clone of `HelloTrillion/trillion`. Maintainer-only planning drafts are excluded through `.git/info/exclude`, never through the shared `.gitignore`.

### 3.2 Python
- Root `pyproject.toml`: package `trillion` from `src/trillion`, Python `>=3.13`, built with `hatchling`, managed with uv. Console script `trillion` (a stub CLI that prints its version).
- Entry-point groups declared, empty: `trillion.tools`, `trillion.connectors`, `trillion.checkers`, `trillion.channels` (CON-7). First-party file tools register under `trillion.tools`.
- Runtime dependencies this cycle: `aiosqlite`, `jsonschema`. Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`.
- `mypy --strict` on `trillion.spine`, `trillion.storage`, `trillion.tools`, and, once they have code, `trillion.voice` and `trillion.brain`. Default mypy settings everywhere else.
- ruff: `E, F, W, I, B, UP, S, T20, LOG, G, RUF`. `T20` bans `print` in `src/`. `LOG`/`G` keep logging calls lazy.

### 3.3 JavaScript
- Root `package.json` (private, `packageManager: pnpm@9`), `pnpm-workspace.yaml` covering `apps/*` and `packages/*`, and a shared `tsconfig.base.json` with `strict`, `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`.
- No packages yet. Galaxy and web add their own.

### 3.4 CI (added to the existing containment, gitleaks and DCO jobs)
| Job | Runs | Matrix |
|---|---|---|
| `python` | `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy`, `pytest tests/unit tests/integration` | macOS, Ubuntu |
| `spine` | `pytest tests/spine --cov=trillion.spine --cov-branch --cov-fail-under=95` | macOS, Ubuntu |
| `structure` | `scripts/check_agent_name.py --selftest` and then the check itself | Ubuntu |
| `js` | `pnpm install --frozen-lockfile` and `pnpm -r exec tsc -b`; skipped when no workspace package exists | Ubuntu |

### 3.5 Structural checks
- `scripts/check_agent_name.py` (PER-1a): scans prompt templates (`src/trillion/**/prompts/**`, `persona/**`) and strings passed to the agent-facing helpers for the literal `Trillion`. An allowlist covers brand spots: the CLI name, the package name, docs and UI chrome. It has a self-test in the same style as `containment_check.py`.
- The "router-only execute" and "no hard delete" rules are tests in `tests/spine/` (§7.2), because they protect the spine.

### 3.6 Documentation
- `docs/architecture.md`: the process model, module map, the life of a tool call, and where each guarantee is enforced (PRD §9 item 16).
- CONTRIBUTING "Getting started": `uv sync`, `uv run pytest`, `pnpm install`.
- `docs/TESTING.md`: each scenario links to its test.
- A GitHub issue for never-list #11 ("no two paths write the same decision field"), deferred until there is decision code to check.

---

## 4. The spine: modules and contract

All modules live in `src/trillion/spine/`. Every module except `router.py` is pure.

| Module | Responsibility |
|---|---|
| `contract.py` | `Risk`, `ToolDefinition`, `Tool` protocol, `ToolContext`, `ToolResult`, `ActionSummary`, `Precondition` |
| `hashing.py` | Canonical JSON (sorted keys, no whitespace, UTF-8) → SHA-256. One function, used by everything. |
| `deny_floor.py` | `check(defn, args, policy) -> Denial \| None` |
| `untrusted.py` | `wrap(tool_name, text)` and defanging |
| `neutralize.py` | `neutralize(text) -> (clean_text, findings)` (SPN-6) |
| `loop_guard.py` | Repeat and failure thresholds, iteration, wall-clock and cost ceilings; typed `StopReason` |
| `confirm.py` | The `PendingAction` model and the pure `transition()` state machine |
| `audit.py` | The `AuditRecord` shape |
| `registry.py` | Registration, entry-point discovery, load-time validation |
| `store.py` | The `SpineStore` protocol |
| `router.py` | The only caller of `Tool.execute`. Orchestration and I/O. |

### 4.1 Contract

```python
class Risk(StrEnum):
    READ = "read"; WRITE = "write"; IRREVERSIBLE = "irreversible"; SPEND = "spend"

@dataclass(frozen=True, kw_only=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    risk: Risk                      # required, no default (SPN-1)
    category: str
    timeout_s: float
    path_args: tuple[str, ...] = ()  # argument names that are filesystem paths
    recoverable: bool = True        # False ⇒ tap required to confirm (SPN-14)
    trusted_output: bool = False    # first-party pure tools only (SPN-5)

class Tool(Protocol):
    definition: ToolDefinition
    def describe(self, args: Mapping[str, Any]) -> ActionSummary: ...        # SPN-2a
    def target(self, args: Mapping[str, Any]) -> str: ...                    # conflict key
    async def precheck(self, args, ctx) -> Precondition: ...                 # SPN-2c
    async def execute(self, args, ctx) -> ToolResult: ...
    async def reconcile(self, args, ctx) -> ToolResult | None: ...           # SPN-17a, optional

@dataclass(frozen=True, kw_only=True)
class ToolContext:
    owner_id: str
    session_id: str
    turn: int                        # committed user turns (VOI-15a); owned by the brain later
    call_id: str
    via: Literal["voice", "text", "tap", "channel"]
    granted_roots: tuple[Path, ...]

@dataclass(frozen=True, kw_only=True)
class ToolResult:
    ok: bool
    content: str
    effect: Literal["confirmed", "none", "unknown"]
    error_kind: str | None = None
```

A base class `BaseTool` supplies the defaults: `target` returns the tool name, `precheck` always passes, and `reconcile` returns `None`.

**Load-time validation (`registry.py`):** registration raises if:
- `risk` is missing or invalid
- the name collides with another tool
- a `path_args` entry is not in the schema
- a schema property whose name matches `path|file|dir|folder` is not declared in `path_args`
- `trusted_output=True` comes from a module outside `trillion.tools`

`confirm_pending_action` and `cancel_pending_action` are registered by the spine itself. Their schema has exactly one property, `pending_id`, and `additionalProperties: false`.

### 4.2 Router pipeline

`async def execute(call: ToolCall, ctx: ToolContext) -> RouterResponse`

1. **Look up** the tool. If it is unknown → `refused(unknown_tool)`.
2. **Validate** the arguments against `input_schema` with `jsonschema` (Draft 2020-12). If they are invalid → `refused(invalid_args, detail)`.
3. **Deny floor** → `refused(denied_by_floor, reason)`.
4. **Loop guard** records the call. `stop` → `refused(loop_guard, stop_reason)`; `warn` is attached to the response.
5. **Branch on risk:**
   - `read` / `write`: precheck → execute with `asyncio.wait_for(timeout_s)` → wrap the output unless `trusted_output` → `succeeded` / `failed`.
   - `irreversible` / `spend`: precheck → supersede any open proposal with the same `(session, tool, target)` → freeze the arguments into a `PendingAction` (status `proposed`) → `confirmation_required{pending_id, summary}`, where the summary comes from `Tool.describe` on the frozen arguments.
6. **Audit** (always, including refusals): tool, args_hash, risk, decision, outcome, duration_ms, session_id, owner_id.

`async def resolve(pending_id, decision: Literal["confirm", "cancel"], ctx) -> RouterResponse`

- Load the action, check for expiry on the injected clock, then apply `transition()`. The refusal reasons are:
  - `not_found`
  - `wrong_owner`
  - `wrong_session`
  - `same_turn`
  - `channel`
  - `tap_required` (for `spend` or `recoverable=False` when `via != "tap"`)
  - `expired`
  - `superseded`
  - `already_resolved`, which returns the recorded outcome
- On confirm: deny floor again → precheck again (a failure moves the action to `invalidated` and returns `refused(precondition_changed)` with the new facts, so the host can `represent()` it) → claim atomically → execute → record the outcome and the decision.

`async def cancel_on_no(words, ctx) -> RouterResponse | None`

- An explicit no ends a pending action (SPN-2f) without relying on the model. When the user's whole turn, spoken or typed (`via` voice or text), is a plain refusal ("no", "no thanks", "cancel that", "never mind", "stop", give or take filler such as "um" and punctuation; `spine/refusal.py`), and exactly one unexpired action is open in the session that raised it, the host cancels it through `resolve(..., "cancel")`, so the decision record shows it declined with that `via`.
- Anything more than a plain no ("no, wait, make it say hello"), no open action, or more than one is left to the model. A "yes" is never read here: approval keeps every rule above.
- The turn still goes to the model, with a `[host]` note saying the card was declined, so she answers in her own words and does not cancel it again.

Execution exceptions are caught as `failed(error_kind=<exception class>, detail=<message, neutralized>)`. Tracebacks go to the log. Argument values never do.

### 4.3 Deny floor

`DenyPolicy` holds the protected roots, resolved once from `trillion.paths` and the installed package location, and the granted roots from the context. For each `path_args` value:
- expand `~`
- resolve symlinks (`strict=False`)
- compare lower-cased path parts

It denies:
- any path segment `.git`, `.ssh`, `.aws` or `.gnupg`
- a file name `.env` or `.env.*`
- anything under the Trillion data directory (`~/.trillion/`, which covers identity, secrets and the database)
- anything under the installed `trillion` package directory
- the macOS keychain directories (`~/Library/Keychains`)
- any path that is not under a granted root

A value that is not a string is refused (fail closed), unlike V1's pass-through.

### 4.4 Untrusted wrapping and neutralizing
- Every tool output is wrapped as `<untrusted_tool_result tool="name">`, followed by the preamble, the content and the closing tag, unless `trusted_output` is set.
- Defanging replaces any occurrence of the opening or closing tag, matched case-insensitively and allowing internal whitespace, with full-width bracket look-alikes.
- `neutralize()` strips C0/C1 control characters (keeping `\n` and `\t`), zero-width and bidi-override characters, role markers (`system:`, `assistant:`, `<|im_start|>`, and so on) and fence delimiters. It returns a list of findings. `file_read` output is neutralized before it is wrapped.

### 4.5 Loop guard
Per agent loop:
- The same `(tool, args_hash)`: warn at 3, stop at 5.
- The same tool failing: warn at 3, stop at 8.
- Iteration, wall-clock and cost ceilings are supplied by the caller.

`StopReason` is a `StrEnum`: `repeat`, `failing_tool`, `iterations`, `wall_clock`, `cost`.

---

## 5. Confirmation lifecycle

```
proposed ──confirm──▶ approved ──claim──▶ executing ──▶ succeeded | failed | unknown
    │                      └── precheck fails at execution time ──▶ invalidated ──represent()──▶ new proposal
    ├── cancel ──▶ rejected
    ├── newer proposal, same (session, tool, target) ──▶ superseded
    └── now ≥ expires_at (10 min) ──▶ expired ──represent()──▶ new proposal (represented_from)
```

- **Expiry** depends on state and the wall clock only (SPN-2f). The clock is injected into the code.
- **`represent(pending_id, ctx)`** copies the frozen arguments into a new proposal with a new id, the current turn, and a precheck run again. It is valid only from `expired` or `invalidated`. Nothing calls it automatically.
- **Atomic claim:** `UPDATE pending_actions SET status='executing' … WHERE id=? AND status='approved'`. Only a caller that changes exactly one row executes the action. Every other caller gets the recorded outcome (SPN-17b; the idempotency key is `pending_id`).
- **Success needs evidence:** `succeeded` requires `ok and effect != "none"`. `ok` with `effect == "unknown"` is stored as `unknown`.
- **Recovery at startup:** every `executing` row goes through `reconcile()`. If it returns `None` or raises, the row becomes `unknown`. Nothing is retried automatically.
- **Decision record:** every terminal or resolving event appends a row to `decisions`. `mark_delivered(pending_id, surface)` inserts into `decision_deliveries` with `INSERT OR IGNORE`, so the first render wins.

The model-facing response is always built from the stored row. It is one of `confirmation_required`, `succeeded`, `failed`, `unknown` or `refused`, each with typed fields. No free-text status is ever made up.

---

## 6. Storage

- `trillion/paths.py`: `home()` returns `$TRILLION_HOME` or `~/.trillion`, plus `db_path()`, `persona_dir()`, `secrets_path()` and `models_dir()`.
- `trillion/storage/db.py`: `connect(path)` opens an `aiosqlite` connection with WAL, `foreign_keys=ON`, `busy_timeout=5000` and `synchronous=NORMAL`.
- `trillion/storage/migrate.py`: applies `trillion/storage/migrations/NNN_name.sql` files (bundled as package data) in order, each inside a transaction. `schema_migrations(version, name, checksum, applied_at)`. It refuses to start if an applied file's checksum has changed.
- `trillion/storage/sqlite/spine_store.py` implements `SpineStore`.

**`001_spine.sql`:** every table has `owner_id TEXT NOT NULL REFERENCES owners(id)`. Times are stored as integer epoch milliseconds.

| Table | Columns (beyond `owner_id`) | Integrity |
|---|---|---|
| `owners` | `id` PK, `created_at` | Seeded with `local` |
| `pending_actions` | `id` PK, `tool`, `args_json`, `args_hash`, `summary`, `target`, `session_id`, `created_turn`, `status` (CHECK in the state set), `expires_at`, `represented_from` FK, `effect_status`, `outcome_detail`, `created_at`, `updated_at` | Index on `(session_id, tool, target, status)` |
| `decisions` | `id` PK, `pending_id` FK, `event`, `decided_by` (session), `via`, `at` | Triggers abort UPDATE and DELETE |
| `decision_deliveries` | `pending_id` PK/FK, `surface`, `delivered_at` | Set once |
| `tool_audit` | `id` PK, `call_id`, `tool`, `args_hash`, `risk`, `decision`, `outcome`, `duration_ms`, `session_id`, `at` | Triggers abort UPDATE and DELETE |

Frozen arguments live only in the user's local database, and they never reach a log.

---

## 7. First-party tools and tests

### 7.1 File tools (`src/trillion/tools/files.py`)

| Tool | Risk | Recoverable | Precheck | Effect evidence |
|---|---|---|---|---|
| `file_list` | read | — | path is a directory | — |
| `file_read` | read | — | path is a file, ≤ 1 MB | output neutralized, then wrapped |
| `file_create` | irreversible | yes | the file does **not** exist; its parent exists | read back, compare the sha256 of the frozen content |
| `file_overwrite` | irreversible | **no** (tap) | the file exists; its size and hash are recorded in the summary | read back and compare the hash |
| `file_append` | irreversible | yes | the file exists | the tail matches the appended bytes |

Summaries come from the host, for example: "Create note.txt on Desktop (12 bytes)" or "Replace report.md in Documents (2.1 KB → 3.4 KB)". Every tool declares `path_args=("path",)`. The target is the resolved path.

### 7.2 Tests

**`tests/unit/`:**
- canonical hashing
- the exhaustive `transition()` table
- loop-guard thresholds and ceilings
- neutralizer findings
- wrapping, with a property test that for any input the output contains exactly one real closing tag
- registry validation (missing risk, undeclared path argument, third-party trusted output)
- migration checksum refusal

**`tests/spine/`** (each file cites its TESTING.md scenario):
- `test_confirmation.py`: §1.1.1–1.1.10
- `test_outcomes.py`: §1.2.1 (crash → `unknown`, no retry); 20 concurrent confirms → 1 execution; `ok` without an effect ≠ `succeeded`
- `test_compaction.py`: SPN-3. The message history is discarded between the proposal and the confirmation.
- `test_deny_floor.py`: each protected rule, capitals such as `.GIT`, a symlink into `.ssh`, `~/.trillion/`, outside the granted roots, and the same checks on the confirmed path
- `test_untrusted_default.py`: an unconfigured tool is wrapped
- `test_honest_failure.py`: SPN-19 fixtures for an empty result, an error, and an acknowledgement only
- `test_structure.py`:
  - an AST scan showing only `spine/router.py` calls `.execute(`
  - no `DELETE` outside the forbidding triggers
  - the triggers abort
  - log capture across the whole suite contains no fixture argument values
- `test_injection_fixtures.py`: hostile file content read by `file_read` is wrapped and neutralized, and no tool call can create or close a gate from it

**Coverage:** branch coverage of at least 95% on `trillion.spine`, enforced in the `spine` CI job.

---

## 8. Out of scope for this cycle
- Network, session tokens and egress (SPN-11, 11a, 11b); secrets (SEC-*); the API and WebSocket
- The model-judged injection eval (EVL-2), which moves to Track D because it needs a model
- The brain's turn counter (VOI-15a). The spine takes `turn` from its caller.
- Any UI, including the confirmation card. `mark_delivered` exists and is tested.
- `web_fetch`, `weather`, `memory_*` and `describe_self`

## 9. Exit criteria
- CI is green on macOS and Ubuntu: containment, gitleaks, DCO, python, spine (≥ 95% branch), structure, js (skipped).
- Every test in §7.2 exists and passes. Every TESTING.md §1.1 and §1.2 scenario links to its test.
- The PRD fixes in §2 are merged, and `docs/architecture.md` is in place.
