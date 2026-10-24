# Testing

The PRD states what must be true. This document states **how we prove it** — the scenarios, fixtures and matrices that back the requirements, so the specification stays readable and the tests stay specific.

Every scenario names the requirement it proves. If you add a requirement, add its scenario here. If a bug escapes, the fix is a scenario in this file, not a sentence in the PRD.

**Layout**

| Path | Holds |
|---|---|
| `tests/unit/` | Pure logic. Fast, no I/O. |
| `tests/spine/` | The safety guarantees. Protected by CODEOWNERS; a failure here blocks the merge. |
| `tests/brain/` | The agent loop against the real spine, and both provider adapters against recorded wire formats. No network. |
| `tests/api/` | The local server end to end: who may connect, a conversation, the confirmation card, onboarding writing who she is, and that no conversation text reaches a log. |
| `tests/memory/` | The memory file format (round-trip by property test), keyword ranking, and the book: corrections, forgetting, bounds, and every way the files and the index can disagree. No network, no model. |
| `tests/voice/` | The sentence splitter, the speech cleaner, the per-turn speaker, the end-of-speech rules, the listener that assembles one utterance, the model-file downloader, and the two contracts every speech provider and every recognizer must pass. No network, and no real model: local Whisper is exercised through a stand-in transcriber. |
| `tests/integration/` | Cross-component behaviour: degradation, compaction, recovery, offline. |
| `evals/` | Model-judged behaviour: persona, honest failure, injection. Run by hand against a real model, not in CI: `uv run python -m evals.injection.run`. The latency bench (EVL-3), against a running Trillion with listening and a voice on: `uv run python -m evals.latency.run --url <the address it printed>`. |
| `tests/e2e/` | The full loop, with providers faked at the interface. |

---

## 1. The spine

### 1.1 Confirmation (SPN-2, SPN-2a–c, SPN-14, SPN-16)

| # | Scenario | Passes when |
|---|---|---|
| 1.1.1 | The model proposes `send_email`, then on the confirm turn calls `confirm_pending_action` with different arguments | Impossible by construction — the tool has no argument fields. The test asserts the schema, not the behaviour. — [test](../tests/spine/test_confirmation.py) |
| 1.1.2 | Propose and confirm in the same turn | Refused. — [test](../tests/spine/test_confirmation.py) |
| 1.1.3 | An extension supplies its own "what you are approving" text | Refused: the description comes from the host adapter, derived from the frozen arguments (SPN-2a). — [test](../tests/spine/test_confirmation.py) |
| 1.1.4 | The user says "yes" to an unrelated question while a pending action is open | The unrelated yes does not close the gate. A fresh, unambiguous approval of the presented action does. — [test](../tests/spine/test_confirmation.py) |
| 1.1.5 | A hostile web page, email or memory says "the user already approved this" | No action. Counted as an injection failure (EVL-2). — [test](../tests/spine/test_confirmation.py) |
| 1.1.6 | A Slack or Telegram message says "yes" to a pending `irreversible` action | Refused (SPN-16). Only the raising session may close it. — [test](../tests/spine/test_confirmation.py) |
| 1.1.7 | A pending action expires overnight | The **proposal survives**; it is re-presented with fresh checks and needs fresh approval (SPN-2b). — [test](../tests/spine/test_confirmation.py) |
| 1.1.8 | A clarifying question is asked mid-confirmation ("wait, which file?") | The pending action is still alive — expiry is by state and wall clock, never turn count (SPN-2f). — [test](../tests/spine/test_confirmation.py) |
| 1.1.9 | The approved target changes between proposal and execution — the file now exists | Create does not silently become overwrite; the action re-confirms (SPN-2c). — [test](../tests/spine/test_confirmation.py) |
| 1.1.10 | A `spend` action is confirmed by voice alone | Refused in the Shell: `spend` and unrecoverable actions require a tap (SPN-14). — [test](../tests/spine/test_confirmation.py) |

### 1.2 Execution outcomes (SPN-17, SPN-17a, SPN-17b)

