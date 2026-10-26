# Widget design system

For contributors. Her board is how she shows things. This page says how a widget is built, how it looks, and how to add one. The full design is in `docs/specs/2026-10-25-widget-design-system.md`.

A widget is a small set of **blocks**. She writes a widget type (`next_up`, `plan`, `custom`). The server checks it and turns it into blocks. The page draws only blocks.

## Principles

- **One job per widget.** If it needs a paragraph to explain, it is two widgets.
- **She picks the content. The system picks the emphasis.** She cannot set colours, fonts, sizes or order. She can set a tone word.
- **Snapshot now, live later.** She reads a connector and writes the values into the widget. The server stamps when (`source.at`). Widget shapes will not change when live refresh arrives.

## Tokens

They live in `apps/web/src/app.css`, in the section "Widget kit". All are built on the theme colours and work in both themes.

| Token | For |
|---|---|
| `--w-hero`, `--w-value`, `--w-title`, `--w-body`, `--w-label`, `--w-meta` | The type scale. The hero value is the first top-level `value` in a card. |
| `--w-space-1` to `--w-space-4` | Spacing: 4, 8, 12, 16 px. |
| `--w-soft`, `--w-line` | A quiet fill and a hairline, for panels and dividers. |
| `--w-good-*`, `--w-bad-*`, `--w-attention-*`, `--w-neutral-*` | Tones. Each has `fill` (dots, bars, edges), `text` (words on or beside the fill) and `tint` (the panel behind). |
| `--w-tint-0` to `--w-tint-4`, `--w-ink-0` to `--w-ink-4` | Avatar backgrounds and their initials. They come from the chart series colours. A name hashes to one, so it keeps its colour. |

Tones:

| Tone | Means |
|---|---|
| `good` | On track, done, up in a good way |
| `bad` | Late, failing, down in a bad way |
| `attention` | Needs a look soon |
| `neutral` | Plain information |

`up` and `down` are accepted as `good` and `bad`. Text never sits on a colour without the matching tone text token.

## Blocks

Defined in `src/trillion/workspace/blocks.py`. Short strings are trimmed and capped, a long one cut with "…" (labels, names, chips and `by` 60; values, deltas and fact values 40; titles 80; details, hints, meta and status text 400), and emoji are removed. The paragraph texts are refused when too long rather than cut: `text` up to 2000 characters, `quote` up to 600. Unknown fields are dropped. A card holds at most 8 top-level blocks and 2 asks.

| Block | Fields | Notes |
|---|---|---|
| `value` | `value`, `label?`, `delta?`, `hint?`, `tone?` | The first top-level one is the hero. Others are drawn small. |
| `sparkline` | `values[]`, `tone?` | 2 to 60 finite numbers. |
| `meter` | `value`, `max`, `label?`, `shape?` (`bar` or `ring`), `tone?` | `max` above 0. The value is clamped to 0..max for drawing. |
| `time` | `at`, `end?`, `label?` | ISO 8601. Drawn as a countdown or "started 5 min ago", then the range. |
| `chart` | `kind`, `x[]`, `series[]`, `unit?` | Checked by the kit's chart rules. The card has the title. |
| `table` | `columns[]`, `rows[][]`, `highlight?` | `highlight` is a row index, counted from 0. |
| `timeline` | `events[]` (`title`, `start`, `end?`, `meta?`) | Times as `HH:MM`. |
| `rows` | `label?`, `items[]` (`title`, `detail?`, `person?`, `status?`, `when?`, `meta?`, `chip?`, `done?`, `amount?`, `amount_tone?`) | At most 30 items across a card. The page places them. An amount (up to 40 characters) sits on the right in tabular figures, with `when` under it. With an amount, `meta` and `chip` are not drawn. |
| `person` | `name`, `detail?` | Initials avatar. |
| `people` | `names[]` | Up to 8. An avatar stack, then "Sam, Jo and 2 more". |
| `status` | `tone`, `text` | A dot and words. |
| `chips` | `items[]`, `tone?` | Up to 6. |
| `text` | `text`, `tone?` | Paragraphs, `- ` bullets, `**bold**`. Up to 2000 characters. A tone gives a tinted edge. |
| `quote` | `text`, `by?`, `when?` | Up to 600 characters. |
| `facts` | `items[]` (`label`, `value`) | Up to 10. |
| `columns` | `columns[]`, each a list of blocks | 2 to 4 columns. Columns cannot hold columns. |
| `bars` | `items[]` (`label`, `value`, `note?`), `unit?`, `tone?` | 1 to 10 items, values 0 or more. Each bar is scaled to the largest, in the order given. |
| `funnel` | `stages[]` (`label`, `count`, `value?`) | 2 to 6 stages, counts 0 or more. Between stages the page says "55% move on" (nothing after a 0). |
| `stages` | `steps[]`, `current`, `tone?` | 2 to 7 steps. `current` is a step's label or its index from 0. Earlier steps are done, `current` is ringed, the rest are open. |

