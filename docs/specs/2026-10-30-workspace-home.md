# Workspace Home

**Date:** 2026-10-30 · **Requirements:** SUR-1a (Home), SUR-1 (the workspace, Your day), ONB-9 (no dead ends), CON-1 (connectors) · **Builds on:** Your day (`persona/day.py`), the widget kit (`docs/design/widgets.md`), Connected tools (`apps/web/src/connected.ts`)

Today an empty workspace says "Ask her to show you something" and offers a sample
board of made-up data. After this, an empty workspace opens to **Home**: a morning
screen that greets the user, offers to build today's briefing, and shows the nine
widgets a briefing can hold. Each one either is ready, because what feeds it is
connected and readable, or says exactly which service would fill it, with one button
that starts that service's sign-in. Home is the first thing the user sees each day,
and it makes connecting the next service the obvious next step.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Home is the empty workspace.** Wherever the board would draw its empty state, Home is drawn instead. Once a board has widgets, the board shows; Home comes back when the board is empty or closed and the workspace is opened. | One place to land, and no new mode to learn. |
| D2 | **Once a day, Home opens by itself.** The first time the page loads on a given local date, the workspace opens to Home. It doesn't open while onboarding is running, when the current conversation's board is open, or when the user has turned it off ("Show Home when I open Trillion", on by default, kept per browser). | The user asked for a morning screen. Later loads leave the orb's stage alone. |
| D3 | **Nine default widgets, in this order:** Next up and Free time (Google Calendar), Inbox (Gmail), Reminders (built in), Mentions (Slack), Due soon (any of Linear, Todoist, Notion, Jira, ClickUp, monday.com, Airtable), Revenue (any of Stripe, PayPal, Square), Recent files (Google Drive), What's broken (any of GitHub, Sentry, Vercel, Supabase). | They are exactly what Your day can read today (one row in `persona/day.py` each), so everything Home promises, the briefing delivers. |
| D4 | **The server decides each tile's state**, never the page: `ready` (a feeding source is set up and readable without asking, per `runtime.day_setup()`), `asks_first` (set up, but its reads still ask each time, so the briefing leaves it out), `connect` (nothing that feeds it is set up), or `built_in` (Reminders). | The rules already live in `day_setup()`; a second copy on the page would drift. |
| D5 | **Every preview is labelled "Sample".** Tiles draw the kit's sample widget of their type (`kit/samples.json`) with the board's own blocks. A ready tile draws it in full colour; a locked tile draws it dimmed behind its connect panel. The live numbers arrive only in the built briefing. | Made-up numbers must never pass for the user's own. |
| D6 | **One button per service, straight into its sign-in.** Connect opens Settings → Connected tools at that service's own setup (Google sign-in, Slack, or the catalogue entry's form), the same flow as tapping its tile there. A tile fed by any of several services shows up to three of their marks as buttons, plus "+N more", which opens Connected tools filtered to that kind. | The fewer steps, the more people connect. Nothing new handles a credential. |
| D7 | **Asks-first gets its own action.** "Let her read it without asking" opens that connection's card in Connected tools (`connected.reveal`), where the setting is. | Connected but not used is the most confusing state; say why and fix it in one tap. |
| D8 | **A meter and one suggestion.** "5 of 9 ready", and the next easiest connection: the first locked tile whose service signs in with one click (Google once set up, Slack, or a catalogue sign-in), else the first locked tile. The line names the service and what it adds ("Connect Slack next: one sign-in adds Mentions"). | One clear next step works better than nine equal asks. |
| D9 | **Build my briefing is Your day.** It opens today's day conversation and builds it, the same call as the Your day row (`day.open()`). When today's day board already has widgets, the button reads "Open today's briefing" and only opens it. | One briefing, one recipe (ONB-3: the page only asks). |
| D10 | **Recent files joins Your day.** `persona/day.py` gains a Google Drive row (`drive_search` with `changed_since` set to yesterday: files changed since yesterday, shown as `files`), and `SetUp` gains `drive`, using the same readable-without-asking test as the other Google services. `drive_search` gains an optional `changed_since` (a date, or a date and time with its offset), and needs `words`, `changed_since` or both. It sees only the files the user picked (the `drive.file` scope), not the whole Drive. | D3: every tile has a row. |
| D11 | **Home refreshes when connections change** (the Connected panel's change callback, and returning from a sign-in) and each time it is shown. It never polls. | It must turn a tile green the moment a sign-in finishes. |
| D12 | **Out of scope:** weather (there is no weather source), choosing or reordering tiles, live numbers on Home, opening Home by voice (asking "what's my day?" already builds the briefing). The sample board stays reachable at `#showcase`, but no button links to it. | Keep it to one screen that works well. |

## The route

`GET /api/workspace/home` (token required, as every `/api` route), in a new
`src/trillion/api/home.py`, registered in `app.py`. It composes its answer in the
pure module `src/trillion/workspace/home.py` from `rt.day_setup()`, what is
configured (Google services signed in, Slack, MCP servers by catalogue id with
their server name), whether the Google app is set up (`ready`), and whether today's day
conversation has a saved board with widgets.

```json
{
  "user_name": "Sam",
  "agent_name": "Trillion",
  "briefing": "build",            // or "open": today's day board has widgets
  "ready": 5, "total": 9,
  "next": {"tile": "mentions", "service": "slack",
           "text": "Connect Slack next: one sign-in adds Mentions"},   // or null when all are ready
  "tiles": [
    {
      "id": "next_up", "title": "Next up", "type": "next_up",
      "what": "Your next meeting, and when to leave for it",
      "state": "ready",            // ready | asks_first | connect | built_in
      "services": [                // what can feed it, catalogue order of D3
        {"id": "google-calendar", "title": "Google Calendar", "color": "#…", "glyph": "…",
         "how": "google", "category": "Email and calendar",
         "state": "ready",         // ready | asks_first | connect
         "settings_key": "google:calendar"}   // the Connected card to reveal; "" when not set up
      ],
      "more": 0                    // services past the three shown
    }
  ]
}
```

- Tile ids are `next_up`, `free_time`, `inbox`, `tasks`, `mentions`, `due_soon`,
  `kpis`, `files`, `checks`, and each tile's `type` is the sample widget it previews
  (the same as its id). Titles and `what` lines are fixed text in `workspace/home.py`:
  - Next up: "Your next meeting, and when to leave for it"
  - Free time: "Open time left in your day"
  - Inbox: "Mail that's waiting on your reply"
  - Reminders: "Your reminders and timed asks for today"
  - Mentions: "Where people need you in Slack"
  - Due soon: "What's due today and tomorrow"
  - Revenue: "Yesterday's sales against the day before"
  - Recent files: "Docs that changed since yesterday"
  - What's broken: "Failing checks and new errors, only when there are any"
- A tile's state is the best state among its services: `ready` over `asks_first` over
  `connect`.
- `settings_key` follows the Connected panel's own keys: `google:<service>`,
  `chat:slack`, or the MCP server's name.
- Brand colours and glyphs are the catalogue's own data, the same values Connected
  tools draws.
- Nothing from a connector's data is read: the route only reports what is set up. It
  makes no network call.

## The page

A new `apps/web/src/home.ts` exports `createHome(opts)` returning `{ el, show(), refresh() }`.
`board.ts`'s `BoardOptions` gains an optional `home?: () => HTMLElement`. When it is
given, the board's empty state is that element, not the old "Ask her to show you
something" box. `main.ts` passes `home.el` and calls `home.show()` each time the
board draws empty.

**Layout** (top to bottom, in the workspace pane, scrolling as one):

1. **Header.** The date ("Friday, October 30"), then "Good morning, Sam" ("Good
   afternoon" from 12:00, "Good evening" from 17:00, local time; without a
   name, the greeting has no name). Under it, one line: "Here's what your briefing can
   show. Connect more and she'll see more." The primary button is "Build my
   briefing" or "Open today's briefing" (D9), with the orb's accent glow.
2. **The meter.** A thin bar filled to `ready/total`, "5 of 9 ready", and D8's line
   with a Connect button for that service. When all nine are ready: "Everything's
   connected", and no button.
3. **The grid.** Nine tiles: three columns from 960 px of pane width, two from 600 px,
   one below that. Each tile has:
   - a head with the title, the `what` line, and a state badge: "Ready" (good tone),
     "Built in", "Needs your OK" (attention tone) or "Not connected";
   - the preview: the sample widget drawn with `drawBlocks`, in a fixed-height frame
     that fades out at the bottom, `aria-hidden`, not focusable, with a "Sample" tag in
     the corner;
   - a foot:
     - ready: the services' marks and "From Google Calendar";
     - asks first: "Gmail asks you each time, so the briefing leaves it out" and a
       button, "Let her read it without asking" (D7);
     - connect: one service gives "Connect Google Drive" with its mark; several give
       up to three mark buttons ("Connect Linear", and so on, as each one's label)
       plus "+4 more" (D6).
   A locked tile's preview is dimmed and desaturated. A ready tile's preview is in
   full colour.
4. **Foot line.** "Want something else on your board? Just ask her." in quiet text.

**Feel.** Use design tokens only (`check_raw_hex`). Brand colours are set from the
route's data, as `mark()` does. Tiles fade and rise in, staggered 40 ms apart, once
per `show()`. Hovering a tile lifts it slightly, and a ready tile's border picks up
its first service's colour. With `prefers-reduced-motion`, none of this moves. Home
uses the workspace's glass surfaces and type scale, like the board's cards.

**Actions.** `main.ts` wires these up:

- Build/Open: calls `day.open()`.
- Connect: calls `openSettings('connected')` and the new `connected.start(catalogId)`,
  which runs that catalogue entry's tile action (Google sign-in view, chat view, or
  setup form). For an entry that's already set up, it reveals its card.
- "+N more": calls the new `connected.browse(category)`, which opens Connected tools'
  home with that category chip chosen.
- "Let her read it without asking": calls `connected.reveal(settings_key)` and then
  `openSettings('connected')`.

`connected.ts` refactors the tile click into one function used by both its tiles and
`start`.

**States.**

- Loading: tiles are drawn as quiet placeholders at their final size, so nothing
  jumps.
- The route fails: the header and Build button stay, and the grid says "Couldn't
  check what's connected." with a Try again button.
- No model provider: Build still sends; the existing `no_model` error explains.

**Accessibility.**

- The grid is a list of tiles, and each tile is labelled "Inbox: ready, from Gmail"
  or "Mentions: not connected. Connect Slack".
- Previews are hidden from screen readers.
- Buttons have visible focus.
- The meter is a `meter` element with text.
- After a refresh that changes a state, the panel's live region says "Mentions is
  ready".

## Platforms

| Part | macOS | Windows | Linux | Browser | Desktop app |
|---|---|---|---|---|---|
| Home, the route, Build | Yes | Yes | Yes | Yes | Yes |
| Once-a-day opening | Yes | Yes | Yes | Per browser (local storage) | Yes (the app's page storage) |
| Connect buttons | Yes | Yes | Yes | Yes | Yes, sign-ins open the default browser as today |

Everything is served by the core and drawn by the page, so it's the same everywhere.
`docs/platforms.md` gains a "Workspace Home" row.

## Testing

- `tests/unit/workspace/test_home.py`:
  - each tile's state from `SetUp` and the configured set: ready, asks first, connect,
    built in;
  - best-of for multi-service tiles;
  - `more` past three;
  - the meter count;
  - D8's choice: one-click first, Google counted as one-click only once its app is
    ready, `None` when all are ready;
  - tile ids match sample types in `samples.py`;
  - every catalogue id named exists in `BY_ID`.
- `tests/unit/persona/test_day.py`: the Drive row appears only when `drive` is set,
  and `day_setup()` sets `drive` only when the Drive reads run without asking.
- `tests/api/test_home_api.py`:
  - the route needs the token;
  - the shape;
  - an MCP server that's on gives that tile ready, and one that's off or disabled
    doesn't;
  - `briefing` is `open` only when today's day board has widgets;
  - no network call.
- `apps/web/test/home.test.ts`:
  - the greeting by hour, and with no name;
  - the badges and feet for each state;
  - "Sample" on every preview;
  - each Connect button calls `onConnect` with the right catalogue id;
  - "+N more" calls `onBrowse` with the category;
  - asks-first calls `onReveal` with the key;
  - Build calls `onBuild`, and its label follows `briefing`;
  - the failure state and Try again;
  - the live-region line after a state changes;
  - reduced motion leaves no animation class.
- `apps/web/test/board.test.ts`: the `home` option replaces the empty state.
- `apps/web/test/connected.test.ts`: `start(id)` opens the same view as tapping that
  tile; `browse(category)` selects the chip.
- `apps/web/test/page.test.ts` or a focused test: the once-a-day opening (first load
  of a date opens Home, the second doesn't, the setting turns it off, onboarding
  holds it).
- `docs/TESTING.md` gains manual checks:
  1. On a fresh home, Home opens with only Reminders ready.
  2. Connect Google Calendar from its tile, and the tile turns ready on return.
  3. Set Gmail to ask each time, and its tile says so.
  4. Build my briefing makes the board, and the button then reads "Open today's
     briefing".

## Notes

- Home doesn't change what she's told. The packs and the day recipe are untouched
  apart from D10.
- The kit samples carry generic names only (`containment_check`).