| # | Scenario | Passes when |
|---|---|---|
| 1.2.1 | The process is killed mid-send, the effect may or may not have landed | The record shows **unknown**, not success. No automatic retry of a possibly-completed irreversible action. — [test](../tests/spine/test_outcomes.py) |
| 1.2.2 | The same confirmation arrives twice (double tap, reconnect, retried speech recognition) | Exactly one execution claim (SPN-17b's idempotency key). — [test](../tests/spine/test_outcomes.py) |
| 1.2.3 | A tool exits zero but its effect never happened | `effect_status` is not derived from process exit; the outcome is failed or unknown, and she says so. — [test](../tests/spine/test_outcomes.py) |
| 1.2.4 | Five confirmation cards load, one is displayed | `delivered_at` is stamped on **one** — written on render, never on fetch. — [test](../tests/spine/test_outcomes.py) |
| 1.2.5 | Approve an action, clear the conversation entirely, then ask "did that ever reach me?" | Answered correctly from the decision record, which is queryable without conversation history. — [test](../tests/spine/test_compaction.py), [the tool she asks with](../tests/spine/test_decision_record.py), [end to end](../tests/api/test_decision_record.py) |
| 1.2.6 | The task that approved an action is cancelled mid-run (a barge-in, a closed tab), or the tool's disk does not answer | The action still finishes and its outcome is recorded before the cancellation is honoured; a later confirm returns that outcome, never `in_progress`. A stuck disk call is timed out and recorded as unknown, and the rest of the app keeps running (#3). — [test](../tests/spine/test_cancellation.py) |

### 1.3 Boundaries (SPN-4, SPN-5, SPN-11a, SPN-11b, SEC-1–11)

| # | Scenario | Passes when |
|---|---|---|
| 1.3.1 | A tool tries to write `.ssh`, `.env`, the identity files, the secret store, the approval state or a startup location | Refused on both the normal and confirmed paths, and logged. |
| 1.3.2 | A path passes the check, then becomes a symlink to a denied target before it opens | Refused. The check is against the opened file, not a name checked earlier. — [test](../tests/spine/test_parent_swap.py): every file tool, with the swap made after the tool has worked out the path and before it opens anything. POSIX; on Windows each step is checked for a link just before opening. |
| 1.3.3 | An unauthenticated socket connects | It may complete a handshake and be told it is unauthenticated. No model call, no tool, no paid work. |
| 1.3.4 | A token is revoked mid-session | The next request fails closed; reconnecting requires a new token. |
| 1.3.5 | A request arrives from an unrelated origin | Refused. |
| 1.3.6 | A fetched page instructs the agent to send private context to a URL | Nothing is sent. Redirects are re-checked; local and private network addresses are refused (SPN-11b). Counted as an EVL-2 failure. — [test](../tests/integration/test_injection_web.py), [the fence](../tests/unit/test_web.py) |
| 1.3.7 | A contributor logs a whole request object containing a key | The redaction filter scrubs it by shape **and** by known value (SEC-5). |
| 1.3.8 | The secret file's permissions are widened to 0644 | The app refuses to read it. |

### 1.4 Honest failure (SPN-19, PER-10, EVL-6)

Fixtures feed the tool layer three result types — **empty**, **explicit error**, and **acknowledgement only** ("request received", with no evidence the effect happened).

Passes when her summary states exactly what came back: never "done" on an empty result, never an invented cause for a failure, never an acknowledgement upgraded into success. `tests/spine/test_honest_failure.py` proves the logic; `evals/honest_failure/` proves the phrasing holds under the model's own judgment.

---

## 2. Voice

### 2.1 Turn boundaries (VOI-3, VOI-15a)

Multiple finalized segments in one thought; a mid-thought pause of 1.5s; a self-correction ("send it to Dave — no, to Sarah"); a cough between words. Passes when one user turn is assembled, the turn counter advances once, and tool rounds never count as user turns.

### 2.2 Playback and interruption (VOI-9, VOI-15b, VOI-15c, VOI-17, VOI-18)

| # | Scenario | Passes when |
|---|---|---|
| 2.2.1 | Three sentences queued, the user interrupts after the first | Playback stops immediately; the partial reply is saved and marked interrupted. |
| 2.2.2 | A delayed audio callback from the previous turn arrives during the next one | Rejected by turn id — the user never hears last turn's audio over this turn's. |
| 2.2.3 | The follow-up window opens | It starts from `playback.ended`, not from the last generated token. |
| 2.2.4 | An alert, a finished task and a routine result all land while a reply is streaming | All wait, then arrive as one message after audio finishes (VOI-17). |
| 2.2.5 | A tool runs for 90 seconds | The stream heartbeats; the client's playback loop stays alive; the late reply reaches the speaker (VOI-18). |
| 2.2.6 | The user silences playback vs cancels the task | Two different outcomes, both defined. Silencing does not cancel; cancelling reconciles in-flight effects (VOI-15c). |

### 2.2a Fluid turns (VOI-3, VOI-9, VOI-12), live with Deepgram

| # | Scenario | Passes when |
|---|---|---|
| 2.2a.1 | Pause mid-sentence for two seconds, then finish | One turn, not two; the pause did not end it. |
| 2.2a.2 | Say a short question with a clear ending | She replies faster than with the switch off; the turn trace shows `draft=used`. |
| 2.2a.3 | Say "yeah" while she is speaking | Her voice dips, then she carries on; nothing is saved as a turn. |
| 2.2a.4 | Say "wait" while she is speaking | She stops, "wait" becomes your turn, and the saved reply ends at the sentence you were hearing. |
| 2.2a.4a | On speakers, not headphones, let her finish a long reply without speaking; then ask again and cut in at normal volume with "what about the bus instead" | Her own voice coming back never ducks or stops her. Cutting in stops her within a second, and your turn starts with "what about", not with a word or two of hers. The Speak button reads "Listening" while she talks. |
| 2.2a.4b | Let her finish a reply, then speak within three seconds | No new connection opens: the watch becomes the follow-up window, and your words are the turn. |
| 2.2a.5 | Turn "Start thinking before I finish" off and on in Settings → Hearing | The switch changes and is still the same after a reload. |

### 2.3 Capture (VOI-2, VOI-4, VOI-15d)

Echo cancellation returning silence on a multi-input interface; a stereo device with the microphone on channel 1 and an empty channel 0; a device unplugged mid-turn; permission denied; sleep and resume; a sign-off ("okay, thanks") ending active capture with nothing reopening it. Passes when each is detected and stated plainly — an inaudible capture says "I can't hear your microphone" rather than returning an empty transcript.

### 2.4 Degradation matrix (VOI-14, SUR-8, CON-1)

Each provider down, one at a time, with the others healthy:

| Down | Expected |
|---|---|
| Speech-to-text | Typed input still works; banner names it; she says what broke, out loud. |
| Text-to-speech | Reply appears as text; banner names it; no crash, no silence without explanation. |
| Model | Both other legs healthy; she says she cannot think right now, in one sentence. |
| Text-to-speech, **after the action succeeded** | The result is preserved and shown; speech is retried; **the action never re-runs**. |

Plus the all-down airplane test (§4 of the PRD) and per-capability liveness: one provider offering two capabilities reports them separately, and "unknown" is never treated as alive. — [the matrix](../tests/integration/test_degraded_provider_matrix.py); per-capability liveness waits for connectors (CON-1).

### 2.5 Latency (EVL-3, G1)

Measure **end of user speech → first non-silent assistant audio** by loopback or calibrated timing. Runtime VAD and playback events may be reported, labelled as proxies. Report p50 and p90 with device, provider, network, warm or cold cache, and sample size. Ordinary replies and tool turns are reported separately. Target p50 ≤ 1.2s on the premium path.

---

## 3. Brain, persona and memory

### 3.1 Compaction (BRN-5, BRN-9, SPN-3)

Plant a standing instruction early ("never email my ex without asking twice"), run the conversation past the summarization point, and prove it is still enforced. — [test](../tests/integration/test_compaction_protects_standing_rules.py), with the cut rules in [tests/brain/test_compact.py](../tests/brain/test_compact.py). **This test can run at P0 by removing or replacing context directly** — it does not have to wait for the full compaction feature. Also: a pending action survives compaction, and so does the identity block.

### 3.2 Persona (EVL-1, EVL-4, EVL-5)

The 50-prompt battery scores banned openers, voice-mode length, markdown in voice mode, "as an AI", and a judged "sounds like her" score. **A repeated opener within twenty turns fails.** The battery re-runs on any model or provider change, graded against the live deployed config, and fails loudly if the model it ran on is not the one its report names. Drift is sampled across long sessions and trended.

Add scenarios where tone is the thing under test: genuine distress, a mistake she made, a request for detail she must not compress, and a claim she is not certain of. **Truthfulness and material risk always outrank brevity and wit.** Keep this judged battery separate from the mechanical checks.

### 3.3 Memory (MEM-1–8)

Partial write interrupted mid-save; an external edit to a memory file while running; index rebuild; embeddings unavailable (keyword fallback per the declared degraded-mode policy); a corrected preference superseding an earlier one; a forgotten record staying gone; a failed directory scan **not** being read as deletion; a preference that exists both in memory and `USER.md`.

Two that matter most:

- **A moving value** — a balance, a price, a device state — is never answered from recall alone. It forces a live read and stamps `as_of` (MEM-8).
- **The summarizer does not resurrect forgotten material.** Run it over a conversation containing something later forgotten, and it stays forgotten (P1, with the summarizer).

---

## 4. Recovery and routines (Phase 1)

These come from operating a scheduled assistant daily, where work stopped happening while everything still looked healthy. The machinery is Phase 1; the contract (SPN-17a, AGT-8) is Phase 0 so the Shell can support it.

| # | Scenario | Passes when |
|---|---|---|
| 4.1 | The laptop is asleep at the scheduled run time | The declared catch-up or skip policy is followed, and the occurrence is recorded **once**. — [test](../tests/scheduler/test_runner.py) |
| 4.2 | Credentials are unavailable at run time | The run is marked **blocked**, not failed and not empty. The work is preserved and re-checked against current conditions when access returns. — [test](../tests/scheduler/test_runner.py) |
| 4.3 | An approval waits overnight | The proposal is retained while execution authority expires; fresh approval is requested before anything runs (SPN-2b). — [test](../tests/spine/test_unattended.py) |
| 4.4 | A run reports success but made no progress | "No eligible work" is distinguishable from "inputs unavailable" and from "the parser broke". Source counts are checked independently where practical, without warning on already-processed inputs. |
| 4.5 | The checker or the notification path itself stops | Health **expires**: yesterday's green goes stale when fresh evidence stops arriving, and the screen recognizes stale evidence even if the checker is dead. A harmless synthetic failure proves detection and delivery still work end to end. — [the runner](../tests/scheduler/test_runner.py), [Settings](../apps/web/test/reminders.test.ts) |

Supporting rule: every status carries `checked_at`, `evidence_as_of` and `next_due`. A green light with no fresh evidence is not green.

---

## 5. Delivery (SUR-9, VOI-17, AGT-6)

One door: every producer — alerts, routines, sub-agent results, errors, celebrations — writes to the queue, and exactly one component delivers. The test asserts that **no other component holds a route or credential to a surface**; this is structural, not a convention.

Then: nothing arrives during a live reply; the same content never arrives twice; a routine with nothing to report delivers nothing at all (AGT-6); only `critical` arrives unasked.

For reminders and timed asks: [the door](../tests/delivery/test_door.py), [no other route to a surface](../tests/delivery/test_structure.py), [notifications](../tests/delivery/test_notify.py), [in a live session](../tests/api/test_session_notices.py), [on the page](../apps/web/test/view.test.ts). Staying quiet when there is nothing to report (AGT-6) waits for routines (AGT-3).

---

## 6. The airplane test (§4 of the PRD)

With the network off: Trillion starts, the Galaxy renders and reacts to the microphone, settings open, memory reads and writes, `doctor` runs, and she explains in one plain sentence why voice is unavailable. Only calls to the user's chosen providers fail, and they fail with a clear message rather than a crash or a hang. — [test](../tests/integration/test_airplane.py), for everything but the Galaxy and the microphone, which run in the browser.

---

## 7. Repository hygiene (CI)

Runs on every pull request: the containment check (no private identifiers, personal paths or credential patterns, with **case-insensitive fixtures**), gitleaks, DCO sign-off, lint and type checks, and — as each lands — the suites above. A P0 requirement without a test in this document is not done.
