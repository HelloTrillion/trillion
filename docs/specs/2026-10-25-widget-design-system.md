# The widget design system

**Date:** 2026-10-25 · **Requirements:** SUR-1, SPN-2a, SPN-5, SPN-14 · **Builds on:** `docs/specs/2026-10-25-her-board.md`, `docs/specs/2026-10-25-board-approvals.md`

Her board is how she shows things, and it is what sets Trillion apart from other
voice-first assistants. This spec gives the board a design system: a small set of
**blocks** every widget is made of, **recipes** that turn her widget types into
blocks, **tokens** and **rules** that keep everything consistent, and a **style
guide** that shows it all. With it she can make a custom widget that looks as if it
shipped with the app.

This is phase 1 of 3. Phase 2 adds widgets for the catalogue's connectors (money,
customers, building, work). Phase 3 adds live refresh. Nothing here may block them.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Snapshot now, live later.** She reads a connector and writes the values into the widget. The server stamps when. Widget shapes do not change when live refresh arrives. | What the user chose. |
| D2 | **Everything is blocks.** The server expands every widget into blocks; the page draws only blocks. Built-in and custom widgets go down the same path. | One visual language that cannot drift. |
| D3 | **She picks the content; the system picks the emphasis.** No colours, fonts, sizes or order inside a card are hers to set, except tone words. | Consistency and no slop. |
| D4 | **Custom widgets arrange blocks.** `type: "custom"` with `blocks`, checked like any widget. | What the user chose. |
| D5 | **Tap to ask.** A widget may carry up to two asks; tapping one sends exactly its words as the user's message. A turn started from an ask can never approve a pending action. | What the user chose; SPN-2a and SPN-14 unchanged. |
| D6 | **One set of samples** feeds the style guide and a server test. | The guide can never show what she may not make. |

## The wire format

What the page receives for one widget (the board message keeps its shape otherwise):

```json
{
  "id": "w3", "type": "next_up", "size": "medium", "title": "Next up",
  "blocks": [
    { "type": "time", "label": "Design review", "at": "2026-10-25T14:30", "end": "2026-10-25T15:00" },
    { "type": "people", "names": ["Sam", "Jo"] },
    { "type": "chips", "items": ["Maple Park room 2"] }
  ],
  "source": { "name": "Calendar", "at": "2026-10-25T21:05:12Z" },
  "asks": ["Prep me for this"]
}
```

- The saved board keeps **what she wrote** (the recipe input, validated). The server
  expands it to blocks each time it sends a board. Boards saved before this change
  load unchanged: today's seven types are recipes with the same fields.
- `type` stays on the wire for naming ("From Trillion", the summary, screen readers).
  The page never draws by `type`.
- `source.name` is hers (≤30 characters). `source.at` is the server's clock at the
  tool call that put the widget up; she cannot set it.
- Timestamps are ISO 8601. With an offset or `Z` they are absolute; without one they
  are the user's local time. The page shows them relative ("in 25 min", "Tue 3:00 pm")
  and updates them every minute.

## Blocks

`type` names the block. Text is capped as in the board spec (labels 60, values 40,
titles 80, other text 400) unless noted. Tones are `good | bad | attention | neutral`
(`up` and `down` are accepted as `good` and `bad`).

