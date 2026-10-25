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
| `rows` | `label?`, `items[]` (`title`, `detail?`, `person?`, `status?`, `when?`, `meta?`, `chip?`, `done?`) | At most 30 items across a card. The page places them. |
| `person` | `name`, `detail?` | Initials avatar. |
| `people` | `names[]` | Up to 8. An avatar stack, then "Sam, Jo and 2 more". |
| `status` | `tone`, `text` | A dot and words. |
| `chips` | `items[]`, `tone?` | Up to 6. |
| `text` | `text`, `tone?` | Paragraphs, `- ` bullets, `**bold**`. Up to 2000 characters. A tone gives a tinted edge. |
| `quote` | `text`, `by?`, `when?` | Up to 600 characters. |
| `facts` | `items[]` (`label`, `value`) | Up to 10. |
| `columns` | `columns[]`, each a list of blocks | 2 to 4 columns. Columns cannot hold columns. |

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

1. **Block.** Add its check to `src/trillion/workspace/blocks.py` and list it in `_BLOCKS`. Reuse the kit's helpers for sizes and text.
2. **Widget.** Add the check to `recipe_kit.py` (`RECIPES`) and the expansion to `recipes.py`. Add its page size in `SIZE` if it is not medium.
3. **Names.** Add aliases and the field that identifies it to `widgets.py` (`_ALIASES`, `_BY_FIELD`, `_KEY`).
4. **Tool schema.** Add the one-line card to `schema.py`. The whole tool definition must stay under 12,000 characters.
5. **Samples.** Add a sample to `samples.py`. Use generic names only.
6. **Regenerate.** `uv run python scripts/gen_kit_samples.py`. A test fails when `apps/web/src/kit/samples.json` is out of date.
7. **Draw it.** Add the block's drawing function to `apps/web/src/blocks.ts`. Use `textContent` only. Add its `bk-` styles to the "Widget kit" section of `app.css`, using the `--w-` tokens.
8. **Look.** Open `#kit`. Check light and dark, narrow and wide.

## Next

- **Phase 2:** widgets for the catalogue's connectors: money, customers, building, work. They are new widget types on the same blocks.
- **Phase 3:** live refresh. A widget keeps its shape and the server refreshes its values.
