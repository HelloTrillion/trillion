# Spend caps: she can't run up your bill

**Date:** 2026-11-04 · **Requirements:** BRN-7, BRN-8, ONB-9, ONB-10 · **Builds on:** [the API usage pill](2026-10-22-api-usage.md), [one leg down](2026-10-20-one-leg-down.md), [speaking up](2026-10-29-speaking-up.md), [routines](2026-10-27-routines.md)

People bring their own keys, so every bill lands on them. Today nothing stops spending: a runaway routine, a watcher stuck in a loop or a long day of talking can cost what it costs. Spending is recorded (the usage pill), but only for Anthropic models. OpenAI-compatible models record no price, and the voice and hearing providers record nothing at all.

This change puts every paid call behind one **spend gate** with a monthly soft cap and hard cap per provider. It also says what each provider roughly costs before a key is entered.

## Decisions

1. **One spend gate, on the providers themselves.** Every model, voice and hearing provider is wrapped in a metered layer. Before each call it checks that provider's spend this month against its caps. After each call it records priced usage. No code reaches a provider except through it. A structural test enforces this, so a new routine or watcher is capped without anyone remembering to cap it.
2. **Models, voice and hearing are all capped.** Models are priced per token, ElevenLabs per character spoken, and Deepgram per minute of audio streamed.
3. **Prices come from a built-in table, plus what you tell it.**
   - The table covers Anthropic (as today) and common OpenAI and DeepSeek models.
   - OpenRouter's own reported cost is used.
   - A local model costs $0 and has no cap.
   - Any other model asks for its price per million tokens in Settings. Until you give one, its caps count tokens instead of dollars, and Settings says so.
   - Voice and hearing rates are pay-as-you-go. You can override a rate if you're on a plan.
4. **Every usage row says who spent it:** `conversation` (a live session: her replies, drafts, summaries of long chats, the day board she builds when you open it) or `background` (routines, timed asks, watchers, channel replies, titles).
5. **Soft cap:** a warning and a mention, nothing stops. **Hard cap:** the gate refuses new calls. A call already running always finishes.
6. **At the hard cap, your conversation finishes the reply in progress and then asks.** You get one line from the app, spoken if voice is on and shown in the banner. Then a card: raise the cap, switch to a cheaper option until the 1st, or stop for now. Nothing changes model or voice silently (BRN-8).
7. **At the hard cap, background work pauses and says so.** It never moves to a weaker model that then writes what you read (BRN-11). This amends BRN-8, which said background work "degrades to a cheaper or local provider"; the PRD row is updated with this change.
8. **Defaults:** per provider, per month, $20 soft (warning at 80%, $16) and $50 hard. New keys get them, pre-filled and editable in the same step as the key. Existing installs get them on upgrade, and are told once.

## What is counted

### The month

The calendar month in the user's timezone (`USER.md`), or the machine's local time if none is set. It is the same month the usage pill already counts (see [the API usage pill](2026-10-22-api-usage.md)).

### Providers

A cap belongs to a **provider id**:

| Provider id | What | Unit |
|---|---|---|
| `anthropic` | model kind `anthropic` | dollars |
| `openai` | model kind `openai` | dollars |
| `custom:<host>` | model kind `custom`, by the host of its base URL (for example `custom:openrouter.ai`, `custom:api.deepseek.com`) | dollars when priced, otherwise tokens |
| `local` | model kind `local` | never capped |
| `elevenlabs` | voice | dollars, from characters |
| `deepgram` | hearing | dollars, from minutes |
| `openai-voice:<host>` | an OpenAI-compatible speech server | dollars when priced, otherwise characters |

The system voice and local Whisper run on the machine and are never capped.

### Prices

`src/trillion/spend/prices.py` holds one table: provider id and model, then a rate per unit (input, output, cache read and cache write per million tokens, or a rate per thousand characters or per minute). Every entry records the date it was checked and the pricing page it came from.

- **Anthropic:** the table that lives in `brain/providers/anthropic.py` today moves here, unchanged, including its long-prompt rates.
- **OpenAI and DeepSeek:** the models the app offers in Settings.
- **OpenRouter:** the response's reported cost (`usage.cost`, requested with `usage: {include: true}`) wins over any table entry.
- **ElevenLabs and Deepgram:** pay-as-you-go rates for the models the app uses (the ElevenLabs model in `voice/providers/elevenlabs.py`, Deepgram Flux).
- **User prices** are kept in preferences (`spend_prices`) and win over the table. They cover any model, and a voice or hearing rate for someone on a plan.

