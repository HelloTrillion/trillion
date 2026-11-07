# The Supernova: a celebration, rarely

**Date:** 2026-11-07 · **Requirements:** GAL-9, AGT-5, SUR-9, VOI-17, GAL-5, GAL-6 · **Builds on:** [speaking up](2026-10-29-speaking-up.md), [helpers panel](2026-11-05-helpers-panel.md), [your helpers](2026-11-05-your-helpers.md)

Some moments deserve more than a line in the chat: the first time a helper you made finishes its job, or a routine that has run every morning for a month. Today the orb looks the same for those as for any reminder. The Supernova is a burst of light from the orb for those moments, in three sizes.

A celebration is a way of reaching the user, so it follows the same rules as everything else that reaches them. It is decided by the attention engine, it goes out through the one door, and it never lands in the middle of a reply.

## Decisions

1. **Three tiers: small, medium, large.** Each is brighter, larger and longer than the one before. Small is a swell of light. Medium adds one shock wave. Large adds a bigger wave and lasts longest.
2. **The attention engine decides, and nothing else.** `attention/celebrate.py` looks at a notice the door is about to hand over and says which tier, if any. The door asks it, the way it asks the quiet check. No tool, plugin, connector or model output can ask for one: there is no tool for it, no argument that carries it, and a structure test fails if the word appears anywhere else in the core.
3. **It travels with a notice, through the one door (SUR-9).** A celebration is a field on the notice's delivery, so it arrives exactly when the notice does: only on a free page, never during a reply (VOI-17), once.
4. **Few, honest triggers.** Only from things that already happened and that the engine can see in the scheduler's records:
   - **Large:** a routine's 30th, 100th or 365th run that brought the user something (delivered or late; a run with nothing to say doesn't count).
   - **Medium:** the first run of a helper you made that brought something, across all its schedules.
   - **Small:** a handoff or timed ask that worked for 15 minutes or more and finished.

   The biggest that applies wins. A failed, missed or empty run never celebrates.
5. **Rare by construction.** At most one per hand-over (the largest). None for a notice more than 30 minutes old by the time it is shown: a celebration is for now. None when her voice is held for a quiet reason (quiet hours, a meeting, Focus): a burst of light is not quiet.
6. **Reduced motion gets a gentle glow.** No swell, no wave: a slow, soft brightening that fades. Performance mode keeps the glow and the swell and skips the wave, which is extra geometry to draw.
7. **No sound.** Ever.
8. **The user can turn it off.** Settings → Celebrations (under You, after Appearance), kept in this browser like the theme. Off means the orb never bursts; the notice is unchanged.

## What the user sees

- A helper they made finishes its first job: its ready line arrives, and the orb swells and sends out one ring of light, then settles.
- On the 30th morning of a routine, the ready line arrives with a larger burst.
- With reduced motion on, the orb glows softly for a few seconds instead.
- In Settings → Celebrations, one switch, on by default.

## How

- **Galaxy.** `galaxy.supernova(tier)` joins the public API (GAL-5). The maths is in `src/supernova.ts`: an envelope with a quick eased rise and a slow eased fall, per tier, and what it means for the orb each frame (extra glow, swell, wave radius and opacity). It runs on the shared renderer and the orb's own frame. A bigger burst replaces a smaller one; a smaller one during a bigger one is ignored. Unit tests at fixed times; a visual snapshot of a large burst at its peak.
- **Core.** `Celebrations` in `attention/celebrate.py` reads the notice's run and schedule from the schedule store (two new reads there: the run that wrote a notice, and how many runs up to it brought something). It only reads. The door gains `celebrate`, a `Celebrate` like `Quiet`, put in place by the runtime. A check that fails or is slow (1 s) celebrates nothing. `Delivery.celebrate` carries the tier; the session sends it on the `notice` message. A structure test allows the words "celebrate" and "supernova" in four files of the core only (the engine, the door, the session's one pass-through line, the runtime's one binding), and checks no tool, skill, connector (MCP included), channel or helper module imports the door.
- **Web.** A plug-in feature, `features/celebrations.ts`: its Settings section, and a `ctx.on` handler that hands each message to `supernova.ts`, which reads the tier off a schedule notice and fires it, unless Celebrations is off. The feature context gains `galaxy` and `replying` (optional) so a feature can draw on the orb and see whether a reply is in flight. A test checks no other file on the page calls `supernova(`. If a reply is somehow open on the page, it waits for it to end (both sides gate VOI-17); one that waited more than a minute is dropped.

## Not now

- Goals the user sets ("finish the report this week"). There is no goal record yet; once there is, finishing one is a natural large tier.
- A celebration for a watcher's find. A meeting or an urgent email is not a win.
- A server-wide switch. Celebrations are a display preference, per browser.