## Widgets

What she writes, and what it becomes. Every widget also takes `title?`, `size?`, `source?` and `asks?`. Checks live in `kit.py` (the first seven) and `recipe_kit.py` (the rest). The expansion to blocks is in `recipes.py`.

| Type | She writes | Becomes |
|---|---|---|
| `stat` | `label`, `value`, `delta?`, `hint?`, `tone?` | `value` |
| `chart` | `kind`, `x[]`, `series[]` | `chart` |
| `list` | `items[]` | `rows` |
| `table` | `columns[]`, `rows[][]` | `table` |
| `note` | `text` | `text` |
| `timeline` | `events[]` | `timeline` |
| `compare` | `options[]` with metrics, `pick?`, `reason?` | `columns`, one per option, then `text` |
| `next_up` | `event`, `start`, `end?`, `where?`, `people[]?`, `note?` | `time`, `people`, `chips`, `text` |
| `free_time` | `slots[]` (`start`, `end`, local time) | `rows`, one per day |
| `inbox` | `unread`, `needs_reply?`, `threads[]?` | `value`, `rows` |
| `thread` | `from`, `subject`, `request`, `due?`, `when?`, `excerpt?` | `person`, `text`, `facts`, `quote` |
| `mentions` | `groups[]` (`channel`, `items[]`) | one `rows` per channel |
| `files` | `files[]` | `rows` |
| `quote` | `text`, `by?`, `when?` | `quote` |
| `plan` | `steps[]` (`title`, `done?`, `detail?`) | `meter`, `rows` |
| `decision` | `options[]`, `criteria[]`, `scores[][]`, `pick?`, `reason?` | `table`, `text` |
| `facts` | `items[]` | `facts` |
| `callout` | `text`, `tone?` | `text` with a tone |
| `goal` | `label`, `value`, `target`, `unit?`, `by?` | `meter` (ring), `value` |
| `sources` | `items[]` (`title`, `site`, `note?`) | `rows` |
| `custom` | `blocks[]` | the blocks as given |

Catalogue widgets get a default title when she gives none. A near-miss type name maps to the right widget ("agenda" is `timeline`, "checklist" is `plan`). Those aliases are in `widgets.py`. Times may be ISO 8601 or a bare clock time like "9:12", which means today in the user's timezone. A count or goal written as text ("12", "4,200") is read as the number.

## Packs

Phase 2 added widgets for what the catalogue's connectors hold. She is told about them only while a matching connector is set up, because the toolkit text is read on every turn and small models do worse with more. They live in `src/trillion/workspace/packs.py`.

| Pack | Connectors (by catalogue title) | Widgets |
|---|---|---|
| core | none, always told | the phase 1 widgets, `kpis`, `tasks`, `custom` |
| money | Stripe, PayPal, Square | `transactions`, `invoices`, `breakdown`, `payouts` |
| customers | HubSpot, Intercom, Nitrosend, Resend | `pipeline`, `deal`, `contact`, `campaign`, `support_queue` |
| building | GitHub, Sentry, Vercel, Supabase | `checks`, `errors`, `deploys`, `services` |
| work | Linear, Jira and Confluence, Todoist, ClickUp, monday.com, Notion, Airtable | `kanban`, `sprint`, `due_soon` |