| Block | Fields | Limits and notes |
|---|---|---|
| `value` | `value`, `label?`, `delta?`, `hint?`, `tone?` | The first top-level `value` in a card is drawn as the hero; every other is drawn small. |
| `sparkline` | `values[]`, `tone?` | 2–60 finite numbers. |
| `meter` | `value`, `max`, `label?`, `shape?` (`bar\|ring`), `tone?` | `max > 0`; `value` clamped to 0…max for drawing; a ring shows the percent inside. |
| `time` | `at`, `end?`, `label?` | The label large, then a countdown or "started 5 min ago", then the range. |
| `chart` | `kind`, `x[]`, `series[]`, `unit?` | As today. |
| `table` | `columns[]`, `rows[][]`, `highlight?` (row index) | As today; the highlighted row is tinted. |
| `timeline` | `events[]` (`title`, `start`, `end?`, `meta?`) | As today (`HH:MM`). |
| `rows` | `label?`, `items[]` (`title`, `detail?`, `person?`, `status?` (tone), `when?`, `meta?`, `chip?`, `done?`) | ≤30 items across a card. The page places them: a person's avatar or a status dot on the left; `when`, `meta` or `chip` on the right (one, in that order). |
| `person` | `name`, `detail?` | Initials avatar; its tint is a hash of the name, so a name keeps its colour. |
| `people` | `names[]` | ≤8; an avatar stack, then "Sam, Jo and 2 more". |
| `status` | `tone`, `text` | A dot and words. |
| `chips` | `items[]`, `tone?` | ≤6. |
| `text` | `text`, `tone?` | ≤2000; paragraphs, `- ` bullets and `**bold**` (today's note). A tone gives a tinted edge (a callout). |
| `quote` | `text`, `by?`, `when?` | ≤600. |
| `facts` | `items[]` (`label`, `value`) | ≤10. |
| `columns` | `columns[]` (each a list of blocks) | 2–4 columns; blocks inside may not be `columns`. |

Card limits: at most **8** top-level blocks and 8 per column, **2** asks (≤40
characters each). Emoji are stripped from every string. Anything else in a block is
dropped, as today.

## Recipes

What she writes, and what it becomes. Every recipe also takes `title?`, `size?`,
`source?`, `asks?`.

| Type | She writes | Blocks |
|---|---|---|
| `stat` | `label`, `value`, `delta?`, `hint?`, `tone?` | `value` |
| `chart` | as today | `chart` |
| `list` | `items[]` (`title`, `subtitle?`, `meta?`, `done?`) | `rows` (`subtitle` → `detail`) |
| `table` | as today | `table` |
| `note` | `text` | `text` |
| `timeline` | as today | `timeline` |
| `compare` | as today | `columns`, one per option: `value` (name, label "Her pick" when picked), a `meter` per metric (max 10), `chips` (tags), `text` (note); then `text` (reason) |
| `next_up` | `event`, `start`, `end?`, `where?`, `people[]?`, `note?` | `time`, `people`, `chips` (where), `text` (note) |
| `free_time` | `slots[]` (`start`, `end`, local time, no offset) | `rows`, one item per day ("Tue 28 Oct"), detail the slots ("9:00–10:30 am, 2:00–3:00 pm"), grouped by the date as written |
| `inbox` | `unread`, `needs_reply?`, `threads[]` (`from`, `subject`, `when?`, `flag?` tone) | `value` (unread, hint "3 need a reply"), `rows` |
| `thread` | `from`, `subject`, `ask`, `due?`, `when?`, `excerpt?` | `person` (from, subject), `text` (ask), `facts` (Due), `quote` (excerpt) |
| `mentions` | `groups[]` (`channel`, `items[]` (`from`, `text`, `when?`)) | one `rows` per group, labelled by channel |
| `files` | `files[]` (`name`, `kind?`, `edited?`, `by?`) | `rows` (detail "by Sam", chip kind, when edited) |
| `quote` | `text`, `by?`, `when?` | `quote` |
| `plan` | `steps[]` (`title`, `done?`, `detail?`) | `meter` ("2 of 5 done"), `rows` |
| `decision` | `options[]`, `criteria[]`, `scores[][]` (0–10), `pick?`, `reason?` | `table` (option, each criterion, total; the pick highlighted), `text` (reason) |
| `facts` | `items[]` (`label`, `value`) | `facts` |
| `callout` | `text` (≤200), `tone?` | `text` with tone |
| `goal` | `label`, `value`, `target`, `unit?`, `by?` | `meter` (ring), `value` (formatted, hint "of $6,000 by Nov 30") |
| `sources` | `items[]` (`title`, `site`, `note?`) | `rows` (meta site). No links in phase 1. |
| `custom` | `blocks[]` | the blocks as given |

The kit's leniency stays: a near-miss type name maps to the right recipe ("agenda" →
`timeline`, "next" → `next_up`, "inbox_summary" → `inbox`, "checklist" → `plan`), and
refusals name the widget, field and limit.

