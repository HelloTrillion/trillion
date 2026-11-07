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
