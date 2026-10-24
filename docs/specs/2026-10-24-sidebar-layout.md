# Sidebar, conversation history and a docking orb

**Date:** 2026-10-24 · **Requirements:** SUR-1 (threaded chat view, subtitle overlay, Ask bar ⌘K), SUR-6, GAL-5 · **Respects:** SPN-9 (soft delete), SPN-5 (untrusted text), BRN-4 (usage), never-list #11 (one writer) · **Leads into:** SUR-1's activity panel, SUR-7, GAL-8 (energy arcs to UI chips)

The conversation floats in a narrow column under the orb, and there is nowhere to
see or reopen older conversations. This moves the conversation into a left
sidebar with a thread list, gives the orb the stage to its right, and adds a
**workspace**: a slot over the stage where other things open while the orb docks
small and keeps reacting. Settings is the first thing that opens there.

Voice stays first. The orb is the largest thing on screen whenever the workspace
is empty, and the sidebar can be hidden entirely.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **One left sidebar** holds the conversation, with a thread switcher at its top. The orb owns the rest. | What the user chose (layout B). Familiar, and the conversation stays visible without crowding the orb. |
| D2 | **This slice is layout plus history.** What else fills the workspace later (activity, agents, documents) is its own design. | What the user chose. The sidebar is useful from day one. |
| D3 | **Settings opens in the workspace**, not over the whole screen. | What the user chose. It makes docking real, and the user can talk to her while changing a voice. |
| D4 | **She names conversations.** The title is the user's first words at once, then a 3–6 word title from the model after her first reply. A rename by the user is never overwritten. | What the user chose. |
| D5 | Delete is a **soft delete** with an undo. | SPN-9: nothing the user said is hard-deleted by a click. |
| D6 | Switching conversations uses the **existing resume path** (`hello{resume}`). | One way to open a conversation, already tested for reloads and second tabs. |
| D7 | The orb's place on screen is a **Galaxy call**, not CSS on the canvas. | One WebGL context stays full-window behind everything (GAL-4); only the orb moves. |

## What the user sees

### Desktop (768px and wider)

- **Sidebar, left, 360px by default.** Its edge can be dragged from 300px to 520px; the width is remembered per device.
  - **Header row:** the agent's name, the connection status and the usage pill (all moved from the top bar), and buttons to hide the sidebar and open Settings.
  - **Title row:** the current conversation's title with a chevron, and a pencil button for a new conversation (this replaces "New conversation" in the top bar). A new, empty conversation reads "New conversation".
  - **Messages:** the transcript as today: her replies, the user's lines, tool chips, approval cards, reminders, quick replies, the live "hearing" line and the thinking line. It scrolls on its own; new lines keep it pinned to the bottom unless the user has scrolled up.
  - **Ask bar** at the bottom, with the mic, send and stop buttons, as today.
- **Thread list.** Clicking the title, or ⌘K, swaps the messages for the thread list. Escape or picking a thread swaps back.
  - A search box (focused on open), then conversations newest first, grouped "Today", "Yesterday", "Previous 7 days", "Older".
  - Each row: the title, and the time of its last message. The open one is marked.
  - A row's menu (⋯, or right-click) has **Rename** (edit in place, Enter saves, Escape cancels) and **Delete**.
  - Delete removes the row and shows "Conversation deleted · Undo" for 8 seconds. Deleting the open conversation starts a new one.
  - Search matches titles and the words of messages, case-insensitive. No match reads "No conversations match".
  - Conversations with no messages are not listed.
  - The list loads 50 at a time; scrolling to the end loads more.
