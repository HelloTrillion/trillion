# Galaxy "first light" (Track A, first slice) — design

**Date:** 2026-09-25 · **Status:** approved (the maintainer delegated the call)
**Implements:** GAL-1, 2, 3, 4, 5 (API surface), 6, 11, and the unit half of 12.
**Why now:** it is the one Phase 0 track that needs no backend, so it gives the project something to look at while the spine lands.

## 1. What you get

A standalone package, `@hellotrillion/galaxy`, and a demo page, run with:

```bash
pnpm install
pnpm --filter @hellotrillion/galaxy dev
```

The demo shows the orb over the galaxy landscape, with:
- buttons for the six states (`idle`, `arming`, `listening`, `processing`, `speaking`, `error`)
- a microphone toggle, so the orb rides your voice while listening
- a "speak" test that plays a tone through the playback analyser
- performance-mode and theme toggles

It respects `prefers-reduced-motion`, and it announces each state in a live region (SUR-6's requirement, applied to the demo).

## 2. Package layout

```
packages/galaxy/
├── package.json         # three pinned exactly; no runtime CDN (never-list #8)
├── tsconfig.json        # extends ../../tsconfig.base.json
├── vite.config.ts       # library build + demo dev server
├── tokens.css           # GAL-11: the only place hex colours live
├── src/
│   ├── index.ts         # createGalaxy() — GAL-5
│   ├── states.ts        # GAL-2: the ONE state table (Appendix B + arming)
│   ├── easing.ts        # pure: per-frame approach toward targets
│   ├── audio/levels.ts  # pure: GAL-3 maths from a frequency array
│   ├── audio/analysers.ts   # attachAnalyser(node, role), synthetic envelope
│   ├── orb/             # shaders (Ashima simplex, fresnel), mesh, glow, rings
│   ├── landscape/       # nebula + dust, drawn in the SAME renderer (GAL-4)
│   └── perf.ts          # GAL-6: auto (FPS < 45 for 3s) + manual + reduced motion
├── demo/index.html, demo/main.ts
└── test/                # vitest: states, easing, levels, perf decision
```

## 3. Decisions
- **One WebGL context (GAL-4).** V1 used two canvases. Here a single renderer draws three passes with `autoClear = false`: the screen-space sky, the particle network (its own drifting camera), then the orb after `clearDepth()`.
- **One state table (GAL-2).** The six states and their targets live only in `states.ts`. The orb eases every parameter toward the current target each frame, using a frame-rate-independent factor: `1 - (1 - k)^(dt·60)`.
- **Arming and rings are added back.** V1's modular orb had neither. Arming is dim ember (Appendix B). The three rings fade to opacity 0.6 during `processing`.
- **Audio maths is pure** (`levels.ts`): voice level from bins 10–60%, bass from the first 6 bins, treble from 65% and above. Attack 0.45, decay 0.08. The mic role applies ×2.4 gain with a 0.10 floor. When playback cannot be analysed, a synthetic envelope is used.
- **API (GAL-5):**
  - `setState`, `attachAnalyser`, `flare`, `setPerfMode`, `setTheme` and `destroy` are implemented.
  - `addSatellite`, `removeSatellite` and `pulseArc` exist with their final signatures, but they are documented no-ops until GAL-7/8 (Phase 1). The API contract is stable from day one.
- **Colours.** Shader colours are linear-RGB floats in `states.ts`. UI hex values live only in `tokens.css`, which has dark and light themes. A lint script (`scripts/check_raw_hex.py`) fails on hex outside `tokens.css`.
- **The look matches V1's desktop galaxy** (maintainer direction, 2026-09-25): the orb, sky and network shaders and constants are ported from the predecessor, which has the same copyright holder and contains no private data. The network is eight clusters of glowing nodes joined by faint lines, plus stray nodes and dust; it brightens and swells while she speaks or thinks. Agent avatars are not part of it (the constellation, GAL-7, is Phase 1).
- **Light theme.** The sky becomes a pale dawn with the nebula as soft tints and no stars; particles and the orb glow switch from additive to normal blending so they read on a light background.

## 4. Tests
- vitest tests for the state table (every state defines every parameter), easing convergence and frame-rate independence, the audio-level maths with fixture arrays, and the perf-mode trigger (FPS below 45 for 3s, with hysteresis).
- **Deferred to a follow-up issue:** GAL-12's visual snapshots (Playwright with a fixed seed). They need a GPU-capable runner decision.

## 5. Out of scope
Constellation, arcs and supernova (GAL-7…9), the Living Mind (GAL-10), and any backend wiring.
