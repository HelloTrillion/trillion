# Memories: see, correct and forget what she remembers

**Date:** 2026-11-07 · **Requirements:** MEM-6, with MEM-1, MEM-3, MEM-6a, MEM-6b, MEM-7, SPN-2, SPN-9, SPN-14, SPN-17, SUR-1, SUR-6 · **Builds on:** [memory](2026-10-10-memory.md), [board approvals](2026-10-25-board-approvals.md), [decision record](2026-10-15-decision-record.md)

She remembers things between conversations, and until now the only way to see them was to open the Markdown files. This adds a Memories screen: every memory she holds, where it came from and when, a way to correct one, and a way to forget one.

## Decisions

1. **A section of Settings, not a workspace panel.** Memories are something you look after now and then, like Skills or Reminders, and those are Settings sections. A workspace panel (Helpers, Folders) is for something that moves while you talk. So it is Settings → Memories, in the "Her" group, opened over the orb like every section (SUR-1).
2. **Only current memories are listed.** Retracted and replaced ones stay on disk and in the index, as before; the screen shows what she would recall today.
3. **Editing is a correction, not an overwrite.** The new wording goes through `MemoryBook.write` with `replaces` set to the old id: the same path her own `memory_write` uses. It is saved as `user_statement` whoever said it first, because the user typed it (MEM-6a). The old memory is marked `superseded`, keeps its file and text, and points to the new one (MEM-7).
4. **Forgetting is her `memory_forget`, never a page delete.** There is no route that forgets. The page sends `memory.forget{id}` on its session's socket. The session proposes `memory_forget` through the router exactly as if she had called it, so the same host-composed card appears, and only a yes on that card forgets (MEM-3). Underneath it stays soft: the file is kept, marked `retracted` (SPN-9).

## What the user sees

- Settings → **Memories**, with the count in the rail ("4 remembered").
- A search box. Typing narrows the list on every word, ignoring case and accents; the live region says how many match.
- Groups in a fixed order, each with its count: **About you** (`user`), **How you like things done** (`feedback`), **Projects** (`project`), **Where things are** (`reference`). Newest first inside each. Empty groups are left out.
- Each row: the text; who it came from ("You said this", "From something she read", "Her own guess") and "corrects an earlier one" when it replaced another; when it was saved ("Saved 2 h ago"); and, for a fact that changes on its own, that she says how old it is.
- **Edit** turns the row into a text box. Save (or ⌘/Ctrl + Enter) saves; Cancel (or Escape) leaves it. The line under it says the new wording is saved as something you said and the old one is kept. A refusal (nothing changed, empty, too long, changed elsewhere) is said in the row.
- **Forget** asks. The card appears in the conversation beside Settings (Settings is showing, so the card goes to the transcript and the sidebar opens, as any card does). The row says it is waiting for your yes, and its buttons rest. Approve, and the row goes: "Forgotten. Its file is kept, marked as forgotten." Decline, and it stays: "Kept."
- When the files and the index disagreed at startup, a warning says so (MEM-6b).

## How

**Routes** (`api/memories.py`, behind the session token like every `/api/` route):

| Route | Does |
|---|---|
| `GET /api/memories` | The current memories, newest first, with `max_text` and whether the last sync diverged. |
| `PUT /api/memories/{id}` `{text, version}` | A correction. Refused when the memory is gone (404), changed since the page read it (409, by `version`), or the text is unchanged, empty or too long (400). Answers with the new list and the new id. |

**Forgetting.** `memory.forget{id}` on the socket. The session checks the id's shape, then calls `router.dispatch(memory_forget, {id})` with its own context and `via="tap"` (the user's own act). The router does what it does for any gated call: precheck (a memory already gone raises no card), freeze the arguments, write the pending action and the `proposed` decision, and answer `confirmation_required`. The session sends the card with `_send_card`, the same function her own call uses, and tells the page `memory.asked{id, pending_id, status, reason}`.

Why SPN-2 and SPN-14 still hold:

- **Frozen arguments.** The card's arguments are frozen at proposal, and `memory_forget` carries the memory's version and status as its precondition token: a memory edited between the ask and the yes is not forgotten (SPN-2c).
- **Not on the turn it was proposed.** The card is proposed at the session's current turn. A tap is judged as the next turn; her `confirm_pending_action` in the same turn is refused (SPN-2e).
- **The raising session only.** The card belongs to the session that raised it, so only that page can tap it (SPN-14). It must have been on screen (`confirm.shown`) before it can be approved.
- **Every outcome recorded.** Proposal, approval, refusal and expiry write the decision record like any card (SPN-17).
- **A spoken yes counts the same as for her own call.** Forgetting is recoverable, so a yes or a tap approves it, exactly as for `memory_forget`.

A tap on a card the page raised is answered on the page and starts no turn of hers: she did not ask, so there is nothing for her to report. What changed reaches her with the next message, as any change to memory does (MEM-6b).

**The page** (`apps/web/src/memories.ts`): pure helpers for grouping, search, provenance and outcome words (tested with vitest), and the section itself. Colours come from tokens only; the rows reuse the Connected tools card styles, so light and dark both follow the theme.

**Keyboard and screen readers (SUR-6).** Every control is a real button or field. Each group is a labelled list. Edit and Forget carry the memory's words in their labels. Escape inside an edit leaves the edit, not Settings. Counts, outcomes and refusals go to one polite live region. After a save, focus returns to the row's Edit button; after a forget, it goes to the search if it was on the row that went.

## Checked

- API: the token is required; the list is current-only, newest first, with source and dates; an edit is a `user_statement` that supersedes the old memory and keeps its file; bad edits are refused plainly; there is no forget route; forget from the socket raises the `memory_forget` card, runs only on a tap, leaves the file marked `retracted`, writes the decision record and calls no model; declining forgets nothing; a memory already gone raises no card.
- Web: grouping, order, search, provenance, dates and outcome words.
- In a browser preview with a throwaway home and made-up memories, Voice off.

## Not now

- Showing a memory's history (what it replaced, what was forgotten). The data is kept; the screen shows only what is current.
- Changing a memory's group or the "changes on its own" flag from the page.
- Forgetting several at once.
- A file edited while Trillion is running is still picked up at the next start.
