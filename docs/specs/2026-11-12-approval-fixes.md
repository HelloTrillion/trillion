# Approval fixes: sends need a tap, cards show it all, a yes is checked

**Date:** 2026-11-12 · **Requirements:** SPN-2, SPN-2a, SPN-14, SPN-17c, SPN-20 · **Reported by:** an outside tester, each case reproduced

Three gaps in the approval gate, each found by sending a real email that the user had not fully approved.

1. **Sends counted as recoverable.** `ToolDefinition.recoverable` defaults to `True`, and only `file_overwrite`, `file_edit` and `folder_share` said otherwise. So `gmail_send` ("It cannot be unsent") was closed by a spoken or typed yes, with nobody's voice enrolled, against SPN-14 ("anything unrecoverable needs a tap") and SPN-20 (`send_email` keeps the second turn). "Yep. You can send it." sent a real email.
2. **The card showed 200 characters.** `router.safe_summary` cut every card to one line of `SUMMARY_LIMIT = 200`, and that was all the card had. A 592-character email showed 200; its last line, asking for a contract to be forwarded elsewhere, went out unseen. With 13 recipients the card showed 8. `gmail_send` told her the card shows "the whole text exactly as it will go".
3. **A yes was not checked against what she read, or which card.** `confirm_pending_action` is the model's call. `decide()` did not look at `ctx.read_outside`, so a confirm on a turn that read planted text still sent the email. With two cards waiting, a confirm naming the older one sent that one.

## Decisions

### 1. What reaches other people needs a tap

- **Google and Slack.** A tool whose own effect is irreversible (`native = Risk.IRREVERSIBLE`: it reaches other people) is `recoverable=False`, however the user set it. A read or a draft that asks only because the user left it asking first changes nothing anyone sees and stays recoverable. Set once, in `GoogleTool.__init__` and `SlackTool.__init__`.
- **MCP.** Trillion can't know what a server's tool does or whether it can be undone. Every MCP tool is `recoverable=False`. A tool the user mapped to `read` or `write` runs without a card, so the flag never matters for it; one left asking (the default) needs a tap. The user's risk mapping lives in `mcp.json` (`"tools": {"name": {"risk": …}}`) and is shown in Settings → Connected tools as Asks first or Runs on its own. A second "recoverable" choice beside it would be a switch whose safe answer Trillion can't check, so none is added. `stripe_revenue`, which is shown as an MCP server's tool, follows the same rule.
- **Structural.** `McpTool` sets `recoverable=False` itself, never from anything the server says. The registry does not refuse a recoverable tool of category `mcp`: test fixtures stand in for connected apps with that category and a typed yes (`tests/api/test_approved_reads.py`), and a real MCP tool is only ever an `McpTool`. The default stays `True` for test fixtures (`tests/unit/test_contract.py` pins it), but a structure test fails for any `ToolDefinition(...)` in `src/trillion` outside the spine whose risk isn't a literal `READ` or `WRITE` and that doesn't say `recoverable=` itself. Another pins the full list of gated tools a yes may approve, so adding one is a reviewed change.
- **What she is told.** Every tool only a tap approves says so in its description (`card.ONLY_A_TAP`: "Only a tap on its card approves it, never a spoken or typed yes: ask the user to tap Approve."), as the file tools already did. `describe_self` reads `recoverable`, the card's `reason` becomes `irreversible` and `tap_only` is true, and the page says "This one can only be approved here." A yes that reaches the gate is refused `tap_required`, which her rules already tell her to answer by asking for the tap. No persona prompt changes, so the eval baselines still match.

Per tool, gated (`irreversible`, always or when left asking first):

