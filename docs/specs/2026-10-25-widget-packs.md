# Widget packs: money, customers, building, work

**Date:** 2026-10-25 · **Requirements:** SUR-1, CON-5 · **Builds on:** `docs/specs/2026-10-25-widget-design-system.md` (read it first; this spec only adds to it)

Phase 2 of the widget design system. It adds widgets for what the catalogue's
connectors hold (payments, deals, errors, issues) and the few blocks they need.
To keep her toolkit small, she is told about a pack of widgets only when a
connector of that kind is connected.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Packs follow connections.** The workspace tool's description lists the core widgets always, and a pack's widgets only while a connector in that pack is connected. | What the user chose: the toolkit text costs on every turn and small models do worse with more. |
| D2 | **Packs change what she is told, never what is accepted.** The kit takes every widget type at all times. | Numbers she was told can still become a pipeline; old boards keep loading. |
| D3 | **Widgets are generic, named by what they show** (`transactions`, `pipeline`), not by service (`stripe_payments`). | One widget serves every service of its kind. |
| D4 | **A source names its connector by its catalogue title** ("Stripe"), and the footer shows that connector's tile when it is connected. | Trust: the user sees where the numbers came from. |

## New blocks

Fields and limits follow the design system's conventions (labels 60, values 40,
tones `good|bad|attention|neutral`).

| Block | Fields | Limits and notes |
|---|---|---|
| `bars` | `items[]` (`label`, `value` number, `note?`), `unit?`, `tone?` | 1–10 items, values ≥ 0 and finite; drawn in the order given, each bar scaled to the largest; the value written with `unit` like `formatValue` does. |
| `funnel` | `stages[]` (`label`, `count` number, `value?`) | 2–6 stages, counts ≥ 0; the page shows "55% move on" between stages (the next count over this one, rounded; nothing when this one is 0). |
| `stages` | `steps[]` (labels), `current` (a step's label or its 0-based index), `tone?` | 2–7 steps; steps before `current` drawn done, `current` ringed, the rest open. |

**Rows** gain `amount` (text ≤40, like a value) and `amount_tone?` on an item.
The amount sits on the right in tabular figures; when the item also has `when`,
the time is written under the amount. `meta` and `chip` are not drawn when there
is an amount.

## New widgets

Every widget also takes `title?`, `size?`, `source?`, `asks?`, and gets a default
title as in phase 1 when it has none.

### Core (always told)

| Type | She writes | Blocks |
|---|---|---|
| `kpis` | `metrics[]` (`label`, `value`, `delta?`, `tone?`, `trend[]?`) | `columns`, one per metric (2–4): `value` (small, never the hero), `sparkline` when `trend` has 2+ points |
| `tasks` | `tasks[]` (`title`, `status?`, `due?`, `owner?`, `priority?`, `done?`) | `rows` (person owner, status tone from words like "blocked"/"in progress"/"done", `when` due, `chip` priority) |

### Money (Stripe, PayPal, Square)

| Type | She writes | Blocks |
|---|---|---|
| `transactions` | `items[]` (`who`, `amount`, `what?`, `status?` (`paid\|failed\|refunded\|pending`), `when?`), `total?` | `value` (total, when given), `rows` (person who, detail what, amount toned: refunded and failed `bad`, pending `attention`) |
| `invoices` | `items[]` (`who`, `amount`, `due`, `status?` (`overdue\|due\|paid`)) | `rows` (title who, amount, `when` due, overdue toned `bad`); a `value` with the overdue total when any are overdue |
| `breakdown` | `items[]` (`label`, `value`), `unit?` | `bars` |
| `payouts` | `items[]` (`amount`, `when`, `note?`), `total?` | `value` (total), `rows` |

### Customers (HubSpot, Intercom, Nitrosend, Resend)

| Type | She writes | Blocks |
|---|---|---|
| `pipeline` | `stages[]` (`label`, `count`, `value?`) | `funnel`, then `facts` with the total value when values are given |
| `deal` | `name`, `amount?`, `stages[]`, `current`, `owner?`, `next?`, `close?` | `value` (amount), `stages`, `facts` (Owner, Next step, Closes) |
| `contact` | `name`, `role?`, `company?`, `facts[]?` (`label`, `value`), `activity[]?` (`title`, `when?`) | `person`, `facts`, `rows` (activity) |
| `campaign` | `sent`, `delivered?`, `opened?`, `clicked?`, `replied?` (numbers) | `funnel` from the numbers given, in that order |
| `support_queue` | `open`, `waiting[]?` (`who`, `subject`, `since`), `first_reply?` | `value` (open, label "Open"), `rows` (oldest first, `when` since), `facts` (First reply) |

### Building (GitHub, Sentry, Vercel, Supabase)

| Type | She writes | Blocks |
|---|---|---|
| `checks` | `items[]` (`title`, `state` (`passing\|failing\|running\|pending`), `who?`, `when?`) | `rows` (status from state: passing `good`, failing `bad`, running and pending `attention`) |
| `errors` | `trend[]?`, `issues[]` (`title`, `events`, `users?`) | `sparkline` (trend), `bars` (issues by events, note "N users") |
| `deploys` | `items[]` (`title`, `state` (`ready\|building\|failed\|cancelled`), `branch?`, `when?`) | `rows` (status from state, chip branch) |
| `services` | `items[]` (`name`, `state` (`up\|degraded\|down`), `note?`) | `rows` of `status`-toned items; a `status` line on top ("All up" or "1 down") |

### Work (Linear, Jira and Confluence, Todoist, ClickUp, monday, Notion, Airtable)

| Type | She writes | Blocks |
|---|---|---|
| `kanban` | `columns[]` (`name`, `count?`, `items[]` (titles, up to 5)) | `columns` (2–4), each a `rows` labelled "name · count" |
| `sprint` | `name?`, `done`, `total`, `ends?`, `burndown[]?` | `meter` ("12 of 20 done"), `chart` line (burndown, when given), `facts` (Ends) |
| `due_soon` | `items[]` (`title`, `due`, `owner?`) | one `rows` per day ("Today", "Tomorrow", "Thu 29 Oct"), grouped by the due date as written |

Near-miss names map as in phase 1 (`payments`→`transactions`, `bills`→`invoices`,
`funnel`→`pipeline` unless it has `sent`, `ci`/`builds`→`checks`, `issues`→`errors`
when it has `events`, `board`→`kanban`, `burndown`/`cycle`→`sprint`, `todo`/`todos`
→`tasks`).

## Packs

- `src/trillion/workspace/packs.py`: `PACKS = {"core": …, "money": …, "customers": …,
  "building": …, "work": …}` (type names) and `PACK_OF = {catalog id: pack}`:
  stripe, paypal, square → money; hubspot, intercom, nitrosend, resend → customers;
  github, sentry, vercel, supabase → building; linear, atlassian, todoist, clickup,
  monday, notion, airtable → work. Others belong to no pack.
- `told(connected_catalog_ids) -> list[str]`: core plus every pack with a connected
  member, in the order above.
- The workspace tools' description is built from `told(...)`: the type cards for
  those types, and every block card (blocks are always available for custom).
- **Changing what she is told.** The registry snapshots a definition when a tool
  is registered. It gains `redefine(name, definition)`, validated exactly like
  `register` and allowed only for a tool already registered under that name, with
  the same risk and category. The runtime calls it for the workspace tools at start
  and whenever a server is connected, disconnected or removed.
- Budgets (tests): the four workspace tool definitions under **12,000** characters
  with no packs, under **16,000** with every pack.

## Source tiles

- The page already has the catalogue's entries (`connected.ts`: id, title, colour,
  glyph). A card whose `source.name` equals, ignoring case, the title of a
  connected entry shows that entry's tile before the name in the footer. No match:
  the name alone, as today.
