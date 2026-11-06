# Your helpers: specialists you make under her

**Date:** 2026-11-05 · **Requirements:** AGT-1, AGT-2, AGT-3, AGT-7, AGT-8, SPN-14, SUR-9 · **Builds on:** [helpers panel](2026-11-05-helpers-panel.md), [routines](2026-10-27-routines.md), [spend caps](2026-11-04-spend-caps.md) · **Reference:** Grok Bot's Bots (docs.x.ai/grok-bot)

The helpers panel shows her background work as helpers. This is step two. You make helpers of your own, each a specialist with one job and a chat of its own. Trillion stays your one contact. She makes them, hands them work, and offers one when a need keeps coming back.

Setup is almost nothing. One sentence is enough: "I want a helper for my invoices." She drafts it at once, from your words and what she knows of you: its name, job and notes, the connections it would use, and what it will ask you first. The draft is one card pinned above the message box; keep talking and she updates that same card. You tap Create, and its chat opens. Everything after that, it learns from what you tell it.

## Decisions

1. **Two ways in, one path.** Ask her, or tap **+** in the sidebar's rail (or **+ New helper** in the Helpers panel) and type one sentence. The button sends that sentence to her, so both ways end on the same card.
2. **It uses everything she can.** It has the same tools, connections and shared folders, under the same rules: anything that changes something still asks on a card (SPN-14). Per-helper permissions are out of scope.
3. **She hands work off.** When you ask her something that is a helper's job, she says "Asking Invoices", the helper works in its own chat in the background, and the answer comes back to her conversation as one line with Open.
4. **She suggests once.** When the same kind of need comes up on 3 different days within 14 days, and no helper covers it, she offers one in a single line with Create. A no is kept, and she never offers that one again. Your own request always wins: no offer comes on a turn where you ask for a helper, or while her draft of one waits.
5. **Only she dispatches (AGT-2).** A helper can't create helpers, hand work on, or change another helper. That avoids the bot-to-bot noise Grok Bot users complain about.
6. **A whole helper pauses.** Pause stops its handoffs and its routines together. Grok Bot can't pause a whole bot.
7. **She drafts, then you talk.** On a request she drafts in the same turn, with her best guess, and never questions you first: what the helper must know, it asks in its own chat. Anything you add updates the one card; nothing is lost by talking on.

## What a helper is

Migration `019_helpers.sql` adds:

- `helpers`: `id`, `owner_id`, `name` (at most 40 characters, unique per owner, case-insensitive), `job` (one line, at most 200), `notes` (what it has learned from you, at most 1,000; AGT-7), `conversation_id`, `paused_ms`, `created_at`.
- `schedules.helper_id` (nullable): a routine or timed ask that belongs to a helper.
- Migration `020_helper_draft.sql` adds `helpers.connectors` and `helpers.asks`, JSON arrays of text (default `[]`): the Connected tools entries its card suggested, by catalog id, and the questions it asks first (at most 4 and 3).
- conversation kind `'helper'`: a helper's own chat, titled with its name and never renamed by naming.

At most 20 helpers. At the limit, she says so and suggests one to delete.

**Its voice.** Every turn in a helper's chat, and every run for it, gets an overlay on her usual instructions: "You are {name}, one of Trillion's helpers. Your job: {job}. Follow these notes from the user: {notes}." Her own identity, rules and memory stay underneath. A helper is her, focused, not a stranger.

**Its tools** are hers, minus `helper_create`, `helper_ask`, `helper_update` for another helper, and `helper_suggest` (AGT-2). The router refuses them by name to a helper's conversation and to its runs, whether or not they were offered.

## Creating one

- **Asking.** She calls `helper_create {name, job, notes?, connectors?, asks?, icon?, color?}`, a carded tool, in the same turn you ask, with her best guess. She never asks clarifying questions first.
  - `notes`: what it should know, from your words and what she knows of you (platforms, goals, audience, tone, cadence, limits), plain sentences on one line, cut at 1,000 characters.
  - `connectors`: up to 4 Connected tools its job would use, by the featured catalog's ids (`connectors/mcp/catalog.py`); the tool's description lists them with their titles. An id not in the catalog is dropped, never refused. When the job needs something the catalog lacks, she says so in one line and names the closest entries or a custom connection in Settings, Connected tools.
  - `asks`: up to 3 short questions (each at most 160 characters) it asks you first, in its own chat, instead of her asking in hers.
  - She ends her reply with a short invitation ("Anything to add? Tell me and I'll update the card.") and never says it is made before Create.
