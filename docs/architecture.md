# Architecture

This is the map: one process, what each package does, how a tool call moves through the spine, and where each safety guarantee is enforced and tested. It assumes you've read [`docs/PRD.md`](PRD.md); it doesn't restate the requirements, only where they live in code.

## 1. One process

`trillion run` serves the UI and the API and runs the background workers, all in a single process on the user's machine. State lives in a SQLite database at `~/.trillion/trillion.db`; set `TRILLION_HOME` to move the whole data directory somewhere else. There is no second process, no hosted sidecar, and no service of ours in the loop — see the PRD's self-contained rule (§4) and process model (§8.5).

## 2. Module map

The `§8.1` tree from the PRD is the target layout. The packages marked "exists" have working code; everything else is a placeholder waiting for its track.

| Package | Status |
|---|---|
| `trillion/cli` | exists — `trillion run` starts the server and opens the app; `trillion setup` prepares the data folder and database, checks the machine, then starts it; `trillion doctor` checks and reports without changing anything (`--secrets`, `--offline`). `doctor --trace` (ONB-11) shows how long recent turns took, from `turn_timings`, and whether the running server is the build on disk (`running.json` in the data folder). `doctor --egress` is not built yet; see `docs/specs/2026-10-13-setup-and-doctor.md` |
| `trillion/spine` | exists — the router, the confirmation state machine, the deny floor, untrusted wrapping, the loop guard; see `docs/specs/2026-09-25-foundation-and-spine-design.md` |
| `trillion/storage` | exists — SQLite, checksummed migrations, the spine store, conversations and usage. `conversations.title`, its source (`words`, `model`, `user`) and the soft-delete time are migration 010 `turn_timings.draft` (migration 009) records whether a turn used an early draft (VOI-12) |
| `trillion/tools` | exists — the file tools `file_list`, `file_read`, `file_create`, `file_append`, `file_edit`, `file_overwrite`, `describe_self`, `memory_write`, `memory_recall`, `memory_forget`, `rule_keep`, `rule_drop`, `decision_record`, which reads the decision record back (SPN-17), and `web_fetch`, fenced to addresses the user gave or links on a page already read and to public addresses only (SPN-11b; see `docs/specs/2026-10-20-web-fetch.md`). Still to build from CON-3: `file_search`, `web_fetch`, `weather` |
| `trillion/api` | exists — the loopback server, the session token, and the text half of the realtime protocol; see `docs/specs/2026-10-07-first-conversation-design.md`. `conversations.py` is the thread list's API (SUR-1): `GET /api/conversations` (newest first, `q` search, a `before`/`before_id` cursor), `PATCH /{id}` rename, `DELETE /{id}` soft delete and `POST /{id}/restore` undo; a session names itself after her first reply and says so with `conversation.title`. `draft.py` holds her early first answer while the recognizer thinks the user is probably finished: nothing is shown, saved or spoken until the turn is confirmed, and it is dropped if they carry on. Fluid turns: `docs/specs/2026-10-24-fluid-turns.md` |
| `trillion/brain` | exists — the provider interface, the Anthropic and OpenAI-compatible adapters, the tool loop, compaction (`compact.py`), the user's standing rules (`standing.py`) and `title.py`, which names a conversation from its first exchange with no tools and the exchange wrapped as untrusted text (a failure keeps the first-words title; a title the user typed is never replaced); see `docs/specs/2026-10-15-compaction.md`. Spend ceilings are not built yet |
| `trillion/persona` | exists — loads the soul and the identity files, assembles the prompt, and holds the privileged setup path (`persona/setup.py`) that is the only writer of `USER.md` and `IDENTITY.md`; see `docs/specs/2026-10-13-onboarding.md`. Overlays beyond text and directness, and the tonal checkpoint, are not built yet |
| `trillion/voice` | exists — speaking: the provider interface, the catalog, the system voice and ElevenLabs, the sentence splitter and the speech cleaner (`docs/specs/2026-10-07-voice-providers-and-hardening.md`). Listening: the recognizer interface and catalog, local Whisper and Deepgram, the end-of-speech rules, verified model downloads (`docs/specs/2026-10-09-listening.md`). Hearing that judges turns: Deepgram Flux, whose end-of-turn decides. `overlap.py` decides what to do when the user talks over her: while she speaks, the microphone streams to the hearing, and `whose.py` judges each word hers (her echo) or the user's by when it was said, what she was saying then (`timeline.py`, her playback on the recognizer's clock) and how loud the microphone was; words of the user's duck her voice, then she carries on for an acknowledgement or stops and takes their words as the turn (`confirm`, `decide`); see `docs/specs/2026-10-24-echo-timing.md`. Local Whisper and OpenAI hearing keep stop-at-once. Capture guards, sign-off detection and the wake word are not built yet |
| `trillion/memory` | exists — memories as Markdown files with an SQLite index, keyword recall, corrections and forgetting that keep history, and the reconcile step that makes the files win; see `docs/specs/2026-10-10-memory.md`. Embedding search (MEM-2), the session-close summarizer (MEM-4) and the memory browser (MEM-6) are not built yet |
| `trillion/agents` | placeholder |
| `trillion/scheduler` | exists — reminders and timed asks (AGT-4, AGT-9): `when.py` turns the user's wall-clock times, in their own zone, into instants across daylight saving; `runner.py` is the loop inside the server (every 15 s, started with it and stopped with it) that enqueues one run per due time, claims runs atomically, and reclaims ones a crash or a timeout left: a reminder is tried once more, an interrupted timed ask is reported failed and never run again; `ask.py` runs a timed ask through the tool loop with only tools that never ask (`ToolRegistry.unattended()`, `via="schedule"`, which the router refuses every card). Tables in migration 008, read through `storage/sqlite/schedule_store.py`. Tools in `tools/schedule.py`; Settings → Reminders uses `/api/schedules`; `trillion doctor` reports them. Nothing runs while Trillion is not running. See `docs/specs/2026-10-24-reminders.md` |
| `trillion/delivery` | exists — the one door (SUR-9): `door.py` delivers each notice to the most recently active page when it is free (VOI-17: no turn running, no microphone open, nothing still being heard), or shows a system notification once (`notify.py`) and holds it for the next page. The session saves notices with her words as her messages and speaks them under a turn number of their own (`OUT_OF_TURN` in `api/runtime.py`), and stops that speech as soon as the user types, talks or taps. Nothing else imports the notifier, and the scheduler has no route to a surface |
| `trillion/secrets` | exists — the OS keychain behind one interface, and the log filter that scrubs keys. `trillion doctor --secrets` shows which store is in use and whether each saved key works, without printing one (SEC-9). The encrypted-file fallback for machines with no keychain (SEC-2) is not built yet |
| `trillion/connectors` | exists — `connectors/mcp`: MCP servers the user connects (stdio or HTTP), kept in `mcp.json`, each connected and disconnected while Trillion runs, each tool registered as a spine tool that asks first unless the user said otherwise (CON-5). Keys come from the keychain by name; remote servers sign in with OAuth and PKCE through the browser (`signin.py`). `catalog.py` lists the featured services, `paste.py` reads a pasted address, command or config. Settings → Connected tools uses `/api/mcp`; `trillion doctor` checks the file without starting anything. `connectors/google`: Gmail, Calendar and Drive as spine tools, OAuth with PKCE (CON-4), `/api/google`. `connectors/slack`: Slack tools with the user token, `/api/slack` |
| `trillion/skills` | exists — `SKILL.md` skills (CON-6): `parse.py` reads one skill folder within limits and never follows a link, `fingerprint.py` hashes its files, `library.py` finds the curated set (`skills/curated`) and the user's folders and keeps their choices in `skills.json`. The tools `skill_open` and `skill_file` (`tools/skills.py`) read only skills that are on and unchanged; her prompt lists them; Settings → Skills uses `/api/skills`; `trillion doctor` reports them |
| `trillion/channels` | exists — talking to her from Slack (Socket Mode) and Telegram (long polling), answers only (SUR-5): an allow-list that fails closed, @mention in groups, a model call with no tools (SPN-16), each chat kept as its own conversation |