- Prompt: name connector data by the connector's title ("Stripe", "Linear").

## Samples, style guide, prompt, docs

- `samples.py` gains one sample per new type and a block sample for `bars`,
  `funnel` and `stages`; `gen_kit_samples.py` writes each widget's pack too
  (`"pack": "money"`), and `#kit` shows it in the caption ("transactions · money ·
  medium").
- Prompt (`text.md`, `voice.md`): one line: more widget types are listed in the
  workspace tool when a matching connector is connected; she uses what is listed.
- `docs/design/widgets.md`: the new blocks, the packs table, how to add a pack.

## Out of scope

- Live refresh (phase 3).
- Connector-specific fetching: she reads with the connector's own tools and writes
  the numbers, as in phase 1.
- Logos: tiles use the catalogue's colour and letters only.

## Testing

- **Server:**
  - each new block's fields and limits;
  - each new widget's expansion;
  - the aliases;
  - `told()` for no connections, one per pack, and all;
  - `redefine` refuses an unknown name and a changed risk or category;
  - the description changes when a server connects and disconnects;
  - a pack widget is accepted with no pack connected;
  - both size budgets;
  - the samples are up to date.
- **Web:**
  - bars scaled to the largest;
  - funnel percentages, including a 0 stage;
  - stages done, current and open by label and by index;
  - an amount with a time under it;
  - the source tile shows only for a connected entry's title;
  - `#kit` captions show the pack.
- **Live** (the user's Claude preview): KPIs, a pipeline and an errors card from given numbers; with a catalogue server connected, the matching pack's widget shows up and its footer has the tile.