- **One live draft.** The card is pinned above the message box of that conversation, not on the board: its orb, name, job, notes (three lines, with More), suggested connections as chips (one not set up opens Settings at it) and the questions it will ask, with **Create** and **Not now**. They are the card's Approve and Decline, sent as every card's are: no new way to approve. In the conversation, a line ("New helper: Socials", View) goes to it. It works at phone width.
- **Talking updates it.** While it waits, a new `helper_create` in that conversation replaces it: every draft's card has the same target (`helper:draft`), so the router retires the waiting one as superseded in the same transaction that saves the new one, as it does for any card with the same target. There is never more than one, and the old one can't be approved. The page puts the new draft where the old one was, and what changed glows for a moment.
- **The + button.** The **+** at the foot of the rail, and **+ New helper** in the Helpers panel, open the same one field: "What should it do?" Submitting sends "New helper: {text}" to her as your message in the main conversation, the same as typing it. She drafts it and raises the same card.
- **On Create:** the helper and its chat are made, with the draft's notes, connections and questions. The pinned card goes, its chat opens (the rail shows its picture, selected; no panel opens), and the helper's first message is one line saying what it'll do and asking anything it must know: "I'll keep an eye on unpaid invoices. Which account should I watch?"
- She is told not to make one when an existing helper's job already covers it. She suggests using that helper instead.
- Never from a run nobody is watching (no card can be raised there).

## The rail