## 3. The life of a turn

A typed message arrives on the WebSocket as `turn.text`. The session saves it, advances the turn counter, and starts the agent loop (`brain/loop.py`). The loop streams the model's reply to the page as it arrives. When the model asks for tools, the loop hands each call to `Router.dispatch` and gives the model back exactly what the router recorded; it never calls a tool itself. A tap on a confirmation card goes straight from the session to `Router.resolve`, and the session then tells the model what the tap did. Every message is saved as it completes, so an interrupt or a crash leaves a history that is still true.

## 4. The life of a tool call

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

## 5. Where each guarantee lives

The status column keeps this table honest: a row says "enforced" only when the test beside it proves it. The `tests/spine/` suite runs in CI with 95% branch coverage on `trillion.spine` required.

| Guarantee | Module | Test | Status |
|---|---|---|---|
| A tool must declare a risk level; there is no default | `spine/contract.py`, `spine/registry.py` | `tests/unit/test_contract.py`, `tests/unit/test_registry.py` | enforced |
| Arguments cannot change on the "yes" turn | `spine/confirm.py` — the confirm tool's schema has no argument fields; `spine/router.py` refuses any extra field | `tests/spine/test_confirmation.py` | enforced |
| A confirmation can't be created and satisfied on the same turn | `spine/confirm.py` — `decide()` | `tests/spine/test_confirmation.py`, `tests/spine/test_compaction.py` | enforced |
| Only the session that raised a pending action can confirm it, and a channel message can never close it | `spine/confirm.py` — `decide()`, applied by `spine/router.py` — `resolve()` (SPN-14, SPN-16) | `tests/spine/test_confirmation.py` | enforced |
| The deny floor applies on both the normal and the confirmed path | `spine/deny_floor.py`, called by `spine/router.py` on both paths | `tests/spine/test_deny_floor.py`, `tests/spine/test_file_tools.py` | enforced |
| Untrusted output is wrapped by default, not opt-in | `spine/untrusted.py`, `spine/neutralize.py` | `tests/spine/test_untrusted_default.py` | enforced |
| One approval produces exactly one execution | `spine/router.py` — the atomic claim; `storage/sqlite/spine_store.py` — compare-and-set | `tests/spine/test_outcomes.py`, `tests/spine/test_router_edges.py` | enforced |
| A crash near an external effect is recorded as unknown, never guessed or silently retried | `spine/router.py` — `recover()`, `final_status()` | `tests/spine/test_outcomes.py`, `tests/spine/test_honest_failure.py` | enforced |
| The decision record is immutable | `storage/migrations/001_spine.sql` — triggers on the `decisions`, `decision_deliveries` and `tool_audit` tables | `tests/spine/test_structure.py` | enforced |
| Only the router ever calls `Tool.execute` | `spine/router.py` | `tests/spine/test_structure.py` | enforced |
| There are no hard-delete tools | `spine/registry.py`, `storage/` | `tests/spine/test_structure.py` | enforced |
| A key entered in the app goes to the OS keychain and nowhere else: not the database, a file, a log or any response | `api/settings.py`, `secrets/store.py`, `secrets/redact.py` | `tests/api/test_server.py`, `tests/unit/test_secrets.py` | enforced |
| A key is sent only over HTTPS or to this machine | `brain/config.py` — `endpoint()` | `tests/unit/test_brain_config.py` | enforced |
| Conversation history is only appended to, so the model's signed reasoning stays valid, and a reply is never changed after the model has been sent it; the one exception is trimming her last spoken reply to what was heard, before anything else is sent (`SqliteConversationStore.amend_tail`); a trimmed reply loses the provider's raw turn, so the next request misses the prompt cache once | `api/session.py`, `brain/providers/anthropic.py` | `tests/api/test_server.py`, `tests/brain/test_providers.py`, `tests/api/test_heard.py` | enforced |
| The model cannot confirm in the turn it proposed, whatever the user taps meanwhile | `api/session.py` — the turn is fixed when the model's turn starts | `tests/api/test_session.py` | enforced |
| A tool call and its result are saved together, whatever interrupts | `brain/loop.py` — `_finish()` | `tests/brain/test_loop.py` | enforced |
| One conversation has one live session | `api/session.py`, `api/runtime.py` | `tests/api/test_session.py` | enforced |
| Every speech provider behaves the same to the core | `voice/catalog.py`, `voice/types.py` | `tests/voice/test_tts_contract.py` | enforced |
| A shipped migration never changes | `storage/migrations/` | `tests/unit/test_migrate.py` | enforced |
| An unauthenticated client gets the static UI shell and a refusal, nothing else | `api/app.py` | `tests/api/test_server.py` | enforced |
| Conversation text never reaches a log | `api/session.py`, `brain/loop.py`, `brain/sse.py` | `tests/api/test_server.py`, `tests/brain/test_providers.py` | enforced |
| The gate survives context compaction | `spine/confirm.py`, `storage/sqlite/spine_store.py` | `tests/spine/test_compaction.py`, `tests/integration/test_compaction_protects_standing_rules.py` | enforced |
| A standing rule the user gave survives compaction (BRN-5) | `brain/standing.py`, `persona/prompt.py` — rules are in the system block, never in the history | `tests/integration/test_compaction_protects_standing_rules.py` | enforced |