- **Hiding the sidebar.** Its button, or ⌘\\, hides it completely; a small button at the top-left brings it back. Remembered per device. While it is hidden, **captions** show under the orb: the line she is saying, and what she is hearing while the user speaks, two lines at most, fading after 4 seconds of quiet. Her approval cards still need to be seen, so a card arriving while the sidebar is hidden opens it.
- **Stage.** The galaxy stays full-window. The orb centres in the space to the right of the sidebar, at today's size scaled to that space. It glides when the sidebar opens or closes (300 ms), and jumps with reduced motion.
- **Workspace.** A panel over the stage, inset 16px, with its own title and a close button (and Escape). While it is open, the orb glides to a **dock** 96px across, 24px in from the bottom-right corner, over the workspace's edge. Docked, it keeps every state, colour and voice and mic reaction. Clicking the docked orb closes the workspace.
- **Settings in the workspace.** The settings screen is the same sections, rail and search as today, drawn inside the workspace instead of over the window. Everything that opens a section today (the gear, onboarding's steps, a "change voice" link) opens it there. The conversation and ask bar stay usable beside it.

### Phone (under 768px)

- The sidebar becomes a **bottom sheet** over the orb with three rests: collapsed (the ask bar only), half, and full. Dragging its handle or tapping the title moves it. Collapsed is orb-first.
- The workspace takes the full screen with the orb docked at the top centre, 64px across, and a close button.
- The thread list opens over the sheet at full height.

### Keyboard and screen readers (SUR-6)

- ⌘K (Ctrl+K elsewhere): the thread list. ⌘\\: hide or show the sidebar. Escape: close the thread list, then the workspace.
- The sidebar is an `<aside aria-label="Conversation">`; the thread list is a `listbox` with arrow-key movement; the workspace is a `region` with its title as its label and focus moves into it when it opens and back when it closes.
- The orb's state is still announced in the live region, wherever the orb is.

## Where things live

| Piece | File | Change |
|---|---|---|
| Titles, listing, rename, delete | `src/trillion/storage/sqlite/conversation_store.py` | New methods; the one writer of the new columns |
| Migration | `src/trillion/storage/migrations/010_conversation_titles.sql` | New |
| Naming a conversation | `src/trillion/brain/title.py` | New |
| Conversations API | `src/trillion/api/conversations.py` | New router |
| Title after the first reply | `src/trillion/api/session.py` | Starts the naming task; sends `conversation.title` |
| One-writer table | `scripts/check_one_writer.py` | Lists the new columns |
| Orb placement | `packages/galaxy/src/` | New `setStage` in the public API |
| Page shell | `apps/web/index.html`, `apps/web/src/app.css` | New layout |
| Sidebar and thread list | `apps/web/src/sidebar.ts`, `apps/web/src/threads.ts` | New |
| Workspace and orb docking | `apps/web/src/workspace.ts` | New |
| Captions | `apps/web/src/captions.ts` | New |
| Settings host | `apps/web/src/panel.ts` | Draws into the workspace instead of a full-window dialog |
| Wiring | `apps/web/src/main.ts`, `apps/web/src/view.ts` | Title and switching state |

## Pieces

### Storage: `010_conversation_titles.sql` and the store

```sql
ALTER TABLE conversations ADD COLUMN title TEXT;
-- 'words': the user's first words; 'model': she named it; 'user': the user renamed it.
ALTER TABLE conversations ADD COLUMN title_source TEXT
    CHECK (title_source IN ('words', 'model', 'user'));
-- Epoch ms of the newest message; NULL until the first one. Lists sort on it.
ALTER TABLE conversations ADD COLUMN last_message_at INTEGER;
CREATE INDEX conversations_by_recent ON conversations (owner_id, deleted_at, last_message_at DESC);
```

Existing rows are backfilled in the same migration: `last_message_at` from their
newest message, and `title` from the first 60 characters of their first `user`
message with `title_source = 'words'`.

`ConversationStore` gains:

- `append` / `append_many` also set `last_message_at`, and set a `'words'` title from the first user message when there is no title yet.
- `set_title(conversation_id, owner_id, title, source)`: a `'model'` title never replaces a `'user'` one (checked in the `UPDATE`'s `WHERE`, so a rename racing the naming task cannot lose).
- `list(owner_id, *, query=None, before_ms=None, limit=50)` → rows of `id`, `title`, `last_message_at`, newest first, excluding deleted conversations and those with no messages. `query` matches `title`, or the `text` of a text block in a `user` or `assistant` message (`json_each` over `blocks_json`, so JSON keys never match), with `LIKE`, escaped, case-insensitive. Tool results are not searched.
- `delete(conversation_id, owner_id, now_ms)` sets `deleted_at`; `restore(conversation_id, owner_id)` clears it.
- `exists` treats a deleted conversation as absent, so a stale tab cannot resume one.

Titles are trimmed, collapsed to one line, and cut at 80 characters.

### Naming: `brain/title.py`

`async def name(provider, messages) -> str | None` asks the conversation's own
model for a title from the user's first message and her first reply:

- The conversation reaches the model wrapped as untrusted text (`spine.untrusted.wrap`), with one instruction: a 3–6 word title in sentence case, no quotes, no ending punctuation.
- No tools, a 24-token limit, a 10-second timeout.
- Its usage is recorded against the conversation like any model turn (BRN-4).
- The answer is cleaned (quotes, trailing punctuation, newlines stripped; empty or longer than 80 characters rejected). Any failure returns `None` and the `'words'` title stays. Nothing is shown or spoken about it.

It is not part of the history and the model never sees it again.

### Session: when naming runs

After the turn that completes **her first reply** in a conversation, the session
starts `name()` as a background task (one per conversation; never for a resumed
conversation that already has a `'model'` or `'user'` title). It does not delay
or block the next turn. On success it saves with `set_title(..., 'model')` and
sends `conversation.title{id, title}` to every page open on that conversation.
`session{id}` gains `title` (null for a new conversation).

### API: `api/conversations.py`

Same token, origin and JSON rules as every other route (SPN-11).

- `GET /api/conversations?q=&before=&limit=` → `{conversations: [{id, title, last_message_at}], more: bool}`
- `PATCH /api/conversations/{id}` `{title}` → renames with source `'user'`. An empty title is `400 bad_request`.
- `DELETE /api/conversations/{id}` → soft delete. Pages open on it get `error{code: 'deleted'}` and start a new conversation.
- `POST /api/conversations/{id}/restore` → undo.
- An unknown or other owner's id is `404`.

### Galaxy: `setStage`

```ts
interface StageRect { x: number; y: number; size: number } // CSS px: centre and diameter
setStage(rect: StageRect, opts?: { animate?: boolean }): void
```

The orb is drawn centred on `(x, y)` at `size` across, through the camera's view
offset and distance, so the background landscape does not move. It eases over
300 ms (`animate` defaults to true; false, or reduced motion, jumps). Without a
call, it behaves as today: centred in the canvas. `destroy`, theme and perf mode
are unaffected. The Galaxy demo page gets controls for it.

### Web app

- **`sidebar.ts`**: the aside, its header, title row, resize handle and hidden state. It owns the sidebar's width and visibility prefs and reports the stage rectangle (the window minus the sidebar) whenever it changes.
- **`threads.ts`**: the thread list. A pure part (grouping by day, search debounce, which rows to show) tested without the DOM, and a thin DOM part. It calls the API and updates its rows on `conversation.title`.
- **`workspace.ts`**: open(title, content) / close(), the region, focus, and the orb's rest or dock rectangle passed to `galaxy.setStage`. A `ResizeObserver` keeps the orb placed as the window or sidebar changes.
- **`captions.ts`**: shown only while the sidebar is hidden; it reads the same view state the transcript does.
- **`panel.ts`**: `createPanel` draws into a workspace host instead of a full-window `<dialog>`. Sections and their `Host` are unchanged, so onboarding still opens them.
- **Switching** a conversation: if she is replying or speaking, stop her first (as "New conversation" does now), store the id, reconnect with `resume`. The transcript clears and the core sends its `history`.

## Errors

- The list fails to load: "Couldn't load your conversations. Retry" in the list; the open conversation is unaffected.
- A rename or delete fails: the row returns to what it was, with "Couldn't rename it" or "Couldn't delete it".
- Resuming a conversation that was deleted elsewhere: the core starts a new one, as it does for an unknown id.
- Naming fails: the first-words title stays, silently.

## Out of scope

- Anything in the workspace other than Settings: the activity panel, agents, documents, the Confirmations view.
- Pinning, folders, archive, export, or sharing conversations.
- Searching inside tool results or by date.
- Moving the orb by dragging it.

## Testing

- **Store** (`tests/storage/`): titles from first words; a model title never overwrites a user one, including when they race; listing order, paging with `before`, search over titles and message text with `%` and `_` in the query; deleted and empty conversations not listed; restore; the migration backfills existing rows.
- **Naming** (`tests/brain/test_title.py`, fake provider): cleaning; overlong, empty and failing answers give `None`; the conversation goes in wrapped; usage recorded.
- **Session** (`tests/api/`): naming runs once after the first reply and not before; `conversation.title` reaches every page on that conversation; a resumed conversation with a title is not renamed; the next turn is not held up by a slow namer.
- **API** (`tests/api/test_conversations.py`): each route, auth, 404 for another owner, rename to empty is refused, delete then resume starts a new conversation.
- **`check_one_writer.py`** passes with the new columns listed.
- **Galaxy** (`packages/galaxy/test/`): `setStage` places the orb's projected centre and size within a pixel; reduced motion jumps; the visual test gets a docked snapshot.
- **Web** (vitest): thread grouping by day across midnight and timezones; the stage and dock rectangles for desktop, hidden sidebar and phone widths; captions only while the sidebar is hidden; a card arriving while it is hidden opens it.
- **Live** (preview on 7790, voice off per the no-sound rule): switch between three conversations; rename and delete with undo; a new conversation gets a model title after the first reply; open Settings and see the orb dock and keep reacting while typing a message; hide the sidebar and see captions; phone width.
