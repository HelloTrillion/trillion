# Listening Cues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Short listening cues ("mm-hm", "mm", "yeah", "go on") in her voice, plus an orb nod, at real mid-turn pauses. Also fix the quiet `listening` window for watch mode and for silence that isn't wordless. See [docs/specs/2026-11-02-listening-cues.md](../specs/2026-11-02-listening-cues.md); the spec is the source of truth for rules and numbers.

**Branch:** `voice/stay-quiet` (continues that PR, which is not merged).

**Rules for every task:**
- Use TDD.
- Commit with `git commit -s`, ending the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run the full gate before each commit:
  - `uv run pytest -q -x --ignore=tests/spine`
  - `uv run pytest tests/spine -q`
  - ruff check and ruff format (your files only)
  - mypy
  - `scripts/containment_check.py`
  - for web tasks, the web typecheck and tests
- No em or en dashes in code comments.
- No real API calls.
- Never open `google_client.json`.
- Never play sound.
- Don't touch `.claude/preview/home` or ports 7777, 7778 and 7790.
- Don't push.

### Task 1 (core): the quiet window, fixed

- **Watch mode.** After a quiet `listening` turn, whichever path opens the next listening (the overlap watch through `_watch_settles` → `_follow_up_from`, or `_after_turn`) opens the quiet window: `QUIET_LISTEN_S` and the one nudge. The relevant code is in `session.py`, around 1149-1158, 1505-1513, 1767-1780 and 2283-2292. Clear `_quiet` only when the window has been opened or is no longer wanted.
- **Silence.** In the quiet window, "nothing" means no recognized words for `QUIET_LISTEN_S`, whatever the loudness meter heard (`voice/endpoint.py` `_spoke`). Implement it as a window-only rule; don't change ordinary turns.
- **The tool description** (`src/trillion/tools/quiet.py`): `listening` is only for an unfinished sentence. A finished one, even "I just don't know", gets an answer. Don't touch the prompt `.md` files.
- **Tests:**
  - a session in watch mode (Flux fake, voice in core, barge on) ending quiet `listening` → `mic.start` with `quiet: true`, then one nudge after 8 seconds of no words;
  - loud non-word frames don't block the nudge;
  - words start a turn.
- Commit: "Session: the quiet window opens in watch mode too, and only words hold it open".

### Task 2 (core): when a cue plays

- **Recognizer data.** `voice/types.py` `Heard` gains `confidence: float | None`. `providers/deepgram.py` `parse()` keeps `end_of_turn_confidence` on `Update`/`EagerEndOfTurn`/`EndOfTurn`, and stops dropping a text-less `Update` that carries a confidence (callers that need text still check). Fakes and tests follow.
- **`src/trillion/voice/cues.py` (new).** It holds `CUES` (with weights), `CUE_CHANCE`, `CUE_GAP_S`, `CUE_MAX`, `MIN_SPEECH_S`, `GO_ON_AFTER_S`, `PAUSE_MIN_S`, `PAUSE_MAX_S` and `MAX_CONFIDENCE`, plus a pure `CuePlan`. `CuePlan.consider(now_s, heard_s, last_word_end_s, confidence, eager_since_word, last_words) -> str | None` returns a cue or None, using an injected `random.Random`. It keeps the per-turn count, the last cue and the last time.
- **Tests:** every rule in the spec's "When a cue plays", using a seeded random.
- Commit: "Voice: when a listening cue may play".

### Task 3 (core): making, sending and owning cues

- **Making them.** A small `CueBank` makes the four cues per voice with `rt.tts.synthesize(text, voice)` and trims silence (reuse the loudness helpers in `api/speech.py`). It keeps PCM and levels in memory, keyed by voice. It is made when a session opens with cues possible and when the voice changes. A failure means no cues, and the log line contains no audio.
- **Session.**
  - Feed each mid-turn recognizer message to the listener's `CuePlan`. That needs a hook from `Listener._read` (`api/listening.py` ~419-478) to the session, like `on_turn`.
  - Only while a user turn is being taken: not a watch, not her reply, and only when cues are possible and on.
  - When it says a cue: send `cue.play` JSON plus the kind-2 binary frame (`<BII` 2, id, 0, then PCM), following `api/speech.py`'s frame helpers.
  - On `cue.started {id, in_ms}`, place the cue on a cue `Timeline` for that listener (`timeline.py`; begin at `listener.sent_s`). Make sure the listener has a judge (`listener.judge_by(self._judge_on(listener, cue_tl))`), so echoed cue words are hers.
- **Setting.** `CUES_PREF = "listen_cues"` (default True), following the `think_early` pattern exactly:
  - `runtime.py`;
  - `api/settings.py` `describe_listening` (add `cues` and `cues_possible`) and `PUT /api/settings/listening/cues`;
  - tests like `tests/api/test_server.py` ~961.
- **PRD.** Amend PER-11 with the narrow exception, and add a line to VOI-12 or VOI-9 naming the cues and this spec.
- **Tests:**
  - a Flux pause sends `cue.play` + frame;
  - an echoed "mm-hm" after `cue.started` is not in the user's turn;
  - no cues when off, with the system voice, with Whisper, with the "multi" choice, or during her reply;
  - the bank is made once per voice.
- Commit: "Voice: she says mm-hm while you talk".

### Task 4 (web): play cues, nod, and the switch

- **Cues.**
  - In `apps/web/src/voice.ts` (or a new `cues.ts`), add a cue player: its own `GainNode` (0.5) in the existing `AudioContext`, playing kind-2 frames.
  - It sends none of the `playback.*` messages and is not attached to the orb's playback analyser.
  - On play: `galaxy.flare(0.3)` and send `{"type":"cue.started","id":N,"in_ms":...}`, measured like `playback.started`.
  - Drop a cue if her reply audio is playing, or if its JSON never came.
- **The switch.** In `apps/web/src/listening.ts`: "Say 'mm-hm' while I talk", with small text "Short listening sounds in her voice while you think aloud." It is shown only when `cues_possible`, and it saves on change via `PUT /api/settings/listening/cues`, like the think-early checkbox.
- **Tests** (jsdom): a kind-2 frame routes to the cue player, not `voice.audio`; flare is called; `cue.started` is sent; the switch shows, hides and saves.
- Commit: "Web: play her listening cues and nod the orb".

### Task 5 (controller): review, live test, PR update

- A whole-branch review.
- The owner restarts the Mac app and talks.
- The controller reads the turn timings and the conversation.
- Update PR 148's description.
