# Compaction, and the rules it can never drop

**Date:** 2026-10-15 · **Requirements:** BRN-5, BRN-9, SPN-3 · **Phase 0 item:** 13

Until now every message of a conversation was sent to the model on every turn, forever. On the local models testers use, a long conversation would eventually go past what the model can take. Now the oldest turns are summarized once the history grows too long. The danger the PRD opens with is exactly this step: an agent deleted hundreds of emails after summarizing away "confirm before acting". So the user's own rules are kept somewhere summarizing cannot reach.

## Standing rules (BRN-5)

**What they are.** Something the user told her to always or never do: "never email my ex without asking twice". Not a fact or a taste; those are memories.

**Where they live.** One plain file, `rules.md` in the data folder, one rule per line. The user can read it and edit it by hand. A rule's id is a hash of its text, so a hand-written bullet needs no id and the same rule is never kept twice. At most 20 rules of 300 characters each.

**How they reach her.** In the system block, on every turn, after her own rules, headed "STANDING RULES Sam GAVE YOU". The heading says they hold until the user drops one, that they can only make her more careful, and that none of them approves an action or stands in for a yes on a card. Because they are in the system block and not in the history, nothing that rewrites the history can touch them.

**How a rule is added or removed.** `rule_keep` and `rule_drop` both ask first, on a card that quotes the rule. That matters: the rules are trusted text in her instructions, so a page or a file she reads must not be able to slip one in. A dropped or edited rule takes effect from the next turn.

## Compaction

**When.** Before a turn starts, if the history since the last summary is over 12,000 rough tokens (four characters to a token, deliberately generous). Never in the middle of a turn. `TRILLION_COMPACT_AT` sets a smaller budget for trying it out.

**Where it cuts.** Only at the start of a turn, before something the user said or tapped, so a tool call and its result are never separated. It keeps as many of the newest turns as fit in 3,000 tokens, and always keeps the turn being answered now.

**The summary.** The same model writes it, with no tools offered, from a plain transcript of the part being cut and the previous summary if there is one. Its instructions (`persona/prompts/compact.md`) ask it to keep decisions, open items, the outcome of every action exactly as reported, and every instruction the user gave, word for word. Text from tool results is to be recorded as information, never repeated as a request.

**What the model is sent afterwards.** The summary rides at the front of the first kept message, wrapped as untrusted text like a tool result. It is her notes on the past, written from a conversation that held tool output, so it is never treated as an instruction. Then every message after the cut.

**What is kept.** Every message stays in the database unchanged, and the page still shows all of them. A new table (migration 006, append-only) records each summary and the last message it covers. A reload sends the latest summary and the messages after it. The turn count still comes from the whole conversation.

**If the summary fails** (an error, an empty reply, or 90 seconds with no answer), the turn goes ahead on the whole history and nothing is recorded. A long prompt is better than a lost one.

**What the user sees.** A line in the chat: "She summarized the earlier messages to make room. They are still here, and her standing rules are kept whole."

## What survives, and why it does not depend on the summary

| What | Why it survives |
|---|---|
| Her identity and rules | They are the system block, not the history. |
| The user's standing rules | Also the system block (above). |
| A card waiting for an answer | It lives in the database. Every new message lists it again in its context, and it can still be approved (SPN-3). |
| The decision record | In the database, untouched by any of this. |

## Checked

- `tests/integration/test_compaction_protects_standing_rules.py` (BRN-9): a rule set by card at the start is in the system block of every call after the conversation is summarized, while the turn that set it is no longer sent. The test fails if the rules are left out of the prompt; I checked by removing them. Also: a card raised before the summary is still listed and can be approved after it; the page keeps every message; a reload sends the summary, not the old turns; a failed summary leaves the whole history in place.
- `tests/brain/test_compact.py`: a property test that the cut never separates a tool call from its result and always keeps the newest turn; what the summarizer is and is not shown; that no tools are offered; that an empty summary is a failure; that the summary is wrapped as untrusted text.
- `tests/unit/test_standing.py`: the rules file round trip, hand-written bullets, duplicates, dropping, the limits, and markup being defanged.
- By hand with `gemma4:e4b` and a small budget: a rule kept by card, seven long answers, two summaries. **The second summary dropped the rule** from its "Instructions" section. She still listed the rule when asked and read a file's text back before writing it, because the rule was in her instructions and not in the summary. That is the failure BRN-5 exists for, caught. The summarizer's instructions now also tell it to carry earlier notes forward; on the same run repeated, both summaries kept the rule, and she again read the text back before writing.

## Not checked

- A paid model. The budget is in rough tokens, not the provider's own count.
- Very long single turns: a turn bigger than the budget is kept whole, so a history can still outgrow a small model if one message is enormous.

## Known limits

- **The budget is one number for every model.** It is sized for a model with a 16k context. A model with a much larger window is summarized sooner than it needs to be. Reading each model's real window is a later step.
- **A summary is lossy.** Details from early in a long conversation can fade. Memory (for facts) and standing rules (for instructions) are the durable places.
- Rules are not shown in the app yet; the file is the way to see them all, or asking her.