| Tool | Recoverable | Why |
|---|---|---|
| `gmail_send` | **no** (was yes) | It goes to other people and can't be unsent. Gmail has no reply or forward tool: a reply is `gmail_send` with `reply_to_id`. |
| `calendar_create` | **no** (was yes) | With guests, Google emails each an invitation. Per tool, not per call, so without guests too. |
| `calendar_update` | **no** (was yes) | Sent with `sendUpdates=all`: guests are emailed about the change, and the old values are gone. |
| `calendar_delete` | **no** (was yes) | Guests are told it is cancelled. |
| `slack_post` | **no** (was yes) | Everyone there sees it at once. |
| every gated MCP tool, `stripe_revenue` | **no** (was yes) | Trillion can't know what it does. |
| `file_overwrite`, `file_edit`, `folder_share` | no (unchanged) | As before. |
| `gmail_draft` | yes | Only in the user's own Drafts. |
| `gmail_search`, `gmail_read`, `calendar_events`, `calendar_busy`, `drive_search`, `drive_read`, `slack_search`, `slack_conversations`, `slack_read` | yes | Reads, gated only when the user leaves them asking. |
| `file_create`, `file_append` | yes | The user's own files, and can be put right. |
| `memory_forget` | yes | Soft underneath (SPN-9). |
| `rule_keep`, `rule_drop` | yes | Her standing rules: the user's to change back. |
| `ask_schedule` | yes | A timed ask runs with read-only tools; it can be cancelled. |
| `routine_widen`, `speak_up_widen`, `helper_create`, `helper_update`, `helper_widen` | yes | Hers and the user's, narrowed or removed again from Settings. Each already refuses a widening on a turn that read something from outside. |

### 2. The card shows everything that will go

- **The principle (SPN-2a):** what the user approves is everything that will go, exactly. The one-line summary stays the card's headline (still neutralized and cut to 200, so every existing card and test reads as before). `ActionSummary` gains `detail`: the host's full content, beneath it.
- **Who writes it.** Each tool, from the frozen arguments, as it writes its summary. `gmail_send` and `gmail_draft`: who it is from, every To and Cc, the subject, that it is a reply, then the whole body. The calendar tools: the title, the times, the place, every guest and all of the notes. `slack_post`: where, in a thread or not, and the whole text. Any other Google or Slack tool, and every MCP tool: each argument whole (text as it is, anything else as indented JSON). The file tools: the destination and the whole text written (and both texts of an edit). Gmail has no Bcc and no attachments, so there are none to show.
- **A tool that gives none.** If the summary is not all of its text (it was cut, or put on one line), the router uses the text itself as the detail. So no card shows part of what it says, whichever tool raised it.
- **Exact, and refused rather than altered.** `spine/card.py:card_detail` keeps line breaks (a carriage return reads as one) and tabs, and removes or replaces nothing. A detail a tool wrote is refused before any card if it holds a character a card can't show as it is: a control character, a line or paragraph separator, or a hidden mark (a direction override or isolate, a zero-width space, a soft hyphen, a tag or variation selector). Emoji drawn with a joiner or a variation selector, and a non-joiner between letters that join, are seen, so they pass (`hidden_marks`, moved here from `files/shares.py`, which `folder_share` already used). Role markers and fences are left as written: they protect the model, and the detail is never sent to the model; the page sets it as text, never markup. NFKC is not applied: the card shows the characters that will go.
- **Files are the user's own.** A file may hold invisible characters on purpose (a lossless round trip, SPN-6a), so the file tools, and the router's fallback for a tool's own text, show each such character as a visible `⟦U+XXXX⟧` token (`card.visible`) instead of refusing. What is written is the text itself.
- **Lookalike addresses.** A To, Cc or guest address with a character outside ASCII is refused before any card ("could read as another address"): a Cyrillic "а" (U+0430) in a domain looks like the Latin "a". Gmail addresses are otherwise checked as before.
- **Bounded.** `DETAIL_LIMIT = 20_000` characters. Longer, and the call is refused before any card with a plain reason ("too long to show whole on a card … make it shorter, or split it in two"), never shown in part. That covers file writes too: a file over 20,000 characters can't be written from a card.
- **Carried.** `PendingAction.detail`, stored beside the full arguments in `pending_action_args.detail` (migration `202611121200_card_detail`), so it is cleared with them 30 days after the action settles (SPN-17c) and never kept forever in the frozen row. The trigger that allows only clearing now also requires the detail to be cleared with the arguments and refuses any other change to it. `confirm.request` carries `detail` (§8.3), empty when the summary says it all. It is never in `RouterResponse.to_model()`. A re-presented card composes it again from the same arguments.
- **Shown.** `apps/web/src/card-detail.ts`: the conversation's card and the board's card show the detail beneath the summary in a scrolling block (at most 320 pixels high, scrolled by keyboard too), set with `textContent`. It is never collapsed, so it is there whenever Approve is.
- **Spoken.** She reads the summary aloud as before. A tap, or a yes where one is allowed, approves what the whole card shows.
- Found on the way: `storage/inspect.py` only knew three-digit migrations, so `trillion doctor` would have called the first timestamped one "a newer Trillion". It now matches `migrate.py`.

