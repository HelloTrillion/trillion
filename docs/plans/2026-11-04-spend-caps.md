# Spend Caps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A monthly soft and hard cap per provider (models, voice, hearing), enforced by one spend gate that every paid call goes through, plus a rough cost shown wherever a key is entered. See [docs/specs/2026-11-04-spend-caps.md](../specs/2026-11-04-spend-caps.md). The spec is the source of truth for rules, numbers and wording, and holds the final decisions made during the build.

**Architecture:**
- A new package, `src/trillion/spend/` (prices, caller, ledger, gate, metered wrappers).
- The runtime hands out providers only through the metered wrappers, which check the gate before a call and record priced usage after it.
- A context variable says whether a call is `conversation` or `background`.

**Tech stack:** Python 3.13 (asyncio, sqlite via the existing store), TypeScript web app (vitest, jsdom).

**Branch:** `spend/caps` (the spec is already committed there).

**Rules for every task:**
- TDD.
- `git commit -s`; each message ends with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Gate before each commit:
  - `uv run pytest -q -x --ignore=tests/spine`
  - `uv run pytest tests/spine -q`
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy`
  - `uv run python scripts/containment_check.py`
  - web tasks also: `pnpm typecheck`, `pnpm test`, and `python3 scripts/check_raw_hex.py`
- No em or en dashes in code comments.
- No real API calls; tests use fakes.
- Never open `src/trillion/connectors/google/google_client.json`.
- Never touch the macOS keychain: tests use `MemoryStore`; set `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` for anything that runs the app.
- Never play sound.
- Don't touch `.claude/preview/home` or ports 7777, 7778 and 7790.
- Don't push.

### Task 1 (core): prices

**Files:**
- Create: `src/trillion/spend/__init__.py`, `src/trillion/spend/prices.py`
- Modify: `src/trillion/brain/providers/anthropic.py` (~104-130 `_PRICES`, `_LONG_PROMPT_PRICES`, ~300 `pricing()`), `src/trillion/brain/providers/openai_compat.py` (request and read OpenRouter's reported cost)
- Test: `tests/spend/__init__.py`, `tests/spend/test_prices.py`

- [ ] **Look up current rates** on each provider's pricing page:
  - the OpenAI and DeepSeek models the app offers in Settings (grep the model lists in `src/trillion/brain/config.py` and `apps/web/src/settings.ts`);
  - ElevenLabs pay-as-you-go per 1,000 characters for the model in `voice/providers/elevenlabs.py`;
  - Deepgram Flux per minute.

  Record each with its source URL and today's date in the table. If a page can't be reached, leave that model out (it becomes "unpriced") rather than guessing.
- [ ] **Write `prices.py`.**
  - `Rate` is a frozen dataclass: `unit` (`"tokens" | "chars" | "seconds"`); `input`, `output`, `cache_read`, `cache_write` per million tokens; or `per_unit` per thousand characters or per minute; plus `checked` (date) and `source` (URL).
  - `TABLE: dict[tuple[str, str], Rate]` is keyed by `(provider_id, model)`; a model of `"*"` means any model of that provider (used for voice and hearing).
  - Move the Anthropic rates here unchanged, long-prompt rates included. `AnthropicProvider.pricing()` then reads them from here; keep its behaviour identical, with a test asserting the old numbers.
  - `provider_id(kind, base_url)` maps a model kind to an id: `anthropic`, `openai`, `local`, or `custom:<host>`, using the host of the checked endpoint (`brain/config.py` `endpoint`). Voice and hearing map to `elevenlabs`, `deepgram` and `openai-voice:<host>`; the system voice and Whisper return None, meaning never capped.
  - `price(provider_id, model, usage, user_prices) -> float | None`:
    - a user price wins;
    - then a table entry;
    - `local` is 0.0;
    - otherwise None.
  - `user_prices` is read from the preference `spend_prices` (`{provider_id: {model: Rate-like dict}}`).
- [ ] **OpenRouter.** When the base URL host is `openrouter.ai`, send `"usage": {"include": true}` and read `usage.cost` from the final chunk. Carry it on the stream's usage event so `price()` uses it ahead of the table.
- [ ] **Tests:**
  - each table entry, including the Anthropic long-prompt rates;
  - a user price wins;
  - OpenRouter's reported cost wins;
  - an unknown model gives None and a local model gives 0.0;
  - characters and seconds are priced from `per_unit`;
  - `provider_id` for each kind and voice.
- [ ] **Commit:** "Spend: one price table, with OpenRouter's own cost and your own prices".

### Task 2 (core): the usage row and the ledger

**Files:**
- Create: `src/trillion/storage/migrations/017_usage_spend.sql`, `src/trillion/spend/ledger.py`
- Modify: `src/trillion/storage/sqlite/conversation_store.py` (`record_usage` ~479, `usage_since`); the migration lists and counts in tests (grep `016_decision_events` in tests/ for every list to extend; `tests/unit/test_doctor.py` counts)
- Test: `tests/spend/test_ledger.py`, `tests/storage/test_usage_spend.py`

- [ ] **Migration 017:**
  - `ALTER TABLE usage ADD COLUMN provider_id TEXT`;
  - `ADD COLUMN caller TEXT NOT NULL DEFAULT 'conversation' CHECK (caller IN ('conversation','background'))`;
  - `ADD COLUMN units TEXT NOT NULL DEFAULT 'tokens' CHECK (units IN ('tokens','chars','seconds'))`;
  - then `UPDATE usage SET provider_id = provider WHERE provider_id IS NULL`.
  - Old `provider` values are `anthropic`, `openai`, `local` or the custom provider's name: check `OpenAICompatProvider.name` and map what you can.
  - Add an index on `(owner_id, provider_id, created_at)`.
- [ ] **`record_usage` gains `provider_id`, `caller` and `units`,** all keyword arguments with today's defaults, so every existing caller still works until Task 4 moves recording.
- [ ] **`ledger.py`:**
  - `Ledger(store, clock, tz)` keeps, per provider id, this month's spend: dollars over priced rows; tokens, characters or seconds over unpriced rows; and a split by caller.
  - It loads once from `usage` (from midnight on the 1st in the user's timezone, as `api/usage.py` computes it: reuse that function, don't copy it).
  - `add(row)` updates it in memory, and a new month resets it.
  - `spent(provider_id) -> Spent(dollars, units, unit, by_caller)`.
- [ ] **Tests:**
  - the migration upgrades a database holding old rows;
  - a row just before the 1st (user's timezone) is excluded, and one just after is counted;
  - a NULL cost counts units, not $0;
  - conversation and background are counted apart;
  - the month rolls over.
- [ ] **Commit:** "Spend: usage rows say which provider and who spent it, and a ledger counts the month".

### Task 3 (core): who is calling, caps and the gate

**Files:**
- Create: `src/trillion/spend/caller.py`, `src/trillion/spend/gate.py`
- Modify: `src/trillion/api/runtime.py` (build the gate; preferences `spend_caps`)
- Test: `tests/spend/test_gate.py`, `tests/spend/test_caller.py`

- [ ] **`caller.py`:**
  - A `ContextVar[Call | None]` where `Call = (caller: Literal["conversation","background"], conversation_id: str | None)`.
  - `as_caller(caller, conversation_id)` is a context manager.
  - `current()` returns `background`, None when unset.
- [ ] **`gate.py`:**
  - `Caps(soft, hard, warn_pct)`, where None means no cap. `DEFAULT = Caps(20.0, 50.0, 80)`.
  - Caps come from the preference `spend_caps` (`{provider_id: {soft, hard, warn_pct}}`). For an unpriced model the cap values are read as units (tokens or characters).
  - `Gate(ledger, caps)` has `state(provider_id) -> "ok" | "warned" | "over_soft" | "capped"`, at the spec's thresholds.
  - `check(provider_id)` raises `SpendCapped(provider_id)` when capped, unless a `raised` override for this month says otherwise (raising simply writes a new hard cap, so no override is needed).
  - `local`, and a provider id of None, are always `ok`.
  - Thresholds compare dollars when the provider is priced, else units.
  - `SpendCapped` subclasses the provider error type each wrapper raises (`ProviderError` for models, `VoiceError` for voice and hearing), with kind `"capped"`. Check `brain/types.py` and `voice/types.py` for the right base classes and error-kind literals, and add `"capped"` where those literals are defined.
- [ ] **Tests:**
  - each state at and around its threshold;
  - priced versus unit caps;
  - removed caps mean never capped;
  - local and None are never capped;
  - an unset caller is background.
- [ ] **Commit:** "Spend: caps per provider, and a gate that says where each one stands".

### Task 4 (core): metered providers, and every call goes through them

**Files:**
- Create: `src/trillion/spend/metered.py`
- Modify:
  - `src/trillion/api/runtime.py`: wrap wherever `provider`, `tts` and `stt` are built or replaced, at `open_runtime` ~956-962, `build_speech` ~179, `build_listening` ~207 and the swaps ~815/825. Grep for every assignment to `rt.provider`, `.tts`, `.stt`.
  - Remove the now-duplicate recording at: `runtime.py` `record_refresh_usage` ~624 and `record_attention_usage` ~683; `session.py` `_record_usage` ~2942 and its callers (~2553, 2835, 2940, 3246); `scheduler/ask.py` ~360; `channels/answer.py` ~94.
  - Set the caller at the top of each caller: `as_caller("conversation", conversation_id)` in the session's turn, draft, compaction and day-board paths; `as_caller("background", ...)` in `scheduler/ask.py`, `attention/mail.py`, `attention/words.py`, `channels/answer.py`, `brain/title.py`, `workspace/fill.py` (background when not run from a session).
- Test: `tests/spend/test_metered.py`, `tests/spend/test_structure.py`

- [ ] **`MeteredModel(inner, gate, ledger, store, prices)`** has the same interface as `ModelProvider` (delegate `name`, `model`, `pricing()` and the rest).
  - `stream(...)` calls `gate.check(provider_id)` first.
  - It yields the inner events unchanged. On the usage event at the end it records one row (`provider_id`, caller and conversation from `caller.current()`, cost from `prices.price`), and the ledger adds it.
  - If the stream is cancelled after usage was reported, it still records. If it was cancelled before, it records nothing, matching today.
- [ ] **`MeteredVoice`:** `synthesize(text, voice)` checks before each sentence, then records `chars = len(text)` when the sentence's audio is complete.
- [ ] **`MeteredHearing`:** `open(choice)` checks, and the returned stream counts seconds of PCM sent (`bytes / (rate * 2)`), recording on close. A stream that crosses the cap is not cut; the next `open` is refused.
- [ ] **Structural test:**
  - every provider the runtime exposes (`rt.provider`, `rt.tts`, `rt.stt`, and any provider handed to drafts, routines, watchers, channels, titles, board fill) is a Metered instance;
  - an AST scan of `src/trillion` finds no call to `.stream(`, `.synthesize(` or `.open(choice` on a provider outside `spend/metered.py` and the providers' own modules (allow-list them explicitly);
  - no `record_usage(` call remains outside `spend/metered.py`.
- [ ] **Tests:**
  - one row per model call, with the right caller and conversation;
  - no double counting (compare the row count before and after a session turn with a fake provider);
  - a capped provider refuses before calling the inner provider (the fake counts calls);
  - a call in flight finishes;
  - voice is counted in characters and hearing in seconds.
- [ ] **Commit:** "Spend: every paid call goes through the spend gate".

### Task 5 (core): your conversation at the caps

**Files:**
- Modify: `src/trillion/api/session.py`, `src/trillion/persona/prompts/degraded.md` (or wherever the one-leg-down lines live: grep `one leg down`), `src/trillion/api/app.py` or a new `src/trillion/api/spend.py` (`POST /api/spend/choice`)
- Test: `tests/api/test_spend_session.py`

- [ ] **Hard cap, her model.**
  - `SpendCapped` raised at the start of a turn is handled like a model leg down (`docs/specs/2026-10-20-one-leg-down.md`), with its own app line, for example "I've reached the $50 cap for Anthropic this month, so I've stopped before spending more." It is spoken if voice is up, and shown in the banner (`error` with `code: "capped"`, `provider_id`, `cap`).
  - Then a `spend.card` message: `{id, provider_id, cap, options: [...]}`. The options are `raise` (suggested next step: the hard cap rounded up to the next $25), `switch:<model>` for each configured model on a provider whose state is not capped (local first, from `rt.model_choices` or equivalent: grep how Settings lists configured models), and `stop`.
  - A reply already streaming finishes: the gate only checks at call start, and a tool loop's later rounds are new calls. Make sure a capped refusal mid-loop ends the turn with the text so far saved, not an error that drops it.
- [ ] **Hard cap, her voice:** the sentence in progress finishes. Later sentences raise `SpendCapped`, so the turn carries on in text, and the card offers `raise`, `switch:system`, `text`.
- [ ] **Hard cap, hearing:** the current turn completes. The next `open` is refused, the mic does not reopen, and the card offers `raise`, `switch:whisper` (only if Whisper is installed: `voice/models.py`) and `type`.
- [ ] **`POST /api/spend/choice {id, choice, amount?}`** is accepted only from the session the card was sent to, and only by a tap (the same rule as approvals, SPN-14).
  - `raise` writes the new hard cap (and soft, if it is now above it).
  - `switch` writes a temporary preference, `spend_switch` (`{provider_id: {to, until_ms}}`, until midnight on the 1st), which the runtime applies when it picks the model, voice or hearing. The banner shows "Using X until Dec 1".
  - `stop` keeps the card reachable from the banner.
  - On the 1st, the switch lapses, and she says once (host note, PER-11) that she is back on the usual model.
- [ ] **Warning:** when a provider first reaches `warned` in a month, one notice through the one door (SUR-9), naming the provider, the amount so far and the soft cap. Once per provider per month, stored in preferences.
- [ ] **Soft cap:** the first turn after a provider reaches `over_soft` carries a host note saying the soft cap was passed and asking her to mention it briefly in her own words. Once per provider per month (stored in preferences).
- [ ] **Tests**, all through a real `Session` with fake providers:
  - every bullet above;
  - a card choice from another session, or not by a tap, is refused;
  - the switch survives a restart and lapses on the 1st (inject the clock).
- [ ] **Commit:** "Spend: at the hard cap she finishes, says so, and lets you choose".

### Task 6 (core): background work at the caps

**Files:**
- Modify: `src/trillion/scheduler/` (run outcome: grep where routine and timed-ask runs record `succeeded`/`failed`), `src/trillion/attention/` (watcher checks), `src/trillion/channels/answer.py`, `src/trillion/brain/title.py`, `src/trillion/delivery/` (the one door, for the notice)
- Test: `tests/spend/test_background.py`

- [ ] **A routine or timed ask refused with `SpendCapped`** records its run as `skipped`, reason `capped`. Add the status and reason where run outcomes are defined, with a migration only if the column has a CHECK that needs it. It is not retried that month.
- [ ] **A watcher refused** keeps its cursor (its "seen" mark is not advanced), so it reads the same mail again after the cap lifts.
- [ ] **Titles and channel replies** refused are dropped quietly. A channel reply is not sent, and its conversation shows nothing new.
- [ ] **One notice per provider per month** when background work first pauses, through the one door (SUR-9), for example "Routines and the inbox watcher are paused: Anthropic is at its $50 cap." Compose it the way other notices are composed (look at `delivery/` and `attention/words.py`). If composing would itself need the capped model, use the app's fixed line, as at the hard cap.
- [ ] **`gate.paused(provider_id)`** lists the paused kinds, for the API.
- [ ] **Tests:**
  - each kind is refused and recorded;
  - the watcher re-reads after the cap is raised;
  - one notice only;
  - everything resumes on raise and on the 1st.
- [ ] **Commit:** "Spend: background work pauses at the hard cap, and says so once".

### Task 7 (core): API, defaults and the one-time notice

**Files:**
- Modify: `src/trillion/api/usage.py`, `src/trillion/api/settings.py`, `src/trillion/api/runtime.py` (first start with this change)
- Test: `tests/api/test_usage.py` (extend), `tests/api/test_spend_settings.py`

- [ ] **`GET /api/usage`** adds `providers: [{provider_id, spent, unit, soft, hard, warn_pct, state, split: {conversation, background}, paused: [...], switched: {to, until_ms} | null, estimate: "…", pricing_url}]`, keeping today's fields.
  - The `estimate` line comes from `prices.py` plus a stated assumption: models assume 40 short turns an hour at 3,000 input and 150 output tokens per turn, voice 40 sentences of 80 characters, and hearing 60 minutes.
  - The text says the assumption, for example "Roughly $0.40 an hour of talk on Sonnet (about 40 short turns), plus routines."
- [ ] **Settings endpoints:**
  - `PUT /api/settings/spend/{provider_id}` with `{soft, hard, warn_pct}`; null removes a cap. Validate that hard ≥ soft, values are ≥ 0 and warn_pct is 1–100.
  - `PUT /api/settings/spend/prices`.
  - Both go behind the token and origin checks (copy the pattern of `PUT /api/settings/listening/early`).
- [ ] **Defaults:**
  - when a key is saved for a provider without caps, save `DEFAULT` for it;
  - on the first start with this change (a preference `spend_seeded`), seed `DEFAULT` for every set-up provider, and queue a one-time host note for the next conversation saying caps are on, what they are, and where to change them (PER-11).
- [ ] **Tests:**
  - the new fields for priced, unpriced, local and switched providers;
  - validation, and the token and origin checks;
  - seeding happens once, and the note comes once.
- [ ] **Commit:** "Spend: caps and costs in the API, defaults for new keys and existing installs".

### Task 8 (web): pill, Settings, key steps, the spend card

**Files:**
- Modify: `apps/web/src/usage.ts`, `apps/web/src/settings.ts` (model key step), `apps/web/src/speech.ts` and `apps/web/src/listening.ts` (key steps), `apps/web/src/onboarding.ts` (model step), `apps/web/src/view.ts` (banner and switch line), `apps/web/src/main.ts` (wiring), `apps/web/src/app.css` (tokens only, no raw hex)
- Create: `apps/web/src/spend.ts` (the spend card)
- Test: `apps/web/test/usage.test.ts` (extend), `apps/web/test/spend.test.ts`, `apps/web/test/onboarding.test.ts` (extend)

- [ ] **The pill** gets the `warned` and `capped` classes (amber and red from design tokens), and a tooltip and accessible name naming the provider and cap.
- [ ] **Settings → Usage** has one block per provider: the cost line; the month so far, split; soft, hard and warn% fields saved via the PUT; the pricing link; a price field (unpriced models) or rate override (voice and hearing); paused work; a switch, with an "End now" control.
- [ ] **Key steps** (onboarding's model step and Settings → Model, Voice and Hearing) show the provider's cost line, pricing link and the two caps prefilled with the defaults. Saving the key saves the caps. Local choices say "Runs on this computer: no cost, no cap."
- [ ] **The spend card:**
  - rendered from `spend.card`, with a field for raise and buttons for each option;
  - it sends `POST /api/spend/choice` on a tap;
  - it stays reachable from the banner after Stop;
  - the banner shows the switch line while it lasts.
- [ ] **Tests:**
  - pill states;
  - each Settings field saves and validates;
  - the key steps show the cost and caps;
  - card options render and post;
  - Stop keeps it reachable.
- [ ] **Commit:** "Web: spend caps in the pill, Settings, key steps and a card at the cap".

### Task 9 (docs)

- [ ] **`docs/PRD.md`:**
  - BRN-7, BRN-8, ONB-9 and ONB-10 get "Exists: [spend caps](specs/2026-11-04-spend-caps.md)".
  - BRN-8's background clause changes to "pauses and says so", with one line on why (BRN-11).
- [ ] **`docs/platforms.md`:** add the spend row.
- [ ] **`docs/specs/2026-10-22-api-usage.md`** "Not covered": point voice usage and OpenAI-compatible prices at the new spec.
- [ ] **`docs/specs/2026-10-29-speaking-up.md`** "Cost": say the caps now exist.
- [ ] **Commit:** "Docs: spend caps exist".

### Task 10 (controller): review, live check, PR

- [ ] A whole-branch review: correctness, plus SPN-14 on the card choice, SEC-4 (no amounts or keys in logs beyond what usage already logs), and the structural test's allow-list.
- [ ] Live check with the owner: set Anthropic's hard cap to just above this month's spend, talk until it trips, and try each card choice. Read `usage` back (read-only) to confirm there is exactly one row per call.
- [ ] Open the PR. Don't merge until the owner asks.
