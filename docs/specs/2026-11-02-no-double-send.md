# No double-send

**Date:** 2026-11-02 · **Requirements:** OPS-1, extending SPN-17a and SPN-17b · **Builds on:** [the decision record](2026-10-15-decision-record.md)

An approved action that reaches outside (an email, a calendar change, a Slack post) must never go out twice, and an interrupted one must never be quietly forgotten.

Most of this exists. An approved action is claimed as `executing` before its tool runs, which is OPS-1's `sending`. At startup `recover()` never re-runs one: it asks the tool's `reconcile()` what happened and records `unknown` if the tool can't say. What is missing:

1. **The outside tools can't say.** `gmail_send`, `calendar_create`, `calendar_update`, `calendar_delete` and `slack_post` have no `reconcile`, so after a crash each one is `unknown`, even when the email is sitting in Sent.
2. **Nobody is told.** `recover()` returns the ids it touched and the runtime drops them.
3. **A lost answer is final.** When Google or Slack doesn't answer (a timeout), the action is `unknown` at once and nothing ever checks again. That is the same double-send risk without a crash.
4. **The proof.** No test kills the process between the provider accepting and the record being written.

## Decisions

1. **Tag every send so it can be found.** The router hands each tool the approved action's id. A tool puts it where the provider keeps it, and its check looks it up.
2. **Three answers from a check.** It landed (`confirmed`), it never went (`none`), or it couldn't reach the provider right now: the check returns `effect="unknown"` with `error_kind="unreachable"` (`UNREACHABLE` in `spine/contract.py`), raises, or runs past the tool's timeout. Only that last answer is tried again. A plain `unknown` (no `unreachable`) means the tool can never tell, as when a file differs from what was approved: the action is `unknown` at once. So is a tool with no check at all (`reconcile` returns `None`, the default, as for MCP tools), as today.
3. **Couldn't reach is not the end.** The action stays `executing` and is checked again every 10 minutes, for 24 hours after it was proposed (an approval comes within 10 minutes of that). Then it is `unknown`.
4. **Never sent is askable again, never re-run.** It is recorded as `invalidated` with "interrupted before it went out", which `represent` already accepts. A new card appears only when the user says yes, and needs its tap like any send.
5. **Tell the user only when in doubt.** Landed: recorded, nothing said. Never went, or unknown: one line, once, through the one door, in her words. Never went: she offers to send it. Unknown: she says she couldn't confirm it and where to look, and does not offer to send it again.
6. **No new status.** An action awaiting a later check is `executing` with `effect_status` `unchecked`. No migration.

## The tag, per tool

`ToolContext` gains `action_id: str | None` and `proposed_ms: int | None`, set by the router to the pending action's id and proposal time on the confirmed path and in every check. Both are `None` on the direct (ungated) path.

| Tool | Where the tag goes | The check |
|---|---|---|
| `gmail_send` | The `Message-ID` header: `<trillion-{action_id}@{sending domain}>` | Search the account's Sent with `rfc822msgid:` for it. Found: `confirmed`. Not found: search Sent for the same recipients and subject sent after the approval (Gmail may rewrite the header); found: `confirmed`, else `none`. |
| `calendar_create` | The event's own `id`: the action id hashed into base32hex (Google allows `a`–`v` and `0`–`9`, 5–1024 characters). Google refuses a second event with the same id. | Get the event by that id. Found and not cancelled: `confirmed`. 404: `none`. |
| `calendar_update` | None needed: the change is the evidence. | Get the event. Every field the card named already has the new value: `confirmed`. Otherwise `none`. |
| `calendar_delete` | None needed. | Get the event. Status `cancelled`, 404 or 410: `confirmed`. Still there: `none`. |
| `slack_post` | None: Slack has no client-supplied id. | Read the conversation (or the thread) since the approval. A message from the user with exactly the approved text: `confirmed`. Otherwise `none`. |

In every check, no answer from the provider (a network error, a timeout, `answered=False`) is `unknown` with `error_kind="unreachable"`, so the check runs again later. An answer of no (403, a revoked sign-in) is also `unknown` and `unreachable`: it says nothing about whether the send happened, and may be fixed by the next check.