### 3. A yes is checked against what she read, and which card

In `decide()`, after the tap rule and the voice checks, both for a confirm that is not a tap. A cancel is always allowed.

- **After reading.** On a turn with `read_outside` (a page, an email, a file, a connected tool's result), a confirm is refused `after_reading`: "this turn read something from outside (…), so a yes now can't approve a card; ask the user to tap Approve on the card". The tap is the user's own act, whatever was read.
- **Which card.** `decide` takes the session's waiting cards (`Waiting`: id, raised, expires, first shown). `newest()` picks the one shown on a screen last (SPN-17's `delivered_at`; ties go to the one raised last). A confirm for any other is refused `not_newest`: "more than one card is waiting and a yes can only approve the newest one; ask the user to tap Approve on the card they mean". A card not yet shown is never the newest: the user can't be answering it. With none shown there is nothing on screen for a yes to be about; the running app requires a card to be shown before it can be approved at all (`require_delivery`), so every card is refused alike then. The router lists the session's proposed cards and their deliveries (`Router._waiting_cards`) only for a confirm that isn't a tap.
- **Re-presented and superseded cards.** A re-presented card is a new card, newest once it is shown. A superseded or expired one is no longer waiting. A card drawn again after a reconnect keeps its first `delivered_at`, so redrawing an older card never makes it the newest.
- The order keeps every existing refusal as it was: same turn, then the tap rule, then the voice verdicts, then these two.

## Tests

- `tests/spine/test_recoverable.py`: the pinned list of gated tools a yes may approve, and of those only a tap approves; each of those says so; sends and posts are never recoverable however they are set; every `ToolDefinition` that may ask says `recoverable=`; a gated MCP tool is unrecoverable whatever its server says; a spoken (no one enrolled, or the owner) or typed yes is refused for one, its tap approves it.
- `tests/spine/test_card_detail.py`: the tester's email (600 characters, its last line past 200, 13 recipients and a Cc) whole on the card and sent as shown; a too-long one refused before any card; a bidi override, a zero-width space, a line separator and a control character refused; emoji and accents shown as they are; a lookalike recipient refused; the short-body card as before; every guest and all the notes on an event; an MCP argument and a file's text whole; a file's invisible characters as tokens and the file written exactly; an edit's two texts; a tool without a detail never showing part; the detail frozen, refused any edit, and cleared with the arguments; a re-presented card's detail; the pure check.
- `tests/spine/test_yes_checks.py`: `newest` in every case, then `decide` exhaustively over via (tap, voice, text) × read or not × seven arrangements of waiting cards (alone, older, newer shown, newer unseen, newer expired, several) × every speaker verdict, against the rule written out plainly; a no in all of them; then through the router, the tester's two cases, the tap that still works, a re-presented card and a superseded one.
- `tests/api/test_approval_card.py`: `confirm.request` carries the detail, and an empty one when the summary says it all. `apps/web/test/card-detail.test.ts`: the page keeps it whole, draws it as text in the conversation and on the board.
- Every existing test passes unchanged, among them `test_sending_shows_everything_and_threads_a_reply` and 1.1.4, which confirms the older of two cards by text: neither card there was shown, so there is no newest to weigh it against.

## Platforms

The same on macOS, Windows and Linux: nothing here touches the file system, audio or the desktop shell. `docs/platforms.md` is unchanged.

## Not now

- A per-call `recoverable` (a calendar event with no guests could take a spoken yes). Per tool is the safe side.
- A recoverable choice for MCP tools in Settings.
- Showing what will go on a voice-only surface: there is none yet; the card is the surface.