- Core is always told. A pack is told while a connector of that kind is configured and enabled. It does not need to be connected this second, so a slow start or a short outage does not take the widgets away.
- Packs change what she is told, never what is accepted. The kit takes every widget type at all times, so old boards keep loading and numbers she was told can still become a pipeline.
- The workspace tools' description is built from `told(...)`. The registry snapshots a definition, so `redefine` swaps it, and `retell_workspace` in `api/runtime.py` calls that at start and whenever a server is connected, disconnected or removed.
- Two budgets, both in tests. The four workspace tool definitions stay under 12,000 characters with core only, and under 16,000 with every pack. Each has about 200 characters of headroom. Trim before you add.

### Source tiles

A card's footer names its source. When the name matches the title of a connected catalogue entry (ignoring case), the footer shows that entry's tile before the name. Google services count when an account is signed in. The tile uses the catalogue's colour and letters, and the bundled icon Settings already shows. No match, no tile: the name alone. She names connector data by the connector's title ("Stripe", "Linear").

## Rules

She follows these (the prompt and `#kit` both state them).

1. One job per widget. If it needs a paragraph to explain, it is two widgets.
2. A number carries its unit and a comparison when one exists.
3. Titles say what it shows ("Unpaid invoices"), never "Here's your data".
4. Anything from a connector names its source.
5. Empty is said plainly: "Nothing due this week". No invented rows.
6. Round to what matters.
7. Prefer a catalogue widget. Make a custom one only when none fits.
8. Asks are for the obvious next question, not decoration. At most two.

## Tap to ask

A widget may carry up to two asks, 40 characters each. An ask is a quiet link at the foot of the card. Tapping it sends exactly its words as the user's message (`turn.text` with `from: "ask"`). It shows in the conversation like anything the user typed.

A tap can never approve. The turn runs with `via = "ask"`, and `confirm.decide` refuses a confirm from that source. A widget is text she wrote, so a tap on it is not the user agreeing to an action. Approvals are only the host-composed cards. Cancelling is allowed. While any approval card is waiting, asks are hidden.

Asks are refused when too long, not cut, because a clipped ask would send different words. An ask can't hold a web address (links go in a `sources` widget) or start with "[" (a "[host]" line is the app speaking). Her ask is her words, not the user's: an address in one never counts as one the user gave, so `web_fetch` will not open it, even after the conversation is resumed.

## Adding a block or a widget

For a widget in a pack, use the next section.

1. **Block.** Add its check to `src/trillion/workspace/blocks.py` and list it in `_BLOCKS`. Reuse the kit's helpers for sizes and text.
2. **Widget.** Add the check to `recipe_kit.py` (`RECIPES`) and the expansion to `recipes.py`. Add its page size in `SIZE` if it is not medium.
3. **Names.** Add aliases and the field that identifies it to `widgets.py` (`_ALIASES`, `_BY_FIELD`, `_KEY`).
4. **Tool schema.** Add the one-line card to `schema.py`. The four tool definitions must stay under 12,000 characters with core only (16,000 with every pack).
5. **Samples.** Add a sample to `samples.py`. Use generic names only.
6. **Regenerate.** `uv run python scripts/gen_kit_samples.py`. A test fails when `apps/web/src/kit/samples.json` is out of date.
7. **Draw it.** Add the block's drawing function to `apps/web/src/blocks.ts`. Use `textContent` only. Add its `bk-` styles to the "Widget kit" section of `app.css`, using the `--w-` tokens.
8. **Look.** Open `#kit`. Check light and dark, narrow and wide.

## Adding a pack or a pack widget

1. **Kit.** Add the check to `pack_kit.py`. Read state words leniently.
2. **Recipe.** Add the expansion to `pack_recipes.py`.
3. **Pack.** Add the type to its pack in `packs.py` (`PACKS`). For a new pack, also map its catalogue ids in `PACK_OF`.
4. **Names.** Add aliases and the identifying field to `widgets.py`.
5. **Schema.** Add the type to `FIELDS` and its one-line card to `CARDS` in `schema.py`. Check both budgets.
6. **Samples.** Add a sample to `samples.py`.
7. **Regenerate.** `uv run python scripts/gen_kit_samples.py`.
8. **Draw it.** Only if it needs a new block, add that to `apps/web/src/blocks.ts`.
9. **Look.** Open `#kit`. The caption shows the pack ("transactions · money · medium").

