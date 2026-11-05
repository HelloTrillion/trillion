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
   - A local provider (an address on this computer: `localhost` or a loopback address) costs $0 and has no cap.
   - Any other model asks for its price per million tokens in Settings. Until you give one, its caps count tokens instead of dollars, and Settings says so.
   - Voice and hearing rates are pay-as-you-go. You can override a rate if you're on a plan.
4. **Every usage row says who spent it:** `conversation` (a live session: her replies, drafts, summaries of long chats, the day board she builds when you open it) or `background` (routines, timed asks, watchers, channel replies, titles).
5. **Soft cap:** a warning and a mention, nothing stops. **Hard cap:** the gate refuses new calls. A call already running always finishes.
6. **At the hard cap, your conversation finishes the model call in flight and then asks.** A later model call in the same turn (after a tool round) is refused, and the turn ends with the text so far. You get one line from the app, spoken if voice is on and shown in the banner. Then a card: raise the cap, switch to a cheaper option until the 1st, or not now. Nothing changes model or voice silently (BRN-8).
7. **At the hard cap, background work pauses and says so.** It never moves to a weaker model that then writes what you read (BRN-11). This amends BRN-8, which said background work "degrades to a cheaper or local provider"; the PRD row is updated with this change.
8. **Defaults:** per provider, per month, $20 soft (warning at 80%, $16) and $50 hard for a provider with a price. A provider without one gets caps in its own unit (see [Caps and units](#caps-and-units)). New keys get them, pre-filled and editable in the same step as the key. Existing installs get them on upgrade, and are told once.

## What is counted

### The month

The calendar month in the user's timezone (`USER.md`), or the machine's local time if none is set. It is the same month the usage pill already counts (see [the API usage pill](2026-10-22-api-usage.md)).

Whenever the month's start moves, the ledger reads the month again from the database. Time passing into a new month starts at zero meanwhile. A new timezone, which can move the month either way, drops nothing: what was counted keeps answering until the read succeeds, so a read that fails never lets a call through.

### Providers

A cap belongs to a **provider id**:

| Provider id | What | Unit |
|---|---|---|
| `anthropic` | model kind `anthropic` | dollars |
| `openai` | model kind `openai`, and any base URL at `api.openai.com` however it was entered | dollars |
| `custom:<host>` | model kind `custom`, by the host of its base URL (for example `custom:openrouter.ai`, `custom:api.deepseek.com`) | dollars when priced (OpenRouter always, from its own cost), otherwise tokens |
| `local` | model kind `local`, and a custom address on this computer (`localhost`, loopback) | never capped |
| `elevenlabs` | voice | dollars, from characters |
| `deepgram` | hearing | dollars, from minutes |
| `openai-voice:<host>` | an OpenAI-compatible speech server | dollars when priced, otherwise characters |

The system voice, local Whisper and a voice server on this computer run on the machine and are never capped.

### Caps and units

Every cap carries a unit: dollars, tokens, characters or seconds (shown as minutes).

- **A dollar cap** counts priced dollars, plus usage nothing priced at the time (earlier in the month, or a model without a price). That usage is estimated at the user's own rate for the provider (input and output blended 20 to 1), or else at the provider's dearest table rate. So a price given mid-month counts the tokens used before it.
- **A unit cap** counts every unit, priced or not.
- **Defaults:** $20 and $50 for a provider with a price. For one without: 5M and 12M tokens, 400k and 1M characters, 600 and 1,500 minutes.
- Giving a price to a provider still on its unit defaults moves it to the dollar defaults. Removing its last price moves default caps back to its unit. Caps the user chose are kept.

### Prices

`src/trillion/spend/prices.py` holds one table: provider id and model, then a rate per unit (input, output, cache read and cache write per million tokens, or a rate per thousand characters or per minute). Every entry records the date it was checked and the pricing page it came from.

- **Anthropic:** the table that lives in `brain/providers/anthropic.py` today moves here, unchanged, including its long-prompt rates.
- **OpenAI and DeepSeek:** the models the app offers in Settings.
- **OpenRouter:** the response's reported cost (`usage.cost`, requested with `usage: {include: true}`) wins over any table entry. A call it sends no cost for is stored unpriced, and a dollar cap counts it at the dearest token rate anywhere in the table (any provider, any part, long prompts included), since its models can be any vendor's.
- **ElevenLabs and Deepgram:** pay-as-you-go rates for the models the app uses (the ElevenLabs model in `voice/providers/elevenlabs.py`, Deepgram Flux).
- **User prices** are kept in preferences (`spend_prices`) and win over the table. They cover any model, and a voice or hearing rate for someone on a plan.
- **A price of zero** is refused (`zero_price`) for any model, and for a voice or hearing provider the table has no rate for: it would count nothing, or move caps in units to dollars that never grow. A model that is free runs on this computer. Only a voice or hearing rate the table has may be zero, for someone on a plan.

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

**After a call** (a model stream ending, a sentence synthesized, a hearing stream closed), the wrapper records one usage row per call (never a summed turn) with its provider id, caller, units and cost, and the ledger adds it.

A model call that may cost (not a local model) is recorded even when its usage is not known:
- **No usage sent** (an OpenAI-compatible server that sends no usage chunk): estimated at four characters a token, of the text sent (system prompt, messages and tools) for input and the text streamed for output, then priced as any call.
- **Stopped before its end** (a barge-in, a dropped draft, an error partway): the usage the provider reported so far (Anthropic says the prompt's tokens when the reply starts), with no fewer output tokens than were streamed; else the same estimate. An error before anything came back used nothing and records nothing.

Each call is recorded once, either way, and the write is finished even if the caller is cancelled.

**A call already running finishes.** The check happens only before a call. Spend can pass the hard cap by at most what was in flight when it was crossed: the model call answering, the sentences already being spoken, or a few background calls running at once. It never goes further.

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

She can't phrase anything on a capped model, so the notice follows the [one leg down](2026-10-20-one-leg-down.md) path: a fixed, specific line from the app, spoken if voice is up and shown in the degraded banner (SUR-8). For example: "I've reached the $50 cap for Anthropic this month, so I've stopped before spending more." Then a **spend card**, titled "You've hit your monthly cap", with the choices below. It is the app's card, not a pending action; a choice is a tap, because it is about money (SPN-14).

- The page is calm: no error orb, and the header says "Stopped at your cap" until a raise, a switch or the next turn. On a phone the sheet opens for the card, as for an approval.
- A choice goes over the conversation's own WebSocket as `{"type": "spend.choice", "id", "choice", "amount"?}`, taken under the same input guard as an approval's tap. The server supplies the session and "tap"; the page can't. The reply is `spend.resolved`, or `spend.refused` with a `code` (for example `target_capped`). One choice at a time: a double tap is answered once.
- A raise defaults to the next $25 step ("Raise to $75"). It must be at least the hard cap, above the month's spend, and at most $10,000 (or 100 times the unit's default hard cap).

| Capped | What happens | The card offers |
|---|---|---|
| Her model | The call in flight finishes. A later call in the same turn (after a tool round) is refused, and the turn ends with the text so far. The next turn is refused before anything is sent. | **Raise to $X** (a field on the card, prefilled with the next round step), **Use [model] until [Dec 1]** (only models already set up, on a provider with budget left, local first), **Not now** |
| Her voice | The sentence in progress finishes; the rest of the reply is text. | **Raise to $X**, **Use the computer's voice until [Dec 1]**, **Text only** |
| Hearing | Listening stops after the current turn. Typing still works. | **Raise to $X**, **Use local Whisper until [Dec 1]** (only if installed), **Type for now** |

- **Switching** is recorded as a temporary preference that ends on the 1st. The banner says so while it lasts ("Using Gemma until Dec 1"). On the 1st she goes back to the chosen model or voice, and says so once.
- **A switch covers the conversation only.** Background work stays paused on the usual provider until its cap is raised or the 1st.
- **Switch back now**, in Settings and the banner, ends a switch early (`POST /api/spend/switch/{provider_id}/end`). It is refused while the usual provider is still capped, so it is offered only once it can work, and asked after a raise.
- **Not now** leaves the card answerable from the banner, so it can be changed later without opening Settings. A reload shows a card put off.

### Capped: background work

- Routines, timed asks, watchers, channel replies and titles on that provider are refused by the gate.
- A routine or timed ask refused before it ran records the run as `skipped`, with the reason `capped`, in its run log. One refused partway records `failed`: "stopped partway: its model reached the spend cap". Neither is retried that month.
- A watcher keeps its place and reads on from it after the cap lifts. It still reads back no further than its own limit: the urgent mail watcher reads at most one day back, so older mail is not read.
- One notice says what paused and why, naming only the kinds set up ("Routines and the board are paused: Anthropic is at its $50 cap"; "Background work is paused" when only titles are). Settings → Usage and limits shows paused work under the provider.
- It all resumes on the 1st, or as soon as the cap is raised.

## Settings → Usage and limits

Settings → Usage and limits lists each provider that is set up, with:
- a line saying **what it unlocks, roughly what it costs, and the free option** (ONB-9). The estimate comes from the price table and a stated assumption, and the assumption is shown. For example: "Anthropic: how she thinks. Roughly $0.40 an hour of talk on Sonnet (about 40 short turns), plus routines. Free option: a local model with Ollama."
- the month so far, split into conversation and background;
- **Warn me at** (the soft cap) and **Stop at** (the hard cap), editable, with the warning % under More options (hard must be at least soft; either can be removed, which says plainly that nothing is capped);
- a link to the provider's pricing page;
- for an unpriced model, a **price per million tokens** field; for a voice or hearing provider, a **rate override**;
- any paused background work, and any temporary switch with its end date.

The pill keeps its current text and gains the warned and capped colours. Its tooltip names the provider, and says "Includes estimates for calls without a price." when its dollars include one. Clicking it opens Settings → Usage and limits as today.

On an install that used an OpenAI-compatible model earlier in the month it upgraded, Settings → Usage and limits says once that this use is not counted toward the limits: those rows name no address, so no cap can take them.

## When a key is entered

Wherever a key is entered (onboarding's model step, Settings → Model, Voice and Hearing), the same step shows:
- the provider's rough cost line;
- its pricing link;
- its soft and hard caps, filled in with the defaults and editable.

Saving the key saves the caps. Choosing a local model or the system voice shows "Runs on this computer: no cost, no cap." (ONB-10)

## Existing installs

On every start, each provider in use (the model, including one set by the environment, what a switch put in its place, the voice and hearing) without a caps entry gets the defaults. An entry already there is never changed, including caps the user removed (saved as null). On the first start with this change only, in the next conversation, she tells the owner in her own words (a host note, PER-11) that caps are on, what they are, and where to change them, and, when this month has OpenAI-compatible use from before caps, that it is not counted.

A card offers a custom model only on the address the custom key was saved for: every custom address shares one key slot, so a switch never sends one server's key to another.

## API

- `GET /api/usage` adds `providers[]`, one per provider set up: `spent`, `unit`, `soft`, `hard`, `warn_pct`, `state`, `split` (conversation and background), `paused` (background names), `switched` (model or voice, end date and its line), unestimated units, `estimated` (its dollars include an estimate), a cost line on a stated assumption, and its pricing page. It also adds `uncounted_before_caps`: this month has OpenAI-compatible use from before caps.
- `PUT /api/settings/spend/{provider_id}`: `{soft, hard, warn_pct}`, with `null` for no cap. Validated: hard at least soft, non-negative, warn_pct from 1 to 100.
- `GET` and `PUT /api/settings/spend/prices`: user prices, per provider id and model.
- `POST /api/spend/switch/{provider_id}/end`: Switch back now. Refused with 409 `still_capped` while the usual provider is at its cap.
- The settings listings for models, voices and hearing carry each choice's cost line and caps.
- A card's choice is not an HTTP call: it is `spend.choice` on the conversation's socket (see above).
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
  - at the hard cap the call in flight finishes, then the next call is refused with the line and the card;
  - raise, switch and not now each do what they say;
  - a switch is announced, shown in the banner, and ends on the 1st;
  - voice capped finishes the sentence and goes to text;
  - hearing capped stops after the turn;
  - the soft-cap mention happens once, in her words.
- **Background:**
  - a routine refused at the cap is recorded `skipped`, reason `capped`, and one refused partway `failed`;
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
