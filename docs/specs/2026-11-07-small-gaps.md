# Small gaps

**Date:** 2026-11-07 · **Requirements:** GAL-8, VOI-15, OPS-3

Three small requirements that each had a seam or a promise and nothing behind it.

## Energy arcs (GAL-8)

### Why

When she uses a tool, a chip appears in the conversation ("Remembering …"). The orb shows she is working, but nothing ties the two together. GAL-5 already promised `pulseArc(targetId)`; it did nothing.

### Decisions

1. **The galaxy draws it; the page says where.** `pulseArc(targetId)` keeps its signature. A new option, `arcTarget(targetId)`, returns the target's point in canvas pixels, or null when it is not on screen. The galaxy never reads the page.
2. **Subtle.** A thread of light 2 px wide, in the working colour, leaves the edge of the orb's core and runs to the chip in about half a second, a faint trail behind it; the whole arc is gone in 0.9 s. It bows upward. At most three at once.
3. **Only when both ends are seen.** No arc when the page is hidden, the orb is outside the window, the chip is missing or empty (a hidden sidebar), or the chip's middle is scrolled out of the transcript or off the window (a phone sheet pulled down).
4. **Motion for its own sake goes first.** No arc in performance mode or with reduced motion (GAL-6). Perf mode switching on clears any in flight.
5. **It lands on the chip's nearest point to the orb**, so it reaches the chip's edge, not its middle. On a desktop the chip is in the sidebar's glass panel, so the last stretch shows through the glass.

### What the user sees

She starts a tool: the chip appears and a faint pulse runs from the orb to it, once.

### How

- `packages/galaxy/src/arc.ts`: the maths, in CSS pixels (start on the core's edge, the bowed curve, the ribbon, the head and fade timings, `arcsAllowed`).
- `packages/galaxy/src/arcs.ts`: one flat orthographic scene over the orb, a mesh per arc, drawn only while one lives.
- `apps/web/src/arcs.ts`: `arcTarget` from measured boxes and `orbSeen`. `main.ts` marks each tool chip with `data-call`, keeps where the orb was placed, and on `tool.start` calls `pulseArc` on the next frame, once the chip is drawn and scrolled to.

### Not now

- An arc for a helper or a background read: they have no chip.
- Arcs that follow a chip while it scrolls.

## The speaker-embedding seam (VOI-15)

### Why

Phase 1's speaker check (§5.3a: SPN-14a, SPN-18, PER-13) scores each utterance against the user's voice. If capture does not hand over a per-utterance signal from the start, that work grows a second capture path. The `Utterance` had a `speaker` field and nothing filled it.

### Decisions

1. **A protocol, beside the recognizer's.** `SpeakerEmbedder` in `voice/types.py`: `embed(pcm, *, start_s, words)` returns a `SpeakerEmbedding` (`vector`, `model`) or None when it cannot tell. No vendor's vocabulary.
2. **The Shell's stub never knows.** `UnknownSpeaker` returns None. The runtime holds one (`Runtime.speakers`); Phase 1 replaces it there, and nothing in capture changes.
3. **Once per committed utterance.** The listener asks when `end()` collects words, after the recognizer is done. Sound with no words, an abandoned listen and her echo alone are never asked about.
4. **It travels with the words.** `Utterance(text, speaker)`: the transcript and who said it come out of capture together.
5. **What it is given.** The audio the recognizer was sent, its latest 30 seconds at most, with where it starts on the recognizer's clock, and the user's timed words (her echo left out once a judge is set, #102), so a real embedder can cut to their speech.
6. **It never holds up a turn.** One second at most; a failure or a stall is "unknown". Its log line names the embedder and the error's class, nothing else.
7. **Biometric.** An embedding is never logged, saved or sent anywhere. It lives as long as its utterance.

### What the user sees

Nothing. She still says she can't tell voices apart, which stays true until Phase 1.

### Not now

- Any real embedder, enrolment or scoring (Phase 1).
- Passing the embedding into the turn: it stops at `_heard` in `api/session.py`, where the speaker check will read it.
