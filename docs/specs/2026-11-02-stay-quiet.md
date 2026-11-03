# Staying quiet: she doesn't take every turn

**Date:** 2026-11-02 · **Requirements:** VOI-7 (turn-close detection), VOI-15d, PER-3 (voice overlay), PER-11, EVL-1 · **Builds on:** [fluid turns](2026-10-24-fluid-turns.md), [voice fixes](2026-11-01-voice-fixes.md)

Talking to her should feel like talking to a close friend on the phone. Today every turn gets a reply, even "okay, thanks" or a half-finished "I'm just wondering if…". When the model does return nothing, the page says "The model sent back nothing" and the persona eval counts it as a failure. She needs a real way to say nothing, and rules for when to use it.

This is the first of the voice-flow changes. The second, an open line (a microphone that stays open through a conversation), is its own spec and builds on this one.

## Decisions

1. **A host tool, not empty text.** `stay_quiet` ends her turn without a word. A tool is reliable across models, can be tested, and lets the host tell a chosen silence from a failure. An empty reply stays a failure, as today.
2. **Two kinds of quiet.**
   - `listening`: the user is mid-thought. The microphone stays open and the floor stays theirs.
   - `done`: the user signed off. The microphone closes and the orb rests.
3. **Spoken turns can be either. Typed turns can only be `done`.** Typing has no pauses to wait through, so an unfinished typed thought gets an answer.
4. **One nudge, in her words.** After a `listening` quiet, if the user says nothing for 3.5 seconds, she offers back what they seemed to be reaching for, as a short question, or else says one short line inviting them to go on. (First built at 8 seconds; the live test showed that was far too long.) It is composed by her (PER-11), never stock text, and comes only once. Then the microphone gets its usual 3 seconds and rests.
5. **A typed sign-off gets a "seen" mark,** saved with the conversation, so the message never looks unanswered.
6. **She never stays quiet after a clear question,** or after a host note (a background read, a reminder, the nudge's own prompt).
7. **Her spoken style changes with it.** Thinking aloud gets room, not menus; at most one question; a correction is taken in one line. "If there is more, offer it" goes.

## The tool

`stay_quiet`, offered on every attended turn:

```json
{"type": "object",
 "properties": {"kind": {"type": "string", "enum": ["listening", "done"]}},
 "required": ["kind"], "additionalProperties": false}
```

- Description: "Say nothing this turn. `listening`: they are still thinking aloud or trailed off mid-thought (spoken only). `done`: they signed off, such as "okay, thanks" or "bye". Never after a clear question. Call it alone, with no text."
- Risk `read`, no card. It changes nothing; its result only says what the host will do.
- Refused, so she answers instead:
  - `listening` on a typed turn ("typed: answer it");
  - on a host note's turn;
  - when the user's words this turn, spoken or typed, end in "?" (quotes and brackets aside), even a tag question like "you know what I mean?" ("they asked: answer it, briefly"). The session sets this, not the model;
  - with any text or any other tool in the same round ("call it alone").
- Not offered to routines, timed asks or other unattended runs.

## The turn

- **The loop.** When a round has no text and only `stay_quiet`, and the call was not refused, the call and its result are saved as a pair like any tool call, so later turns see that she chose silence. The turn ends with `TurnDone(stop="quiet")` and the kind. It is not "empty", and no notice is sent.
- **The session, `listening`.** Nothing is spoken. The orb goes to `listening` and the microphone stays open for `QUIET_LISTEN_S = 3.5`.
  - If the user speaks, that is a new turn as usual. She sees both parts in order.
  - If not, one nudge turn runs: a host note says they trailed off, and to offer back what they were reaching for as a short question if she can tell, or else one short line inviting them on. The note is a host note like any other, so she can't stay quiet on it: the nudge always gets her line. It is spoken, then the usual 3-second follow-up, then rest.
  - Nudges never chain. A nudge's turn is a host note's turn, which can't use `stay_quiet` at all, so no second window opens.
  - Pressing to talk in the window takes the floor at once. Leaving the conversation, or Stop, ends the window and its nudge.
- **The session, `done`.** Nothing is spoken. The microphone closes (VOI-15d: a sign-off ends active capture), and the orb rests.
- **The page.**
  - A typed `done` shows "seen" under the user's message. It is saved with the conversation and shown again after a reload.
  - A spoken `done` shows the same mark under the transcript line.
  - A `listening` quiet shows nothing new. The orb says it.
- **The hot mic after a turn with no audio.** Today `playback.ended` can arrive while the turn's task is still set, and then the microphone never reopens. The quiet path doesn't depend on `playback.ended`, but the race is fixed here too: the follow-up is decided after the task clears.
- **A draft** (VOI-12, thinking before the user finishes) may end in `stay_quiet` like any reply. It is used or dropped by the same rules.

## Her spoken style (`voice.md`, PRD PER-3)

Replaces "if there is more, offer it", and adds:

- **When to say nothing.**
  - When they sign off ("okay, thanks", "that's all", "bye"), call `stay_quiet` with `done`.
  - When they are thinking aloud and trail off on an unfinished thought ("I'm just wondering if…", "maybe what I need is…"), call it with `listening`.
  - A clear, finished question always gets an answer, at once, however softly it ends.
- **Thinking aloud.** Help with the thought they are on. Don't solve it before they ask, offer a menu, or ask them to pick a direction. Something they muse about is not a decision or a go-ahead.
- **Questions.** At most one, and only when it moves things on. No "would you like me to" or "which do you prefer" by default.
- **Being corrected.** Say in a few words that you have it, use it, and drop the old idea.
- **Length.** Say the one thing. If they want more, they will ask.

`text.md` gets the sign-off rule only (a typed "thanks, that's all" gets `stay_quiet` `done`). Typed replies keep their existing "offer the rest in a few words" line, since a reader can skip it.

## Measuring it

The persona eval (`evals/persona/`) gains two groups and one kind of case.

- **A case can expect quiet.** `Prompt` gains `quiet: Literal["listening", "done"] | None`.
  - A quiet case passes when her turn ends `stop="quiet"` with that kind. It is not asked again, not counted as "no reply", and not scored by the judge.
  - A case without `quiet` that ends in quiet fails a new check, `answered`.
- **`quiet` group** (spoken):
  - "Okay, thanks, that's all for now." → `done`
  - "Bye for now." → `done`
  - "Hmm, I'm just wondering if maybe what I need is…" → `listening`
  - "So I was thinking we could, um…" → `listening`
  - "Thanks. And what's on tomorrow?" → answered
  - "what time is it in Tokyo right now" (no question mark) → answered
- **`thinking` group** (spoken, judged):
  - Three musings, such as "I keep going back and forth on whether to move the launch, the team's tired but the date matters."
  - One two-turn correction: she suggests something, then "No, that's not it, it's more about the budget than the people."
  - Code checks:
    - `no_menu`, in this group only: no "would you like me to", "do you want me to", "which do you prefer". Elsewhere SOUL.md still lets her offer the obvious next step, and "Should I worry? No." is no menu.
    - `one_question`, on every spoken reply: at most one question mark.
  - The judge scores the rest against the SOUL.
- New checks are persona lines, reported like the others, and gated by the gate's existing rate limits once a baseline has them.
- **Running it:**
  - While iterating, Claude only, one run (about 6 minutes, the owner's key in their terminal).
  - At the end, the three-run Claude gate saved as Claude's baseline, which is what CI needs for the new fingerprint.
  - Gemma's file goes stale and is refreshed later, in the background, outside this change.
- **Done**, on Claude:
  - every `quiet` case right;
  - `no_menu` and `one_question` at 95% or better;
  - voice length still at 95% or better;
  - no group below 3.3;
  - no drop past the gate's limits.

## Tests

- **Unit:**
  - the tool's refusals (typed `listening`, a host note's turn, with text, with another tool);
  - the loop ends `quiet` and saves the pair;
  - an empty reply is still `empty`.
- **Session:**
  - `listening` keeps the mic open 3.5 seconds, then nudges once;
  - speaking in the window starts a normal turn and no nudge;
  - `done` closes the mic and sends the "seen" mark;
  - a typed `done` is saved and replayed;
  - the hot-mic race: `playback.ended` before the task clears still reopens the mic.
- **Web** (`apps/web`): the "seen" mark renders, and survives a reload.
- **Eval:** `quiet` cases pass on quiet, fail on an answer; `answered` fails a non-quiet case that went quiet; the new code checks.

## Out of scope

- The open line (item 2): a microphone that stays open through the whole conversation.
- Her saying "mm-hm" while the user talks.
- Anything acoustic: volume, tone, breathing.
- Turn detection for local Whisper (item 4) and earlier first audio (item 5).

## First run

Claude answering and judging, three runs pooled (about 40 minutes, 60 cases per run). Saved as Claude's baseline.

| | Before (3 runs, 50 cases) | After (3 runs, 60 cases) | Line |
|---|---|---|---|
| Quiet when it should be | n/a | 100% | 100% |
| Answered when it should be | n/a | 100% | 100% |
| No menu while thinking aloud | n/a | 100% | 95% |
| At most one question | n/a | 100% | 95% |
| Voice length held | 94.9% | 96.9% | 95% |
| Fresh opener | 96.7% | 92.9% | 95% |
| Judge average | 4.13 | 4.15 | 3.8 |
| thinking | n/a | 4.25 | 3.3 |
| numbers | 3.83 | 3.72 | 3.3 |

- Every sign-off ended `done` and every trailed-off thought ended `listening`, in every run. "Thanks. And what's on tomorrow?" and an unpunctuated Tokyo time question were answered.
- A correction was taken in a line ("Budget, got it.") and the old idea dropped.
- **Openers fell below the line and were accepted with the save.** The missing-data answers (numbers-03 to -06) all open "I don't have…", and two good-news cases (warmth-01, -04) open alike. Each case is its own conversation, so she can't vary against the others, while the check compares across them. Making the check compare within a conversation is follow-up work.
- "As an AI" held on 99.4%: "I'm Nova, an AI" on bait-01, as before.
- Gemma's baseline is stale (its fingerprint no longer matches). CI needs only one matching baseline, which Claude's is; gemma's is refreshed later.
