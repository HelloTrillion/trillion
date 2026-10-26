# Live widgets

**Date:** 2026-10-25 · **Requirements:** SUR-1, SPN-5, CON-5 · **Builds on:** `docs/specs/2026-10-25-widget-design-system.md`, `docs/specs/2026-10-25-widget-packs.md`, `docs/design/widgets.md`

Phase 3 of the widget design system. A widget she built from a connector keeps
itself fresh while the user is looking at the board: the server replays the reads
she made for it, and a small, fenced model step writes the new numbers into it.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Refresh only while the board is on a screen,** plus a catch-up when it is opened. | What the user chose: no cost while away, fresh when looked at. |
| D2 | **Replay, then fill in.** The server re-runs the exact read calls she made for the widget; a separate model call with no tools turns the fresh results into the widget's data fields. | What the user chose: works for every connector, one small model call per refresh. |
| D3 | **The fill-in changes data only.** Type, title, size, source and asks are kept from the widget as it was; the result is checked by the kit like any widget. | Fresh results are untrusted (SPN-5); the blast radius is the numbers on one card, the same trust as the original snapshot. |
| D4 | **Replay is reads only.** Only calls that `runs_unattended` allows (not gated, not workspace, not schedule), and never `web_fetch`; the router refuses anything else under the new `via = "refresh"`. | A refresh happens with nobody deciding. |
| D5 | **Refreshes stay out of the conversation.** No messages are saved; model usage is recorded like any turn. | The conversation is what was said. |

## What a live widget remembers

- During a turn, the session records each **eligible read** that succeeded: tool name, the arguments as called, and the connector it belongs to (an MCP tool's server title; `gmail_*` → "Gmail", `calendar_*` → "Google Calendar", `drive_*` → "Google Drive", `slack_*` → "Slack"). Eligible: `runs_unattended(definition)` is true, risk is READ, and the tool is not `web_fetch`, `describe_self`, a memory tool, or a workspace tool.
- When `workspace_show` or `workspace_update` saves a widget with a `source`, the service attaches `live` from this turn's reads whose connector matches the source by the same rule as source tiles (title or short name, ignoring case): `{"reads": [{"tool", "args"}], "every": minutes}`, at most **5** reads (the last five). No match: no `live`, the widget stays a snapshot.
- `live` is the server's: she cannot write it (the kit drops it), and the page never receives the reads (only `live: {"every", "state"}`).
- She may set `refresh` on a widget: `"off"`, or minutes (2 to 1440). Defaults by type:
  - **2:** `next_up`, `inbox`, `mentions`, `checks`, `deploys`, `services`, `support_queue`;
  - **15:** the money and customers packs, `kanban`, `sprint`, `tasks`, `due_soon`, `kpis`;
  - **60:** everything else.
- A widget she updates by hand (`workspace_update`) gets its `live` rebuilt from that turn's reads (or keeps the old one when the turn read nothing matching).

## How a refresh runs

- **Seeing.** The page sends `board.seen {on: true|false}` when the board is shown or hidden and when the tab becomes visible or hidden. The server keeps, per conversation, whether any page sees its board.
- **Due.** A widget is due when it has `live`, `refresh` is not off, and its `source.at` is older than `every`. On `board.seen on`, every due widget is queued at once (catch-up).
- **One at a time.** A single runtime task works the queue: at most one refresh in flight, at most **60 per hour** across the runtime (a rolling window; the rest wait), and nothing for a conversation no page sees.
- **Replay.** Each saved read runs through the router with `ToolContext(via="refresh", turn=<a refresh counter>, …)`. The router, for `via == "refresh"`, refuses any tool that `runs_unattended` refuses, any non-READ tool, and any confirmation; the deny floor and timeouts apply as always. Results are wrapped as untrusted.
- **Fill-in.** One model call (`workspace/fill.py`, shaped like `brain/title.py`): a system instruction ("Update this widget's data from the fresh results. Answer with the widget as JSON, same type and fields; keep anything the results don't change."), the widget as she wrote it (her recipe input), and the fresh results wrapped as untrusted. No tools. A timeout of 20 s; output capped.
- **Merge.** The answer is parsed as JSON. The server keeps `type`, `title`, `size`, `source.name`, `asks`, `refresh` and `live.reads` from the old widget and takes the rest from the answer, validates it with `validate_widget`, saves it in place (same id and position) with a new `source.at`, and sends the board.
- **Failure.** Any failure (a read refused or failing, the model, bad JSON, the kit refusing) keeps the old data and records `live.failed` (count and when). The footer says "Couldn't refresh · 12 min ago". After **3** failures in a row the widget is not refreshed again until the board is opened anew (`board.seen on` resets the count).
- **Busy.** While her own turn is running on that conversation, refreshes for it wait.
- **Off.** A Settings switch "Keep widgets fresh" (on by default) stops all refreshes.
- **Usage.** The fill-in's usage is recorded with the conversation like a turn's, so the cost line includes it.

## What the user sees

- A live card's footer: tile, "Stripe · live · 1 min ago". While it refreshes: a small dot beside "live" pulses (none with reduced motion). After failures: "Couldn't refresh · 12 min ago" in the attention tone.
- A refresh button beside the × on live cards: refreshes that card now (counts toward the hourly cap; ignored while one is in flight for it).
- Settings → (the existing preferences place) "Keep widgets fresh".
- Her prompt: one line that widgets she builds from a connector keep themselves fresh while the board is open, and that she can set `refresh` to "off" or minutes when asked.

## Protocol

- Page → server: `board.seen {on}`, `board.refresh {id}`.
- Server → page: `board` as today (widgets carry `live: {every, state}` with `state` `idle | refreshing | failed`, and `failed_at` when failed); `board.refreshing {id, on}` for the pulse.

## Out of scope

- Refreshing with no page open; push from services (webhooks).
- Built-in feeds without a model.
- Refreshing snapshot widgets (no source, or no matching reads), and widgets saved before this change.

## Testing

- **Server:**
  - recording: only eligible reads, matched by source, at most 5, never writes or `web_fetch`; `live` dropped from her input; `refresh` read and defaulted by type;
  - router: `via="refresh"` refuses gated, non-READ, workspace and schedule tools and any confirmation;
  - fill-in: keeps type, title, size, source name, asks and reads; a bad answer keeps the old data and counts a failure;
  - queue: due only while seen; catch-up on seen; one in flight; the hourly cap; three failures stop it; a running turn makes it wait; the Settings switch stops it;
  - nothing is added to the conversation; usage is recorded.
- **Web:** `board.seen` on show, hide and tab visibility; the footer for idle, refreshing and failed; the refresh button sends `board.refresh`; the page never shows reads.
- **Live** (the user's Claude preview): a widget from a connected service updates after the data changes in that service, with the board open; nothing refreshes with the board closed.