A row priced by nothing is stored with `cost_usd` NULL, as today, and is never counted as $0.

### What a usage row records

Migration `017_usage_spend.sql` adds three columns to `usage`:
- `provider_id TEXT` (the id above);
- `caller TEXT NOT NULL DEFAULT 'conversation'`, either `conversation` or `background`;
- `units TEXT`, either `tokens`, `chars` or `seconds`, with the four token columns reused: `input_tokens` holds characters or seconds for voice and hearing rows.

Old rows get the provider id derived from their `provider` column and count as conversation.

**Who is calling** is carried by a context variable set once at the top of each caller: the session for its turns, drafts, summaries and day board; the scheduler, the watchers, channels and the title writer for theirs. An unset caller counts as `background`, so an unlabelled path is capped the stricter way.

## The spend gate

`src/trillion/spend/` holds the gate:
- `prices.py`: the table and user prices;
- `ledger.py`: spend this month per provider id, read from `usage`, cached and updated as rows are written;
- `gate.py`: caps and state;
- `metered.py`: the wrappers.

`MeteredModel`, `MeteredVoice` and `MeteredHearing` wrap a `ModelProvider`, a TTS provider and an STT provider. They have the same interfaces as what they wrap, so nothing else changes shape. The runtime builds providers only through them.

**Before a call**, the wrapper asks the gate for its provider id's state:
- **ok** or **warned**: the call goes ahead.
- **capped**: the call is refused with `SpendCapped(provider_id, caller)`, a `VoiceError`-style error with kind `capped`, before anything is sent.

A conversation that you chose to continue on a capped provider ("raise the cap") is not capped, by definition. Raising the cap changes the state.

**After a call** (a model stream ending, a sentence synthesized, a hearing stream closed), the wrapper records one usage row with its provider id, caller, units and cost, and the ledger adds it.

**A call already running finishes.** The check happens only before a call. Spend can pass the hard cap by at most what was in flight when it was crossed: one reply's model calls, the sentences already being spoken, or a few background calls running at once. It never goes further.

**Hearing** is metered by the second while a stream is open. The gate is asked when a stream opens. A stream that crosses the hard cap is let finish its turn, then the next open is refused.

## States and what each does

| State | When | Pill | Conversation | Background |
|---|---|---|---|---|
| ok | below the warning | as today | normal | normal |
| warned | at the warning percentage (default 80%) of the soft cap | amber, tooltip names the provider | normal. One notice. | normal |
| over soft | at the soft cap | amber, "over your $20 soft cap" | normal. She mentions it once, in her own words, in her next reply, through a host note on that turn (PER-11). | normal |
| capped | at the hard cap | red, tooltip names the provider and cap | see below | pauses |

Notices go through the one door (SUR-9), once per provider per state per month.

### Capped: your conversation

She can't phrase anything on a capped model, so the notice follows the [one leg down](2026-10-20-one-leg-down.md) path: a fixed, specific line from the app, spoken if voice is up and shown in the degraded banner (SUR-8). For example: "I've reached the $50 cap for Anthropic this month, so I've stopped before spending more." Then a **spend card** with the choices below. It is the app's card, not a pending action; a choice is a tap, because it is about money (SPN-14).

| Capped | What happens | The card offers |
|---|---|---|
| Her model | The reply in progress finishes. The next turn is refused before anything is sent. | **Raise the cap** (a field on the card, prefilled with the next round step), **Use [model] until [Dec 1]** (only models already set up, on a provider with budget left, local first), **Stop for now** |
| Her voice | The sentence in progress finishes; the rest of the reply is text. | **Raise the cap**, **Use the computer's voice until [Dec 1]**, **Text only** |
| Hearing | Listening stops after the current turn. Typing still works. | **Raise the cap**, **Use local Whisper until [Dec 1]** (only if installed), **Type for now** |

- **Switching** is recorded as a temporary preference that ends on the 1st. The banner says so while it lasts ("Using Gemma until Dec 1"), and Settings can end it early. On the 1st she goes back to the chosen model or voice, and says so once.
- **Stop for now** leaves the card answerable from the banner, so it can be changed later without opening Settings.

### Capped: background work

- Routines, timed asks, watchers, channel replies and titles on that provider are refused by the gate.
- A routine or timed ask whose run is refused records the run as `skipped`, with the reason `capped`, in its run log. It is not retried that month.
- A watcher keeps its place and checks again after the cap lifts, so nothing is lost; it just isn't read until then.
- One notice says what paused and why ("Routines and the inbox watcher are paused: Anthropic is at its $50 cap"). Settings → Usage shows paused work under the provider.
- It all resumes on the 1st, or as soon as the cap is raised.