## The router

- **`recover()` at startup.** Unchanged for `approved`: invalidated, never run. For each `executing` action it runs the check, then:
  - `confirmed`: `succeeded`, decision `recovered:succeeded`.
  - `none`: `invalidated`, detail "interrupted before it went out", decision `recovered:not_sent`. On an action already `unchecked`, `none` counts only once the action has been quiet for 5 minutes since it was marked (`SETTLE_NONE_AFTER_MS`); before that it stays `unchecked`.
  - Couldn't reach, proposed under 24 hours ago: stays `executing`, `effect_status` `unchecked`. The first time, one `unchecked` decision is written (through the one writer, so the record shows a check is pending); later rechecks write nothing.
  - Couldn't reach and older, a plain `unknown`, or the tool has no check or is gone: `unknown`, decision `recovered:unknown`.
- **A lost answer mid-run.** When a gated action's tool returns `effect="unknown"`, the router runs the check once at once. The request that timed out may still be landing at the provider (a timeout only stops the waiting), so only `confirmed` settles it now. `none` or couldn't reach: it stays `executing` and `unchecked`, and she is told it went out unconfirmed and is being checked; the 5-minute quiet rule then keeps a later `none` from counting too soon. No check, or a plain `unknown`: recorded as `unknown`, as today.
- **`check_unchecked()`**: settles each `executing` + `unchecked` action as `recover()` does. The runtime calls it every 10 minutes.
- **`represent` after a restart.** An action whose last decision is `recovered:not_sent` may be represented from any session of its owner, since the session that raised it ended with the crash. The new card belongs to the session that asks, and the usual rules apply to it.
- `recover()` and `check_unchecked()` return what they settled (id and outcome), not just ids, so the runtime can tell the user.

## Telling the user

- For each action settled as `not_sent` or `unknown`, the runtime adds one notice through the one door, keyed `action:{id}` so it is made once.
- Her line is written by one small model call, in the manner of a watcher's line (`attention/words.py`): no tools, the action's summary wrapped as untrusted, a short timeout. If the call fails or there is no model, the notice is shown by its title and not spoken (PER-11).
- The title is the host's and says nothing private, since a lock screen is public: "A send was interrupted".
- What she says, roughly:
  - Not sent: "The email to Sam didn't go out before I stopped. Want me to send it?"
  - Unknown: "I can't tell whether the email to Sam went. Worth a look in your Sent folder."
- When the user says yes, she finds the action through `decision_record` and calls `represent_pending_action`. The card says what it said before.

## Tests

- **`tests/spine/test_no_double_send.py`, the kill test (OPS-1's proof).** A child process runs a router with a fake Gmail tool whose provider is a small local HTTP server. The fake tool's `execute` posts the send; once the server accepts, the child exits at once (`os._exit`) before the record is written. The parent then starts a router on the same database and runs `recover()`. The server counts exactly one send, the action is `succeeded`, and its decision is `recovered:succeeded`. A second case has the server refuse the send before the exit: the action is `invalidated` and `represent` from a new session gives a card.
- **`tests/spine/test_outcomes.py`:** the three answers, the 24-hour line, `unchecked` settling in `check_unchecked()`, the lost-answer path, `represent` from another session only after `recovered:not_sent`. The existing "reconcile raises" test becomes "raises within 24 hours stays unchecked; after, unknown".
- **Tests per tool** (`tests/mcp/test_google.py`, `tests/mcp/test_slack_channels.py`): each check against recorded Google and Slack answers, the `Message-ID` and event id carrying the action id, the calendar id's alphabet and length.
- **The notice:** a `not_sent` and an `unknown` each make one notice, a `succeeded` none, and a second `recover()` none more.

## Docs

- PRD OPS-1: an "Exists:" note naming this spec.
- `docs/TESTING.md`: a row for the kill test beside 1.2.1 and 1.2.2.

## Out of scope

- Money and purchase tools: none exist yet. They will get a check of their own.
- The Confirmations view (SUR-7).
- Any retry without a new tap.
- MCP tools: they have no way to be checked, and stay `unknown` after a crash.
