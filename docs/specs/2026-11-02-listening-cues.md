# Listening cues: she says "mm-hm" while you think aloud

**Date:** 2026-11-02 · **Requirements:** PER-11 (amended), VOI-9, VOI-12, GAL-5, VOI-7 · **Builds on:** [staying quiet](2026-11-02-stay-quiet.md), [fluid turns](2026-10-24-fluid-turns.md), [echo timing](2026-10-24-echo-timing.md)

On the phone, a friend says "mm-hm" or "yeah" while you talk, so you know they're with you. Trillion stays completely silent until you finish, which makes even a fast conversation feel like taking turns. This adds short listening cues in her own voice at natural pauses while the user is mid-thought, with a small nod of the orb.

The live test also showed that the quiet `listening` window from [staying quiet](2026-11-02-stay-quiet.md) never opens with the owner's setup (Flux hearing, her voice made in the core, barge-in on). It also never ends once any sound has been heard. Both are fixed here.

## Decisions

1. **Four cues, in her voice:** "mm-hm", "mm", "yeah" and "go on". Each is made once per voice with the session's text-to-speech and kept in memory, so it plays at once with no model call.
2. **PER-11 is amended narrowly.** Short listening cues from that fixed set may play while the user is talking. Anything else she says is still composed by her. A cue is not an answer and not agreement.
3. **Only with hearing that judges turns (Flux) and her voice made in the core** (not the system voice, which plays in the page and has no audio to send). Otherwise there are no cues.
4. **Sparing by design.** A cue plays only in a real mid-turn pause, and often a pause gets nothing.
5. **The orb nods** with every cue: one soft `flare`.
6. **A cue is hers, never the user's words.** If the recognizer transcribes a cue's echo, those words are dropped, by the same timing judgement that tells her reply's echo from the user (echo timing).
7. **A switch:** Settings → Hearing → "Say 'mm-hm' while I talk". On by default, and shown only when cues are possible.

## When a cue plays

All of these must hold, checked when a Flux `Update` arrives during the user's turn:

- The user's turn is open: a listener is taking their words, and it isn't her reply or an overlap watch.
- At least 4 seconds of their speech in this turn so far (`listener.heard_s`).
- The last word ended between 0.5 and 1.5 seconds ago, by the recognizer's word times on the stream clock. That's a pause, not a stop.
- The turn is not likely over:
  - Flux's `end_of_turn_confidence` on the latest message is below 0.3. Flux sends an `Update` about every 0.25 s and its confidence climbs through any pause, so a rise below 0.3 doesn't block a cue;
  - no `EagerEndOfTurn` since the last word;
  - and the last words do not end the thought in an obvious way (a question mark, or a sign-off like "thanks").
- At least 8 seconds since the last cue, and at most 3 cues in one turn.
- A coin flip says yes (`CUE_CHANCE = 0.5`), so many pauses get nothing.

The cue is picked at random from the four, never the same one twice in a row. "go on" is used only once the turn has run past 12 seconds of speech. The pick is weighted: mm-hm 0.4, mm 0.3, yeah 0.2, go on 0.1.

## What each turn records

A spoken turn's row in `turn_timings` keeps `cues_json` (migration 017), so `trillion doctor --trace` can say why cues did or didn't play: `{"sent": n, "played": n, "skipped": {reason: count}}`. `played` counts the page's `cue.started`. Each recognizer report that got no cue is counted once, by the first rule that said no:

- the session's: `watching` (an overlap watch), `busy` (her turn or an overlap), `off` (the switch, mute, or no page audio), `not_made` (her cues are not made yet);
- the plan's: `no_words`, `short`, `max`, `gap`, `pause`, `confident`, `eager`, `ends`, `decided` (this pause already had its flip), `coin`.

Only counts and rule names: nothing the user said. NULL for typed turns, and when cues aren't possible.

## How it plays

- **Core to page:** `{"type": "cue.play", "id": N, "cue": "mm-hm", "sample_rate": R}`, followed by one binary frame of kind `0x02` (`<BII` header: kind 2, id N, seq 0, then PCM16). The cue's PCM is trimmed of leading and trailing silence when it is made.
- **The page:**
  - It plays the frame through its own `GainNode` at 0.5, into the same `AudioContext` as her voice, so the browser's echo cancellation knows it.
  - It does not use the reply path: no `playback.*` messages, no orb "speaking" level, and nothing to do with turn bookkeeping.
  - It calls `galaxy.flare(0.3)`.
  - It sends `{"type": "cue.started", "id": N, "in_ms": ...}` when the audio starts. A cue for an id it has no audio for, or one arriving while her reply plays, is dropped.
- **Echo:** the listener taking the user's words gets a cue timeline on its clock. Each cue is a "sentence" (written and spoken = the cue, with loudness levels from its PCM), placed when `cue.started` arrives. `listener.judge_by(...)` then drops recognized words that are hers. With no judge yet, the first cue creates one.
- **The cues are made** when a session opens with cues possible, and again when the voice changes. If making one fails, no cues play, and the failure is logged without the audio.

## The quiet window, fixed

- **Watch mode.** When the turn ends quiet `listening` and an overlap watch is open, the watch becomes the quiet window: `QUIET_LISTEN_S`, nudging once on silence. It is not the 3-second follow-up. The same holds for any path that opens a follow-up after her turn: after a quiet `listening`, it is the quiet window.
- **Silence means no words.** In the quiet window, "nothing" means no recognized words for `QUIET_LISTEN_S`. A breath, a cough or her echo tail doesn't hold the window open. Words, or any of the existing turn rules, end it as usual.
- **"listening" only for an unfinished sentence.** The `stay_quiet` tool description says `listening` is for an unfinished sentence. A finished one, even a vague "I just don't know", gets an answer. Prompt files are unchanged, so no new baseline is needed.

## Tests

- **Cue decision (pure function):** each condition alone blocks a cue. All met with a fixed random seed plays one. Two in 8 seconds, a fourth in one turn, and the same cue twice in a row all never happen. "go on" never plays before 12 seconds.
- **Session:**
  - A Flux pause with low confidence sends `cue.play` and a frame of kind 2.
  - An echo of the cue transcribed as "mm-hm" is not in the user's turn.
  - No cues with the switch off, with the system voice, with Whisper, or during her reply.
- **Quiet window:**
  - Watch mode turns the watch into the 8-second window and nudges once.
  - A cough's worth of sound and no words still nudges.
  - Words in the window start a turn.
- **Web:**
  - A kind-2 frame plays through the cue gain without `playback.*` messages, and flares the orb.
  - `cue.started` is sent.
  - The switch shows and saves.
- **The tool description:** says `listening` is for an unfinished sentence.

## Out of scope

- Cues while she is speaking. That's barge-in, which exists.
- Choosing cues with a model.
- Cues in other languages: only the four English ones, so no cues with the "multi" Flux choice.
- Local Whisper hearing.