## Settings → Usage

Settings → Usage lists each provider that is set up, with:
- a line saying **what it unlocks, roughly what it costs, and the free option** (ONB-9). The estimate comes from the price table and a stated assumption, and the assumption is shown. For example: "Anthropic: how she thinks. Roughly $0.40 an hour of talk on Sonnet (about 40 short turns), plus routines. Free option: a local model with Ollama."
- the month so far, split into conversation and background;
- **soft cap, hard cap, warning %**, editable (hard must be at least soft; either can be removed, which says plainly that nothing is capped);
- a link to the provider's pricing page;
- for an unpriced model, a **price per million tokens** field; for a voice or hearing provider, a **rate override**;
- any paused background work, and any temporary switch with its end date.

The pill keeps its current text and gains the warned and capped colours. Its tooltip names the provider. Clicking it opens Settings → Usage as today.

## When a key is entered

Wherever a key is entered (onboarding's model step, Settings → Model, Voice and Hearing), the same step shows:
- the provider's rough cost line;
- its pricing link;
- its soft and hard caps, filled in with the defaults and editable.

Saving the key saves the caps. Choosing a local model or the system voice shows "Runs on this computer: no cost, no cap." (ONB-10)

## Existing installs

On the first start with this change, every set-up provider without caps gets the defaults. Once, in the next conversation, she tells the owner in her own words (a host note, PER-11) that caps are on, what they are, and where to change them.

## API

- `GET /api/usage` adds, per provider id: `spent`, `unit`, `soft`, `hard`, `warn_pct`, `state`, `split` (conversation and background), `paused` (background names) and `switched` (model or voice and end date).
- `PUT /api/settings/spend/{provider_id}`: `{soft, hard, warn_pct}`, with `null` for no cap. Validated: hard ≥ soft, non-negative, warn_pct 1–100.
- `PUT /api/settings/spend/prices`: user prices, per provider id and model.
- `POST /api/spend/choice`: a spend card's choice (`raise` with an amount, `switch` with a target, `stop`). It is accepted only from the session that was shown the card, by a tap (SPN-14).
- All behind the session token and origin check (SPN-11).

## Platforms

| Part | macOS | Windows | Linux | Browser tab | Desktop app |
|---|---|---|---|---|---|
| Gate, ledger, prices | same everywhere: core only | same | same | — | — |
| Pill, Settings, spend card | — | — | — | yes | yes (same page) |
| Spoken cap line | when her voice is up | same | same | yes | yes |

## Tests

- **Gate** (`tests/spend/`):
  - each state at its threshold;
  - a call refused at or over the hard cap, before anything is sent;
  - a call in flight is let finish;
  - only this month counts, in the user's timezone, across the 1st;
  - conversation and background are counted apart;
  - a local model and the system voice are never capped;
  - an unset caller counts as background.
- **Prices:**
  - each table entry, including long-prompt rates;
  - OpenRouter's reported cost wins;
  - an unknown model counts tokens until priced;
  - characters and minutes are priced;
  - a user price or rate wins;
  - a NULL cost is never counted as $0.
- **Structural** (`tests/spend/test_structure.py`): every `ModelProvider`, TTS and STT the runtime hands out is metered, and no module calls a provider's `stream`, `synthesize` or `open` except through the wrappers.
- **Session, with fake providers:**
  - at the hard cap the reply finishes, then the next turn is refused with the line and the card;
  - raise, switch and stop each do what they say;
  - a switch is announced, shown in the banner, and ends on the 1st;
  - voice capped finishes the sentence and goes to text;
  - hearing capped stops after the turn;
  - the soft-cap mention happens once, in her words.
- **Background:**
  - a routine run at the cap is recorded `skipped`, reason `capped`;
  - a watcher keeps its place;
  - one notice;
  - all resume when the cap is raised or the month turns.
- **Migration:** old rows get a provider id and `conversation`.
- **API:** validation, the token and origin checks, and a card choice from another session or not by a tap is refused.
- **Web:**
  - pill states;
  - Settings rows (cost line, caps, prices, paused, switched);
  - the key step showing the estimate and the caps;
  - the spend card.

## Out of scope

- Caps per agent (there is one agent), and spend by routine or watcher (BRN-10, P1).
- Fetching prices from providers live.
- Daily or weekly caps.
- A provider's own spending limit on its side, which users can still set there and which this doesn't read.
