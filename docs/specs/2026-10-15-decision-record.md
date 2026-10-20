# The decision record, read back

**Date:** 2026-10-15 · **Requirements:** SPN-17 (reading it back) · **Phase 0 item:** 14

Every action that asks for the user's yes has always been recorded: the frozen request, each decision, when its card was shown, and the outcome. She had no way to read it. Now she does, so "did that ever reach me?" is answered from the record, not from a conversation that may have been summarized, cleared or never seen.

## `decision_record`

A read-only tool. It takes optional `about` (words from what was asked, such as a file name), `days` (default 30, at most 365) and `limit` (default 10, at most 25), and returns the matching actions, newest first:

```
3f2a…: Create /Users/sam/notes/groceries.txt (11 B): “eggs, bread”
  asked: Wed 15 Oct 2026 14:02
  shown: on screen at Wed 15 Oct 2026 14:02
  answer: approved (a tap on the card) at Wed 15 Oct 2026 14:03
  outcome: done
```

- **Every line is read from the database.** "shown" comes from the delivery row written when the card was really on screen (SPN-17); "answer" from the append-only decisions; "outcome" from the action's status and recorded detail.
- **A card nobody saw is never called waiting.** A request still open whose card was never shown reads "not answered, and the user has not seen it". One shown and unanswered reads "waiting for an answer". One past its time reads "expired without an answer, so nothing was done".
- **How an answer arrived** is named as the user would: a tap on the card, spoken, typed, or a message.
- **`about` matches any one word** of three letters or more, leaving out filler like "the file I asked for", so a question in ordinary words still finds the action. Each word is matched as plain text: `%` and `_` are not wildcards.
- **Its result is untrusted text**, like every tool result: a summary can quote what a file said.
- Her rules now say that whether something was approved, shown, done or is still waiting is a question for this tool, never for her memory of the chat.

## Checked

- `tests/spine/test_decision_record.py`, through the router: an approval answered from the record alone; a card never shown; a card shown and waiting; one past its time; a spoken no; a failure; newest first, the window, the limit and word matching; `%` and `_` taken literally; nothing to report; another owner refused.
- `tests/api/test_decision_record.py`: approve in one conversation, start a new one with nothing from the first, ask, and the tool's answer to the model says shown, approved by tap, and done.
- By hand with `gemma4:e4b`: a file approved and a second card never shown, then a new conversation. On the first run she searched for "groceries file", the lookup needed the exact phrase and found nothing, and she said the unseen card was "waiting for your answer". After the two fixes above she answered both correctly: the file was approved and written, and nothing is waiting because the other card was never displayed.

## Not built

- A Confirmations screen over the record (SUR-7, P1).
- Escalating unseen decisions over time (the hour, the daily digest, 48 hours).