Your helpers live in a slim rail of round pictures down the left edge of the sidebar, about 50px wide, beside the header and chat: pictures only, so the chat keeps its room (modelled on Grok Bot's sidebar). It replaces the Helpers button.

- **Top to bottom:** her own picture (a small orb, named with her name), one picture per helper of yours in the order the list gives, then a dashed **+** ("New helper"). It scrolls on its own when there are many.
- **A tap** opens that helper's chat; her picture goes back to her last conversation that is not a helper's, or a new one. The picture whose chat is showing is ringed in the accent, with `aria-current="page"`.
- **Its state:** a dot while it is working (accent) or needs you (amber, larger); a paused one is dimmed. Its name, and its state when not at rest, is on hover and in its accessible name: "Inbox Monitor, needs you".
- **Its menu** (right-click, long press, Shift+F10 or the menu key; Escape closes it): **Change colour** (the picker), **Pause** or **Resume**, **Details** (the panel, at it), **Delete** (the panel's same confirm). Her picture's menu has one item, **Routines and watchers**, which opens the panel.
- **On a phone** the rail is a strip across the top of the sheet, so the chat keeps the sheet's full width. It hides with the sidebar.

## Talking to it

- Open it from its picture in the rail, the Helpers panel (**Talk to it**), or a line in her conversation (**Open**). It is a normal conversation: type, read, approve its cards.
- **Feedback edits its notes (AGT-7):** `helper_narrow` applies at once with the same allowlist as routines (`narrows_routine()`), and `helper_widen` asks on a card. Both are offered only in its own chat.
- **Changing its name, job, notes, connections or questions:** say so in its chat. `helper_update {name?, job?, notes?, connectors?, asks?}` asks on a card; each one given replaces what it had.
- **Its connections:** the chat's head and its details show the connections its card suggested that are not set up yet, as chips. A tap opens Settings, Connected tools, at that entry (`connect:<catalog id>`).
- **Its own routines:** "every Monday, check what's overdue" in its chat makes a routine with `helper_id` set, by the usual `ask_schedule` card. It runs in the helper's chat, with its overlay.
- In voice, you talk to her and she hands off. A helper's chat has no separate voice.

## Handoffs

- Her prompt lists each helper, not paused, as `name: job`, and offers `helper_ask {helper, request}` (`Risk.WRITE`, no card).
- `helper_ask` makes a one-off timed ask, due now, with `helper_id` and the request, through the same scheduler as everything else. So it gets the runner's timeouts, failure handling (AGT-8), spend gate, the panel's Working state and run history for free.
- She says one line ("Asking Invoices") and carries on. She never waits for it.
- **The run** is unattended, like a timed ask (`via="schedule"`), in the helper's chat, with its overlay. It may read and work. It can't raise a card. When it needs your approval it says what and why, and the run ends with that.
- **The answer** comes back through the door (SUR-9) as one `'ready'` notice in the conversation she was asked in: "Invoices: 3 invoices are overdue, $4,200 in all." with **Open**, which opens the helper's chat. It carries `helper_id` and `helper_name`, as the panel already shows. With nothing to say, it says so in one line (a handoff was asked for, so silence would read as lost).
- **Needs you:** "Invoices needs you: approve sending 3 reminders." with **Open**. You approve in its chat, where the card is raised once you say go. The panel lists it under Needs you.
- **Paused or capped:** she doesn't hand off. She answers herself and says, in a few words, that the helper is paused or at its spend cap.
- **She is busy elsewhere:** the result waits in the door like any notice (quiet hours, a meeting, Focus).

## Suggestions

- **A need** is an area of work: the tools a turn used, mapped to areas by tool name (email, calendar, files, payments, Slack, web, or one MCP server, found from the server that registered the tool). It is counted from the tool audit that already records every call: only calls that ran, only in your own conversations with her (not a helper's, not a run), once per area per local day. The check runs at most once a day per conversation.
- **Your request wins.** No host note rides with a turn whose words ask for a helper (they start "New helper:", or plainly ask to make, set up or want a helper, agent, assistant or bot: `needs.asks_for_helper`), or while a `helper_create` card waits in that conversation. A turn wrongly taken for one only goes without an offer.
- The offer line says "Suggested by {her name}" above its words, so it is never taken for your own request.
- When an area reaches 3 days within 14, no helper's job covers it, and it isn't declined, her next turn gets a host note: offer a helper for it, once, in one sentence, after answering. She offers with `helper_suggest {area, name, job}`. It shows as a line with **Create** and **No thanks**. After a tap the line reads "Making {name}." until the helper is made, then "Made {name}.", or "Okay." for No thanks, or a short reason if it couldn't be made. Only the newest offer can be answered.
- **Create** raises nothing more: your tap is the approval. The helper is made as above. **No thanks** keeps the area in the preference `helper_declined`, and she never offers it again. Neither happens on its own: no tap, no helper.
- At most one offer a day, and the note to offer at most once a day even if she doesn't.

## Its picture

Every helper's picture is a small orb built exactly as hers is (lit from the top left, with a soft glow), in the helper's own colour, so you can colour-code them and the rail and panel read at a glance. Hers stays her own: the first in the rail, in her accent.

- **11 colours:** red, orange, amber, lime, green, teal, blue, violet, fuchsia, rose and slate, each a theme token (`--helper-<colour>`), bright in dark and deeper in light, as her accent is, and none her own mint. Its accessible name is its name and colour ("Inbox Monitor, teal").
- **An icon is still stored** (one of about 24, picked from the job), but not drawn; nothing is migrated.
- **Never blank.** `helper_create` takes an optional `icon` and `color`. She picks ones that fit the job, and the card shows the picture beside the name. One she leaves out or gets wrong is picked from the job's words (a receipt for invoices, an envelope for email), else a spark, and the colour from the name, so it stays the same each time.
- **Changing it is one tap.** Tap the picture in the helper's details, or **Change colour** in its rail menu: the colours, each an orb, the current one chosen (arrows move, Escape closes). Your tap saves it at once (`PUT /api/helpers/own:<id>/look {icon, color}`, the icon sent back as it was), with no card, since a colour changes nothing she does.
- **Where it shows:** its row in the Helpers panel (the state dot sits on the picture's edge), the top of its details and its chat, its "Invoices: ..." lines in her conversation, and the create card.
- Stored on the helper (`icon`, `color`). Only names from the lists are kept.

## Its first hello

A new helper doesn't wait to be told what to do. When its chat first opens after Create, it takes one turn of its own:

- It says, in one or two sentences, what it will do first and anything it needs to know. When its card had questions, its host note gives them (made inert) and it asks those. When its card suggested connections that are not set up yet, the note names them and it says so in one line.
- If a regular check suits its job, it offers one through the usual `ask_schedule` card, as one of its own routines: "Want me to check for overdue invoices every Monday at 9?" One tap starts it. Nothing starts without the tap.
- The turn runs as a host turn in its chat, with its overlay, once per helper (`hello_ms` is set when it has run). If the model can't be reached, the fixed first line stands: "Hi, I'm {name}. {job} Tell me anything I should know to start."

## Managing

The Helpers panel lists your helpers under her with the others:

- A row has the name, its state, and its job when idle. Working shows "Working" and the time. Needs you shows what.
- Details show the job, its notes, its routines (each with its next run), its last 10 runs (handoffs and routines), and **Talk to it**, **Pause** or **Resume**, and **Delete**.
- **Pause** stops handoffs and its routines. Resume picks them up again, with no missed runs replayed.
- **Delete** asks once ("Delete Invoices? Its routines stop. Its chat stays in History."), then removes the helper and its schedules. Its chat stays, as a plain conversation.
- Routes: `POST /api/helpers/{id}/pause`, `.../resume`, `DELETE /api/helpers/{id}`, behind the usual token, origin and JSON checks. Creating has no route: it goes through her.

## Safety

- Every tool call goes through the router as today. A helper's run is unattended, so it gets only what runs without a card (D5).
- What a run reads counts as outside (MEM-5), and its reply is saved as read, not as your words.
- A helper's job, name and notes come from you, through her, on a card or by the narrowing allowlist. A job she drafted from something she read becomes the helper's instructions in its chat once you tap Create, so the card is where you check it. Outside content can't write them: after reading outside content in a turn, `helper_create`, `helper_update` and `helper_widen` are refused, as `routine_widen` is. The same holds for a draft's notes, connections and questions: they come only with a card made before any outside read, so nothing she read can draft them.
- Handoff runs are background work for the spend gate (`Work` gains `"helpers"`). At a cap they pause like routines.

## PRD changes

AGT-1 and AGT-2 describe a manifest (accent colour, avatar, model, tool allowlist) and YAML with prompt versioning. This spec replaces them with less: a name, a job and notes, made by asking. Their rows are amended to say so, and the manifest fields and YAML are listed as deferred.

## Out of scope

- Per-helper permissions, models or voices.
- Helpers talking to each other, or handing work on.
- Sharing, importing or exporting helpers.
- Uploading your own picture for a helper (colours only, for now).

## Tests

- **Core:**
  - create on a card, the limit of 20, unique names, never from an unattended run or a helper;
  - the draft: notes, connections and questions kept, unknown connections dropped, each cut to its limit, nothing drafted after an outside read; a new draft in the same conversation supersedes the waiting one, which can't then be approved; migration 020 on a database at 019;
  - no offer on "New helper:", a plain ask for one, or while a draft waits; the hello note carries the questions and the connections not set up;
  - the overlay in its chat and its runs;
  - narrow and widen, and update on a card;
  - its routines run with its overlay;
  - `helper_ask` makes a due-now ask, runs in its chat, and the ready line carries its name;
  - a run that needs approval ends with a needs-you line;
  - paused and capped helpers aren't handed work;
  - dispatch tools refused to helpers by name;
  - areas counted per day, the offer at 3 in 14, declined areas never offered, one offer a day;
  - pause and delete with its schedules;
  - the routes refuse without the token;
  - the picture: kept from the card, picked from the job when missing or wrong, the same each time, changed by `PUT .../look`, only names from the lists;
  - the first hello runs once, offers a routine on a card, and falls back to the fixed line when the model fails.
- **Web:** the pinned draft (its parts, More, Create and Not now as approve and decline, updated in place with what changed marked), a connection chip opening Settings at its entry, "Suggested by" on an offer, the picture on the row, the details, the chat and the ready line; the picker saves a tap; + New helper sends the sentence, the create card, the new chat opens, the panel rows and details, pause, resume, delete with its confirmation, the suggestion line, phone width, light and dark.
- **Live** on a throwaway install with a local model: make a helper by asking and by the button, talk to it, correct it, hand it work and see the ready line, pause it, see an offer after a repeated need, delete it.