## Live widgets

A widget she builds from a connector keeps itself fresh while the user looks at the board. The spec is `docs/specs/2026-10-25-live-widgets.md`.

### What is remembered

- During a turn, the session records each read that succeeded and may run with nobody deciding. These are reads that `runs_unattended` allows, never `web_fetch`, memory or workspace tools. Each is kept with its tool name, its arguments and its connector.
- When she saves a widget with a `source`, the reads of that turn from the same connector become its `live`. The source matches by title or short name ("Calendar" for "Google Calendar"), ignoring case, with `-` and `_` read as spaces. The widget keeps the last five reads.
- No matching read means no `live`. The widget stays a snapshot.
- `live` is stored in the board as plain JSON, like the conversation history. It goes when the board goes. She cannot write it, and the page never sees the reads, only how often and how it is going.

### Rates

She may set `refresh` to `"off"` or to minutes, from 2 to 1440. Without it, the type decides:

- 2 minutes: next up, inbox, mentions, checks, deploys, services, support queue.
- 15 minutes: the money and customers packs, kanban, sprint, tasks, due soon, KPIs.
- 60 minutes: everything else.

### When it runs

- Only while a page shows the board. The page sends `board.seen` when the board is shown or hidden and when the tab changes visibility, and marks it `opened` only when the board itself was opened.
- Showing the board catches up at once: every widget past its rate is refreshed.
- One runtime task works a queue, one refresh at a time, at most 60 an hour.
- It waits while her own turn is running on that conversation.
- After three failures in a row a widget is left alone until the board is opened anew. Switching tabs back is not opening it.
- The Settings switch "Keep widgets fresh" (on by default) stops all of it.

### The fill-in

The server replays the saved reads. Then one model call with no tools turns the fresh results into the widget's data. The results reach it wrapped as untrusted, at most about 24,000 characters in all, shared across the reads. From the answer the server takes only the data fields of the widget's type. It keeps the type, title, size, source, asks and `refresh` from the widget as it was, and the field that names it: a stat's or goal's `label`, a thread's `subject`, a deal's, contact's or sprint's `name`, and a chart's `kind`. The kit checks the result like any widget she writes. A refreshed widget is named to her by its title only, so nothing the fill-in wrote reaches her context. Nothing is added to the conversation. The usage is recorded like a turn's.

### Safety

Replay goes through the router as `via = "refresh"`. It runs reads only. It refuses anything gated, any card, any confirmation, and every workspace, schedule and `web_fetch` call. The model has no tools, so a hostile result can change at most the data fields of one card, which is the same trust as the first snapshot (SPN-5).

### What the user sees

- A live card's footer reads "Stripe · live · 1 min ago". While it refreshes, a dot beside "live" pulses (not with reduced motion).
- After a failure the footer reads "Couldn't refresh · 12 min ago".
- A refresh button sits beside the ×. It refreshes that card now and counts toward the hourly cap.

## Your day

Ask "what's my day?", or tap **Your day** above the thread list. Both open today's day conversation, one per date, and build the board there. Nothing builds by itself. The page only asks (`day.build`). The host writes what she is told, in `persona/day.py`, and saves it as a host message that the conversation shows as "Building your day".

She reads each source with her own tools, so every card is live. A source with nothing to show gets no card.

| Source | Widget |
|---|---|
| Google Calendar | `next_up`, `free_time` |
| Gmail | `inbox` |
| Slack | `mentions` |
| Work pack | `due_soon` |
| Money pack | `kpis` |
| Building pack | `checks` or `errors`, only if something is failing |
| Her reminders | `tasks` |

She shows at most 8 widgets, the most urgent first, then says two short sentences about what matters most. She does not read the board out. With nothing connected she shows a callout that says how to connect a calendar.

To personalise it, give her a standing rule, such as "on my day board, skip mentions". The recipe tells her to follow her rules for the day board.

Tapping Your day again opens the same conversation and does not rebuild a board that is there. Tomorrow a new day conversation is made. Yesterday's stays in the list.

## Next

- Refreshing with no page open, and push from services.