## 6. Frontend

`packages/galaxy` is a standalone TypeScript package with no dependency on the backend — it can be dropped into any page that wants the orb.

`apps/web` is the app you talk to. `src/view.ts` turns the messages the core sends into what the screen shows, as a pure function with its own tests; `src/main.ts` owns the socket and the DOM. It writes text with `textContent` only, so nothing the model or a tool produced is ever parsed as markup. The core serves the built app; in development, run Vite beside it with `trillion run --dev`.

The screen is a left sidebar beside the stage, and a workspace that opens over the stage:

| Module | Does |
|---|---|
| `layout.ts` | Pure geometry: the sidebar's width limits, the phone sheet's three rests, and where the orb sits (`orbRect`) given the sidebar, the sheet and the workspace |
| `sidebar.ts` | The sidebar: dragged wider, hidden (⌘\), remembered per device. On a phone it is a bottom sheet dragged between collapsed, half and full |
| `threads.ts` | The thread list (a listbox: arrows, Enter, Escape), search, rename, delete with undo, and the one Escape order (`escapeAction`: stop her, close the list, close the workspace) |
| `workspace.ts` | The workspace over the stage; Settings opens there (`panel.ts` draws into it as a `Surface`). Focus moves in on open and back on close; the orb docks small and keeps every state and reaction |
| `captions.ts` | Her words under the orb while the sidebar is hidden |
| `announce.ts` | A polite live region outside the sidebar that says what a hidden (inert) sidebar would have |

The orb is placed by Galaxy's `setStage(rect, { animate })`, a rectangle in CSS pixels (`defaultRect` is where it sits without one); the page owns where, Galaxy owns the glide. The orb's state reaches screen readers through the sidebar's status line, or `announce.ts` while the sidebar is hidden.