**The tool definition stays small.** The schema stays flat. The workspace tools'
description carries a one-line card per type ("next_up: event, start, end?, where?,
people[]?"). A test keeps the whole tool definition under 12,000 characters.

## Tokens and rules

**Tokens** (`app.css`, section "Widget kit", prefix `--w-`), built on the existing
colour and glass tokens, in both themes:

- type: hero value, value, title, body, label, meta;
- spacing: 4, 8, 12, 16;
- tones: good, bad, attention, neutral (fill, text and tint for each);
- series: the existing `--chart-0…4`;
- avatar tints: five, from the series colours at low strength.

**The renderer decides** order, spacing, size and hierarchy. The first top-level
`value` is the hero; text never sits on a colour without the matching tone text
colour; a card's footer is the source and its age.

**Rules she follows** (prompt and style guide):

1. One job per widget. If it needs a paragraph to explain, it is two widgets.
2. A number carries its unit and a comparison when one exists.
3. Titles say what it shows ("Unpaid invoices"), never "Here's your data".
4. Anything from a connector names its source.
5. Empty is said plainly: "Nothing due this week", never invented rows.
6. Round to what matters.
7. Prefer a catalogue widget; make a custom one only when none fits.
8. Asks are for the obvious next question, not decoration; at most two.

## Tap to ask

- An ask is a quiet link at the foot of the card ("Prep me for this ↗"). It never
  looks like a button or like an approval card.
- Tapping sends `turn.text` with `from: "ask"` and exactly the ask's words. It shows in
  the conversation as the user's message.
- The session runs that turn with `via = "ask"` (a new `Via`). `confirm.decide`
  refuses a confirm with `via == "ask"` (new refusal `ASK`: "a tap on a widget can't
  approve; use the card"). Cancelling is allowed.
- While any approval card is waiting, asks are hidden.

## Style guide

- **`#kit`** in the web app: every block and every recipe drawn from the samples, a
  light/dark switch, a narrow/wide switch, and the rules beside them. `#showcase`
  stays as it is.
- **Samples**: `src/trillion/workspace/samples.py` holds recipe inputs (generic
  names only). `scripts/gen_kit_samples.py` validates and expands them into
  `apps/web/src/kit/samples.json`; a test fails when the file is out of date.
- **`docs/design/widgets.md`**: principles, tokens, blocks, recipes and rules, for
  contributors.

## Pieces

### Server

- `workspace/blocks.py` (new): block validation, pure.
- `workspace/recipes.py` (new): one function per type, recipe input → blocks, pure.
- `workspace/widgets.py` (new): `validate_widget`, the one way in: leniency, emoji
  stripping (`workspace/text.py`), `source`, `asks`; today's types checked in
  `workspace/kit.py`, the new ones in `workspace/recipe_kit.py`.
- `workspace/schema.py`: the new fields, flat; the one-line cards.
- `workspace/service.py`: stamps `source.at`; sends boards expanded.
- `spine/contract.py` (`Via`), `spine/confirm.py` (refuse `ask`),
  `api/session.py` (`turn.text` `from: "ask"`).
- Prompts `text.md` and `voice.md`: the catalogue in a line, custom when none fits,
  the rules in short. No em or en dashes.

### Web

- `blocks.ts` (new): one drawing function per block, `textContent` only.
- `when.ts` (new): relative and range formatting, pure.
- `board.ts`: cards draw `blocks`, the footer (source and age), asks; one
  minute timer for the board, cleared on dispose; the per-type drawing moves into
  blocks.
- `kit.ts` (new) and `kit/samples.json`: the style guide page.
- `main.ts`: asks send `turn.text` with `from: "ask"`; hidden while an approval
  waits; the `#kit` route.
- `app.css`: "Widget kit" tokens and `bk-` classes; reduced motion; both themes.

## Errors

A widget the kit refuses changes nothing, and she gets the first problem in plain
words, as today. A block the page does not know (a newer server) is skipped, and the
card still draws.

## Out of scope

- Phase 2 widgets (money, customers, building, work) and phase 3 live refresh.
- Links, images and maps in widgets.
- Tapping a row; dragging or reordering widgets; the user editing a widget.

## Testing

- **Server:**
  - each block's limits;
  - each recipe's expansion;
  - a board saved with today's types loads and expands;
  - emoji stripped;
  - `source.at` is stamped by the server and ignored from her;
  - asks capped at two;
  - a confirm with `via="ask"` is refused and a cancel is not;
  - the samples file is up to date;
  - the tool definition's size.
- **Web:**
  - `when.ts` (countdown, started, past, other day, local versus offset);
  - initials and avatar tint;
  - the hero rule;
  - each block draws its fields with `textContent`;
  - an unknown block is skipped;
  - asks hidden while an approval waits.
- **Live** (Ollama preview, voice off):
  - a next-up card for "a design review with Sam at 3 pm in Maple Park room 2";
  - a plan;
  - a custom "who's on call" card;
  - an ask tap;
  - phone width;
  - the `#kit` page in both themes.
