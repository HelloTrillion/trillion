# Architecture

This is the map: one process, what each package does, how a tool call moves through the spine, and where each safety guarantee is enforced and tested. It assumes you've read [`docs/PRD.md`](PRD.md); it doesn't restate the requirements, only where they live in code.

## 1. One process

`trillion run` serves the UI and the API and runs the background workers, all in a single process on the user's machine. State lives in a SQLite database at `~/.trillion/trillion.db`; set `TRILLION_HOME` to move the whole data directory somewhere else. There is no second process, no hosted sidecar, and no service of ours in the loop — see the PRD's self-contained rule (§4) and process model (§8.5).

## 2. Module map

The `§8.1` tree from the PRD is the target layout. Today, only `trillion/cli` has working code; `trillion/spine`, `trillion/storage` and `trillion/tools` are being built this cycle (see the design doc below); everything else is a placeholder package waiting for its track.

| Package | Status |
|---|---|
| `trillion/cli` | exists — the `trillion` command |
| `trillion/spine` | in progress — see `docs/specs/2026-09-25-foundation-and-spine-design.md` |
| `trillion/storage` | in progress — see `docs/specs/2026-09-25-foundation-and-spine-design.md` |
| `trillion/tools` | in progress — see `docs/specs/2026-09-25-foundation-and-spine-design.md` |
| `trillion/api` | placeholder |
| `trillion/brain` | placeholder |
| `trillion/persona` | placeholder |
| `trillion/voice` | placeholder |
| `trillion/memory` | placeholder |
| `trillion/agents` | placeholder |
| `trillion/scheduler` | placeholder |
| `trillion/secrets` | placeholder |
| `trillion/connectors` | placeholder |
| `trillion/channels` | placeholder |

## 3. The life of a tool call

Every tool call — from the model, a sub-agent, a skill, an MCP server or a channel — goes through the same pipeline. There is no second door.

**The first pass**, for any risk level:

1. **Lookup.** The tool must be registered, or the call is refused.
2. **Schema validation.** Arguments are checked against the tool's declared `input_schema`.
3. **Deny floor.** A pure function checks the resolved paths against the protected roots (`.git`, `.ssh`, secrets, the identity files, the app's own source, anything outside a granted root). A denial here cannot be argued around.
4. **Loop guard.** The same tool with the same arguments, or the same tool repeatedly failing, is throttled and eventually stopped.
5. **Risk branch:**
   - `read` and `write` tools run immediately: precheck, execute, wrap the output as untrusted (unless the tool is a trusted first-party source), and record the outcome.
   - `irreversible` and `spend` tools never run on the turn they were proposed. Their exact arguments are frozen into a `PendingAction` and the model is told to ask the user for a yes.
6. **Audit.** Every call — including a refusal — is recorded: the tool, a hash of the arguments, the risk, the decision, the outcome and the duration.

**The confirm path**, once the user says yes:

1. The pending action's state machine transitions from `proposed` toward `approved`. A no, an expiry, or a newer conflicting proposal ends it a different way.
2. The deny floor runs again, because conditions may have changed since the proposal.
3. The precondition is rechecked at execution time, not trusted from proposal time — a file that didn't exist an hour ago might exist now.
4. An atomic claim ensures exactly one caller executes the action, even if the same "yes" arrives twice.
5. The tool executes, and the outcome — succeeded, failed, or unknown, never guessed — is written to an immutable decision record.

## 4. Where each guarantee lives

Every row below is planned for this cycle; none of it is implemented yet. The status column exists so this table stays honest as the work lands — a row moves out of "planned" only when its test passes in CI.

| Guarantee | Module | Test | Status |
|---|---|---|---|
| A tool must declare a risk level; there is no default | `spine/contract.py`, `spine/registry.py` | `tests/spine/test_registry.py` | planned (this cycle) |
| Arguments cannot change on the "yes" turn | `spine/contract.py` — the confirm tool's schema has no argument fields | `tests/spine/test_confirmation.py` | planned (this cycle) |
| A confirmation can't be created and satisfied on the same turn | `spine/confirm.py` — `transition()` | `tests/spine/test_confirmation.py` | planned (this cycle) |
| Only the session that raised a pending action can confirm it, and a channel message can never close it | `spine/router.py` — `resolve()` (SPN-14, SPN-16) | `tests/spine/test_confirmation.py` | planned (this cycle) |
| The deny floor applies on both the normal and the confirmed path | `spine/deny_floor.py` | `tests/spine/test_deny_floor.py` | planned (this cycle) |
| Untrusted output is wrapped by default, not opt-in | `spine/untrusted.py` | `tests/spine/test_untrusted_default.py` | planned (this cycle) |
| One approval produces exactly one execution | `spine/router.py` — the atomic claim | `tests/spine/test_outcomes.py` | planned (this cycle) |
| A crash near an external effect is recorded as unknown, never guessed or silently retried | `spine/router.py`, `spine/confirm.py` | `tests/spine/test_outcomes.py` | planned (this cycle) |
| The decision record is immutable | `storage/migrations/001_spine.sql` — triggers on the `decisions` and `tool_audit` tables | `tests/spine/test_structure.py` | planned (this cycle) |
| Only the router ever calls `Tool.execute` | `spine/router.py` | `tests/spine/test_structure.py` | planned (this cycle) |
| There are no hard-delete tools | `spine/registry.py`, `storage/` | `tests/spine/test_structure.py` | planned (this cycle) |
| The gate survives context compaction | `spine/confirm.py`, `storage/sqlite/spine_store.py` | `tests/spine/test_compaction.py` | planned (this cycle) |

## 5. Frontend

`packages/galaxy` is a standalone TypeScript package with no dependency on the backend — it can be dropped into any page that wants the orb. `apps/web` will consume it as a dependency once the web app exists; that hasn't started yet.
