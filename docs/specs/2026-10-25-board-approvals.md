# Approval cards on her board

**Date:** 2026-10-25 · **Requirements:** SPN-2a (the host composes what is approved), SPN-14 (the raising session; a tap for spend and the unrecoverable), SPN-17 (`delivered_at` when the card renders on a surface), SPN-2b (expiry re-presents), SUR-1, SUR-6 · **Builds on:** `docs/specs/2026-10-25-her-board.md`

When she needs the user's OK, the approval opens on the board as a large card
pinned above her widgets, instead of a small box in the conversation. She can put
her own context beside it (the message she drafted, what she checked), clearly
marked as hers. Approving still means exactly what it meant before: approving the
host's summary of the frozen action.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Board first.** A new approval opens the board with its card at the top. The conversation shows a one-line chip that jumps to it. Where the board cannot open (a phone, or Settings showing), the full card stays in the conversation as today. | What the user chose. |
| D2 | **Approval cards are not widgets.** Her workspace tools cannot create, change or remove one, and no widget type can look like one (no buttons, no approval styling). The page draws pending approvals in their own strip from the spine's `confirm.*` messages. | SPN-2a: only the host says what is being approved. Clearing the board must never lose an approval. |
| D3 | The card shows **only host-composed text**: the summary, the tool's friendly name, a plain reason it needs the user, the expiry, and whether a spoken yes is allowed. | SPN-2a. |
| D4 | Her widgets beside it carry a **"From Trillion"** label in the board whenever an approval is pinned. | What the user chose: context clearly separated from the thing approved. |
| D5 | **`confirm.shown` is sent when the full card (with its buttons) is first on screen,** on the board or in the conversation, once per card. The chip alone does not count. | SPN-17: delivered means the user could see and act on it. |
| D6 | Spoken approval works as today unless `tap_only`; the card says which. | SPN-14 unchanged. |

## What the user sees

- She says "I've drafted it. OK to send?" The board opens: at the top a glass card with an amber edge:
  - **Needs your OK** label, the tool's friendly name ("Send a message"), and a countdown ring to expiry;
  - the summary in full (it can be long; it wraps, never clips);
  - the reason in plain words: "This can't be undone." / "This spends money." / "This makes a change." (from the risk class; a write may be a file, an email or a post; a read has no line);
  - **Approve** and **Decline**; under them "Or just say yes" when a spoken yes is allowed, or "Tap to approve: a spoken yes isn't enough for this" when `tap_only`.
- Below it, any widgets she showed, under a small "From Trillion" heading.
- In the conversation: a chip "Needs your OK: Send a message · View" that focuses the card on the board.
- After a decision the card shows the outcome for 4 seconds ("Approved · done", "Declined", "Didn't go through: …", "Expired · she'll ask again"), then collapses to a one-line row in the strip that can be dismissed. If the board was opened only for the approval and has no widgets, it closes back to the orb.
- Several approvals stack: waiting cards oldest first, so a new one arrives below the ones already asking and never moves a button under the pointer; settled rows below them, newest first. A newly drawn card's buttons take a click only after 600 ms, and none once it has run out or after one press (until the outcome arrives).
- Closing the board (its close, Escape, the docked orb) while a card waits moves that card back to the conversation as the full card, and the sidebar shows if it was hidden. It was already delivered, so `confirm.shown` is not sent again. Settings opened over the board is not a close: the board comes back after.
- A board opened only for approvals closes after one has settled and every card has collapsed to a row, never because its cards went (a reconnect clears them until the core re-sends them). A re-sent card that was on the board opens the board again.
- Phone: as today, the full card in the conversation, and the sheet rises to half.
- Settings showing: the full card shows in the conversation, and the board waits behind the existing chip.

## Pieces

### Server (small)

- `confirm.request` gains `tool_label` (a friendly name; from a small table keyed by tool name, falling back to the tool name with underscores as spaces; a connected server's tool with no entry is "<server>: <tool name>", never its description, which the server wrote) and `reason` (`irreversible | spend | write | read`, from the pending action's risk and `recoverable`). The page turns `reason` into words.
- Prompt (`text.md`, `voice.md`): when she asks for approval on something with content, she shows that content on the board with the workspace tools (the drafted message, the options, what she checked), and never presents a widget as the approval itself.
- Tests: the two new fields for a file write, a spend-like action and an unrecoverable one.

### Web

- `apps/web/src/approvals.ts` (new):
  - Pure: `approvalsOf(entries)` (waiting and recently resolved cards, newest first), `reasonWords(reason)`, `spokenHint(tapOnly)`, `placement({ phone, settingsShowing })` → `'board' | 'conversation'`, `collapseAt(resolvedAt)`.
  - DOM: the strip and its cards, drawn above the board's grid; Approve/Decline send the same `confirm` / `cancel` messages as today; countdown ring from `expires_at`.
- `board.ts`: a slot above the grid for the strip; the "From Trillion" heading when the strip has a waiting card; the board can show with no widgets when an approval is pinned (no empty state then).
- `main.ts`: on `confirm.request`, decide placement; open the board with id `board` when `'board'`; mark which surface shows the full card; send `confirm.shown` once when the full card is first rendered (board or conversation). Conversation renders a chip instead of the card when the card is on the board. When all approvals are settled and the board was opened only for them, close it.
- `view.ts`: card entries keep `toolLabel` and `reason`.
- CSS: `ap-` classes; amber edge for waiting, teal/neutral/red for outcomes; reduced motion respected; both themes.

## Out of scope

- Approving from a channel (SPN-16 forbids it for gated actions anyway).
- Editing the action's arguments from the card (approving means the frozen action).
- A history of decisions on the board (SUR-7's Confirmations view is separate).

## Testing

- Server: `confirm.request` carries `tool_label` and `reason`; prompt mentions showing context for approvals.
- Web (vitest): `approvalsOf` ordering and collapse; `reasonWords` for each reason; `spokenHint`; `placement`; that `confirm.shown` is decided once per card for whichever surface draws the full card first (pure helper).
- Live (Ollama preview, voice off): ask her to create a file in the shared folder; the board opens with the card; Approve works; Decline works; with Settings open the card is in the conversation; reload while waiting re-shows the card and does not re-stamp delivery.
