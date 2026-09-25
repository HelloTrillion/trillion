# Galaxy "First Light" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `@hellotrillion/galaxy` renders the orb over a galaxy landscape in one WebGL context, eases between six states from one table, reacts to the microphone and to playback, and ships a demo page anyone can open with one command.

**Architecture:** The pure maths lives in its own modules and is unit-tested with vitest: the state table, easing, audio levels, performance governor and seeded random. The three.js modules (`orb/`, `landscape/`) are thin and take already-computed numbers each frame. `createGalaxy()` in `index.ts` owns the renderer, the frame loop and the public API.

**Tech Stack:** TypeScript (strict), three.js pinned exactly, Vite (library build and demo server), vitest.

**Spec:** `docs/specs/2026-09-25-galaxy-first-light-design.md`. **Depends on:** Plan 01 Tasks 2 and 4 (workspace root, raw-hex check).

**Conventions:** Each commit uses `git commit -s`, and its message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No hex colours outside `tokens.css`, and no element ids that are valid hex (`#add`, `#bad`, `#fade`…). No CDN: three.js comes from npm and is bundled.

---

## File map (all under `packages/galaxy/`)

| File | Responsibility |
|---|---|
| `package.json`, `tsconfig.json`, `tsconfig.build.json`, `vite.config.ts`, `vitest.config.ts` | Package and build |
| `tokens.css` | GAL-11 design tokens, dark and light |
| `src/states.ts` | GAL-2: the one state table and labels |
| `src/easing.ts` | Frame-rate-independent approach |
| `src/random.ts` | Seeded PRNG (deterministic layouts for snapshots) |
| `src/audio/levels.ts` | GAL-3 pure maths |
| `src/audio/analysers.ts` | Web Audio wiring: `attachAnalyser`, per-frame reads |
| `src/perf.ts` | GAL-6: governor, quality settings, motion scale |
| `src/shaders/noise.ts` | 3D simplex noise GLSL (MIT, attributed) |
| `src/orb/orb.ts` | Core wireframe, glow shell, three rings |
| `src/landscape/landscape.ts` | Nebula sky and dust, in the same scene |
| `src/index.ts` | `createGalaxy()`: renderer, loop, public API |
| `test/*.test.ts` | Unit tests |
| `demo/index.html`, `demo/main.ts`, `demo/demo.css` | The demo page |

---

### Task 1: Package scaffold

**Files:** Create `packages/galaxy/{package.json,tsconfig.json,tsconfig.build.json,vite.config.ts,vitest.config.ts}`. Delete `packages/galaxy/.gitkeep`.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "@hellotrillion/galaxy",
  "version": "0.0.1",
  "description": "Trillion's visual engine: the orb, the landscape, and the states between them.",
  "license": "Apache-2.0",
  "type": "module",
  "main": "./dist/galaxy.js",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": { "types": "./dist/index.d.ts", "import": "./dist/galaxy.js" },
    "./tokens.css": "./tokens.css"
  },
  "files": ["dist", "tokens.css"],
  "sideEffects": ["*.css"],
  "scripts": {
    "dev": "vite",
    "build": "vite build && tsc -p tsconfig.build.json",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  }
}
```

- [ ] **Step 2: Add the dependencies (three pinned exactly)**

Run from the repo root:
```bash
pnpm --filter @hellotrillion/galaxy add three@0.186.1 --save-exact
pnpm --filter @hellotrillion/galaxy add -D @types/three typescript@~5.9 vite vitest
```
Expected: `package.json` gains `"three": "0.186.1"` (no caret) and the four dev dependencies.

- [ ] **Step 3: Create the configs**

`tsconfig.json`:
```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": { "noEmit": true, "types": ["vite/client"] },
  "include": ["src", "test", "demo", "vite.config.ts", "vitest.config.ts"]
}
```

`tsconfig.build.json`:
```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": {
    "noEmit": false,
    "emitDeclarationOnly": true,
    "outDir": "dist",
    "rootDir": "src",
    "types": ["vite/client"]
  },
  "include": ["src"]
}
```

`vite.config.ts`:
```ts
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const here = (p: string): string => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig(({ command }) =>
  command === 'serve'
    ? {
        root: here('./demo'),
        // Loopback only, like the app itself (never-list #6).
        server: { host: '127.0.0.1', port: 5173 },
      }
    : {
        build: {
          lib: { entry: here('./src/index.ts'), formats: ['es'], fileName: 'galaxy' },
          rollupOptions: { external: ['three'] },
          sourcemap: true,
        },
      },
);
```

`vitest.config.ts`:
```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: { include: ['test/**/*.test.ts'], environment: 'node' },
});
```

- [ ] **Step 4: Verify and commit**

Run: `pnpm --filter @hellotrillion/galaxy exec tsc --noEmit`
Expected: exits 0 (there's no source yet; TS may warn "No inputs were found". If so, continue: Task 2 adds inputs).

```bash
git rm -q packages/galaxy/.gitkeep
git add packages/galaxy pnpm-lock.yaml
git commit -s -m "Scaffold @hellotrillion/galaxy with three pinned and no CDN

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The state table (GAL-2)

**Files:** Create `src/states.ts` and `test/states.test.ts`.

- [ ] **Step 1: Write the failing test**

`test/states.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { ORB_STATE_NAMES, ORB_STATES, STATE_LABELS, type OrbParams } from '../src/states';

const KEYS: (keyof OrbParams)[] = [
  'color', 'colorSecondary', 'opacity', 'fresnelPower', 'noiseSpeed',
  'displacementAmp', 'colorMix', 'glowOpacity', 'rotationSpeed', 'ringOpacity', 'colorOscillation',
];

describe('ORB_STATES', () => {
  it('defines exactly the six PRD states', () => {
    expect([...ORB_STATE_NAMES].sort()).toEqual(
      ['arming', 'error', 'idle', 'listening', 'processing', 'speaking'],
    );
    expect(Object.keys(ORB_STATES).sort()).toEqual([...ORB_STATE_NAMES].sort());
  });

  it('gives every state every parameter, with colours in 0..1', () => {
    for (const name of ORB_STATE_NAMES) {
      const s = ORB_STATES[name];
      for (const k of KEYS) expect(s[k], `${name}.${k}`).toBeDefined();
      for (const c of [...s.color, ...s.colorSecondary]) {
        expect(c).toBeGreaterThanOrEqual(0);
        expect(c).toBeLessThanOrEqual(1);
      }
    }
  });

  it('matches Appendix B where it is specific', () => {
    expect(ORB_STATES.idle.color).toEqual([0.07, 0.33, 0.26]);
    expect(ORB_STATES.idle.opacity).toBe(0.55);
    expect(ORB_STATES.idle.displacementAmp).toBe(0.08);
    expect(ORB_STATES.arming.color).toEqual([0.55, 0.4, 0.12]);
    expect(ORB_STATES.listening.color).toEqual([1.0, 0.78, 0.15]);
    expect(ORB_STATES.listening.colorSecondary).toEqual([1.0, 0.9, 0.45]);
    expect(ORB_STATES.listening.displacementAmp).toBe(0.45);
    expect(ORB_STATES.listening.glowOpacity).toBe(0.34);
    expect(ORB_STATES.processing.colorSecondary).toEqual([0.65, 0.55, 0.98]);
    expect(ORB_STATES.speaking.color).toEqual([0.18, 0.85, 0.65]);
    expect(ORB_STATES.speaking.displacementAmp).toBe(0.8);
    expect(ORB_STATES.error.color).toEqual([0.94, 0.27, 0.27]);
  });

  it('shows rings and oscillates colour only while processing', () => {
    for (const name of ORB_STATE_NAMES) {
      const expected = name === 'processing';
      expect(ORB_STATES[name].ringOpacity > 0, name).toBe(expected);
      expect(ORB_STATES[name].colorOscillation > 0, name).toBe(expected);
    }
    expect(ORB_STATES.processing.ringOpacity).toBe(0.6);
  });

  it('has a human label for every state (SUR-6 live region)', () => {
    for (const name of ORB_STATE_NAMES) expect(STATE_LABELS[name].length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --filter @hellotrillion/galaxy test`
Expected: FAIL, `Cannot find module '../src/states'`.

- [ ] **Step 3: Implement `src/states.ts`**

```ts
/**
 * The one place orb states are defined (GAL-2). Every visual parameter
 * eases toward the current state's target each frame; nothing else in the
 * package may hardcode a per-state value.
 *
 * Colours are 0..1 RGB floats passed straight to the shaders.
 */

export type OrbStateName = 'idle' | 'arming' | 'listening' | 'processing' | 'speaking' | 'error';

export const ORB_STATE_NAMES: readonly OrbStateName[] = [
  'idle', 'arming', 'listening', 'processing', 'speaking', 'error',
];

export type RGB = readonly [number, number, number];

export interface OrbParams {
  readonly color: RGB;
  readonly colorSecondary: RGB;
  readonly opacity: number;
  readonly fresnelPower: number;
  readonly noiseSpeed: number;
  readonly displacementAmp: number;
  /** Base mix between color and colorSecondary. */
  readonly colorMix: number;
  readonly glowOpacity: number;
  readonly rotationSpeed: number;
  /** Opacity of the three orbital rings. */
  readonly ringOpacity: number;
  /** 0..1: how strongly the colour mix swings on a 2s cycle, plus a pulse. */
  readonly colorOscillation: number;
}

export const ORB_STATES: Readonly<Record<OrbStateName, OrbParams>> = {
  idle: {
    color: [0.07, 0.33, 0.26], colorSecondary: [0.07, 0.33, 0.26],
    opacity: 0.55, fresnelPower: 3.0, noiseSpeed: 0.2, displacementAmp: 0.08,
    colorMix: 0, glowOpacity: 0.04, rotationSpeed: 0.08, ringOpacity: 0, colorOscillation: 0,
  },
  // The mic was requested but no audio has arrived yet: honest feedback.
  arming: {
    color: [0.55, 0.4, 0.12], colorSecondary: [0.55, 0.4, 0.12],
    opacity: 0.6, fresnelPower: 3.0, noiseSpeed: 0.3, displacementAmp: 0.12,
    colorMix: 0, glowOpacity: 0.08, rotationSpeed: 0.1, ringOpacity: 0, colorOscillation: 0,
  },
  // Gold: the recording light. Nothing else in the cosmos is this warm.
  listening: {
    color: [1.0, 0.78, 0.15], colorSecondary: [1.0, 0.9, 0.45],
    opacity: 0.95, fresnelPower: 2.2, noiseSpeed: 0.55, displacementAmp: 0.45,
    colorMix: 0, glowOpacity: 0.34, rotationSpeed: 0.15, ringOpacity: 0, colorOscillation: 0,
  },
  processing: {
    color: [0.18, 0.83, 0.66], colorSecondary: [0.65, 0.55, 0.98],
    opacity: 0.5, fresnelPower: 2.8, noiseSpeed: 0.8, displacementAmp: 0.2,
    colorMix: 0, glowOpacity: 0.06, rotationSpeed: 0.35, ringOpacity: 0.6, colorOscillation: 1,
  },
  speaking: {
    color: [0.18, 0.85, 0.65], colorSecondary: [0.25, 0.92, 0.75],
    opacity: 0.65, fresnelPower: 2.2, noiseSpeed: 0.7, displacementAmp: 0.8,
    colorMix: 0, glowOpacity: 0.18, rotationSpeed: 0.2, ringOpacity: 0, colorOscillation: 0,
  },
  error: {
    color: [0.94, 0.27, 0.27], colorSecondary: [0.94, 0.27, 0.27],
    opacity: 0.65, fresnelPower: 4.0, noiseSpeed: 1.0, displacementAmp: 0.12,
    colorMix: 0, glowOpacity: 0.06, rotationSpeed: 0.03, ringOpacity: 0, colorOscillation: 0,
  },
};

/** Text for the accessibility live region (SUR-6). Not spoken by the agent. */
export const STATE_LABELS: Readonly<Record<OrbStateName, string>> = {
  idle: 'Idle',
  arming: 'Waiting for the microphone',
  listening: 'Listening',
  processing: 'Thinking',
  speaking: 'Speaking',
  error: 'Error',
};
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `pnpm --filter @hellotrillion/galaxy test`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/galaxy/src/states.ts packages/galaxy/test/states.test.ts
git commit -s -m "Define the six orb states in exactly one place (GAL-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Easing

**Files:** Create `src/easing.ts` and `test/easing.test.ts`.

- [ ] **Step 1: Write the failing test**

`test/easing.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { approach, approachFactor, stepParams } from '../src/easing';
import { ORB_STATES } from '../src/states';

describe('approachFactor', () => {
  it('is 0 for no time and never exceeds 1', () => {
    expect(approachFactor(0.07, 0)).toBe(0);
    expect(approachFactor(0.07, 1000)).toBeLessThanOrEqual(1);
  });

  it('is frame-rate independent', () => {
    let a = 0;
    for (let i = 0; i < 60; i++) a = approach(a, 1, 0.07, 1 / 60);
    let b = 0;
    for (let i = 0; i < 30; i++) b = approach(b, 1, 0.07, 1 / 30);
    expect(Math.abs(a - b)).toBeLessThan(1e-9);
  });
});

describe('stepParams', () => {
  it('converges on the target state', () => {
    let p = ORB_STATES.idle;
    for (let i = 0; i < 600; i++) p = stepParams(p, ORB_STATES.listening, 1 / 60);
    expect(p.opacity).toBeCloseTo(ORB_STATES.listening.opacity, 5);
    expect(p.color[0]).toBeCloseTo(1.0, 5);
  });

  it('moves every numeric field part-way in one frame', () => {
    const p = stepParams(ORB_STATES.idle, ORB_STATES.error, 1 / 60);
    expect(p.opacity).toBeGreaterThan(ORB_STATES.idle.opacity);
    expect(p.opacity).toBeLessThan(ORB_STATES.error.opacity);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --filter @hellotrillion/galaxy test -- easing`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/easing.ts`**

```ts
import type { OrbParams, RGB } from './states';

/** Default per-60fps-frame approach rate for state parameters. */
export const STATE_EASE = 0.07;

/**
 * Fraction of the remaining distance to cover in `dt` seconds, such that
 * the curve is identical at any frame rate: 1 - (1 - k)^(dt·60).
 */
export function approachFactor(k: number, dt: number): number {
  if (dt <= 0) return 0;
  return Math.min(1, 1 - Math.pow(1 - k, dt * 60));
}

export function approach(current: number, target: number, k: number, dt: number): number {
  return current + (target - current) * approachFactor(k, dt);
}

function approachRGB(c: RGB, t: RGB, f: number): RGB {
  return [c[0] + (t[0] - c[0]) * f, c[1] + (t[1] - c[1]) * f, c[2] + (t[2] - c[2]) * f];
}

export function stepParams(
  current: OrbParams,
  target: OrbParams,
  dt: number,
  k: number = STATE_EASE,
): OrbParams {
  const f = approachFactor(k, dt);
  const n = (a: number, b: number): number => a + (b - a) * f;
  return {
    color: approachRGB(current.color, target.color, f),
    colorSecondary: approachRGB(current.colorSecondary, target.colorSecondary, f),
    opacity: n(current.opacity, target.opacity),
    fresnelPower: n(current.fresnelPower, target.fresnelPower),
    noiseSpeed: n(current.noiseSpeed, target.noiseSpeed),
    displacementAmp: n(current.displacementAmp, target.displacementAmp),
    colorMix: n(current.colorMix, target.colorMix),
    glowOpacity: n(current.glowOpacity, target.glowOpacity),
    rotationSpeed: n(current.rotationSpeed, target.rotationSpeed),
    ringOpacity: n(current.ringOpacity, target.ringOpacity),
    colorOscillation: n(current.colorOscillation, target.colorOscillation),
  };
}
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `pnpm --filter @hellotrillion/galaxy test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/galaxy/src/easing.ts packages/galaxy/test/easing.test.ts
git commit -s -m "Ease between states at the same speed on any frame rate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Audio level maths (GAL-3)

**Files:** Create `src/audio/levels.ts` and `test/levels.test.ts`.

- [ ] **Step 1: Write the failing test**

`test/levels.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import {
  ATTACK, DECAY, MIC_FLOOR, SILENT, applyRole, bandLevels, smooth, syntheticListening,
  syntheticSpeech,
} from '../src/audio/levels';

const bins = (fill: (i: number) => number, n = 128): Uint8Array =>
  Uint8Array.from({ length: n }, (_, i) => fill(i));

describe('bandLevels', () => {
  it('reads silence as zero', () => {
    expect(bandLevels(bins(() => 0))).toEqual(SILENT);
  });

  it('reads a full spectrum as one in every band', () => {
    const l = bandLevels(bins(() => 255));
    expect(l.level).toBeCloseTo(1);
    expect(l.bass).toBeCloseTo(1);
    expect(l.treble).toBeCloseTo(1);
  });

  it('uses bins 10–60% for voice, the first 6 for bass, 65%+ for treble', () => {
    // Energy only in bins 0..5 (bass) — voice band starts at 12 for 128 bins.
    const bassOnly = bandLevels(bins((i) => (i < 6 ? 255 : 0)));
    expect(bassOnly.bass).toBeCloseTo(1);
    expect(bassOnly.level).toBe(0);
    expect(bassOnly.treble).toBe(0);
    const trebleOnly = bandLevels(bins((i) => (i >= 84 ? 255 : 0)));
    expect(trebleOnly.treble).toBeCloseTo(1);
    expect(trebleOnly.level).toBe(0);
  });

  it('treats an empty array as silence', () => {
    expect(bandLevels(new Uint8Array(0))).toEqual(SILENT);
  });
});

describe('applyRole', () => {
  it('boosts the mic ×2.4 with a 0.10 floor, capped at 1', () => {
    expect(applyRole({ level: 0.1, bass: 0, treble: 0 }, 'mic').level).toBeCloseTo(0.24);
    expect(applyRole(SILENT, 'mic').level).toBe(MIC_FLOOR);
    expect(applyRole({ level: 0.9, bass: 0, treble: 0 }, 'mic').level).toBe(1);
  });

  it('leaves playback alone', () => {
    const l = { level: 0.3, bass: 0.2, treble: 0.1 };
    expect(applyRole(l, 'playback')).toEqual(l);
  });
});

describe('smooth', () => {
  it('attacks fast and decays slowly', () => {
    const up = smooth(SILENT, { level: 1, bass: 1, treble: 1 });
    expect(up.level).toBeCloseTo(ATTACK);
    const down = smooth({ level: 1, bass: 1, treble: 1 }, SILENT);
    expect(down.level).toBeCloseTo(1 - DECAY);
  });
});

describe('synthetic envelopes', () => {
  it('are deterministic and bounded', () => {
    for (const t of [0, 0.37, 1.5, 12.25]) {
      const a = syntheticSpeech(t);
      expect(syntheticSpeech(t)).toEqual(a);
      for (const v of [a.level, a.bass, a.treble]) {
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThanOrEqual(1);
      }
      const l = syntheticListening(t);
      expect(l.level).toBeGreaterThanOrEqual(0);
      expect(l.level).toBeLessThanOrEqual(1);
    }
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --filter @hellotrillion/galaxy test -- levels`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/audio/levels.ts`**

```ts
/**
 * GAL-3 audio maths. Pure: input is an analyser's byte frequency data
 * (0..255 per bin), output is 0..1 levels. Constants are from Appendix B/C.
 */

export interface Levels {
  readonly level: number;
  readonly bass: number;
  readonly treble: number;
}

export type AnalyserRole = 'mic' | 'playback';

export const SILENT: Levels = { level: 0, bass: 0, treble: 0 };
export const ATTACK = 0.45;
export const DECAY = 0.08;
export const MIC_GAIN = 2.4;
export const MIC_FLOOR = 0.1;
const MIC_BASS_GAIN = 2.0;

function meanRange(bins: ArrayLike<number>, start: number, end: number): number {
  if (end <= start) return 0;
  let sum = 0;
  for (let i = start; i < end; i++) sum += bins[i] ?? 0;
  return sum / ((end - start) * 255);
}

export function bandLevels(bins: ArrayLike<number>): Levels {
  const n = bins.length;
  if (n === 0) return SILENT;
  return {
    level: meanRange(bins, Math.max(1, Math.floor(n * 0.1)), Math.floor(n * 0.6)),
    bass: meanRange(bins, 0, Math.min(6, n)),
    treble: meanRange(bins, Math.floor(n * 0.65), n),
  };
}

const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));

/** Mic input is quieter than playback; lift it so the orb visibly listens. */
export function applyRole(l: Levels, role: AnalyserRole): Levels {
  if (role === 'playback') return l;
  return {
    level: Math.max(MIC_FLOOR, clamp01(l.level * MIC_GAIN)),
    bass: clamp01(l.bass * MIC_BASS_GAIN),
    treble: clamp01(l.treble * MIC_GAIN),
  };
}

function smoothOne(prev: number, next: number): number {
  const k = next > prev ? ATTACK : DECAY;
  return prev + (next - prev) * k;
}

/** Breathe instead of twitch: fast attack, slow decay. */
export function smooth(prev: Levels, next: Levels): Levels {
  return {
    level: smoothOne(prev.level, next.level),
    bass: smoothOne(prev.bass, next.bass),
    treble: smoothOne(prev.treble, next.treble),
  };
}

/** Speech-like envelope for when playback cannot be analysed. Deterministic. */
export function syntheticSpeech(t: number): Levels {
  const wave = Math.sin(t * 4.3) * 0.4 + Math.sin(t * 11.7) * 0.25 + 0.5;
  const flutter = Math.sin(t * 23.1) * 0.05;
  return {
    level: clamp01((wave + flutter) * 0.55),
    bass: clamp01(Math.sin(t * 1.7) * 0.35 + 0.35),
    treble: clamp01(Math.sin(t * 9.1) * 0.2 + 0.25),
  };
}

/** Gentle pulse while listening with no mic attached (demo, before permission). */
export function syntheticListening(t: number): Levels {
  return {
    level: clamp01(0.18 + 0.12 * Math.sin(t * 2.6) + 0.06 * Math.sin(t * 7.3)),
    bass: clamp01(0.08 + 0.06 * Math.sin(t * 1.4)),
    treble: 0.05,
  };
}
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `pnpm --filter @hellotrillion/galaxy test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/galaxy/src/audio/levels.ts packages/galaxy/test/levels.test.ts
git commit -s -m "Turn analyser bins into voice, bass and treble levels (GAL-3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Performance governor and seeded random (GAL-6)

**Files:** Create `src/perf.ts`, `src/random.ts`, `test/perf.test.ts`, `test/random.test.ts`.

- [ ] **Step 1: Write the failing tests**

`test/perf.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { PerfGovernor, motionScale, qualityFor } from '../src/perf';

describe('PerfGovernor', () => {
  it('turns on after 3s below 45fps, not before', () => {
    const g = new PerfGovernor();
    expect(g.sample(30, 0)).toBe(false);
    expect(g.sample(30, 2900)).toBe(false);
    expect(g.sample(30, 3000)).toBe(true);
  });

  it('forgives a brief dip', () => {
    const g = new PerfGovernor();
    g.sample(30, 0);
    g.sample(60, 1000);
    expect(g.sample(30, 3500)).toBe(false);
  });

  it('latches once on, so quality does not flap', () => {
    const g = new PerfGovernor();
    g.sample(30, 0);
    g.sample(30, 3000);
    expect(g.sample(120, 60_000)).toBe(true);
  });

  it('lets a manual choice override auto in both directions', () => {
    const g = new PerfGovernor();
    g.setMode('on');
    expect(g.sample(120, 0)).toBe(true);
    g.setMode('off');
    g.sample(10, 0);
    expect(g.sample(10, 10_000)).toBe(false);
    g.setMode('auto');
    expect(g.sample(10, 20_000)).toBe(true);
  });
});

describe('qualityFor', () => {
  it('caps pixel ratio at 2 and drops to 1 in perf mode', () => {
    expect(qualityFor(false, 3).pixelRatio).toBe(2);
    expect(qualityFor(true, 3).pixelRatio).toBe(1);
  });

  it('halves detail and dust in perf mode', () => {
    const full = qualityFor(false, 1);
    const lite = qualityFor(true, 1);
    expect(full.orbDetail).toBe(24);
    expect(lite.orbDetail).toBeLessThan(full.orbDetail);
    expect(lite.dustCount).toBeLessThan(full.dustCount);
  });
});

describe('motionScale', () => {
  it('slows everything under prefers-reduced-motion', () => {
    expect(motionScale(false)).toBe(1);
    expect(motionScale(true)).toBeLessThan(0.5);
  });
});
```

`test/random.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { seededRandom } from '../src/random';

describe('seededRandom', () => {
  it('repeats for the same seed and differs across seeds', () => {
    const a = seededRandom(7);
    const b = seededRandom(7);
    const c = seededRandom(8);
    const sa = [a(), a(), a()];
    expect([b(), b(), b()]).toEqual(sa);
    expect([c(), c(), c()]).not.toEqual(sa);
    for (const v of sa) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --filter @hellotrillion/galaxy test`
Expected: FAIL for perf and random (module not found).

- [ ] **Step 3: Implement**

`src/perf.ts`:
```ts
/** GAL-6: automatic and manual performance mode, and reduced motion. */

export type PerfMode = 'auto' | 'on' | 'off';

export const PERF_FPS_THRESHOLD = 45;
export const PERF_HOLD_MS = 3000;

export class PerfGovernor {
  private mode: PerfMode;
  private lowSince: number | null = null;
  private autoLatched = false;

  constructor(mode: PerfMode = 'auto') {
    this.mode = mode;
  }

  setMode(mode: PerfMode): void {
    this.mode = mode;
    this.lowSince = null;
  }

  getMode(): PerfMode {
    return this.mode;
  }

  /** Feed the current fps estimate; returns whether perf mode is active. */
  sample(fps: number, nowMs: number): boolean {
    if (this.mode === 'on') return true;
    if (this.mode === 'off') return false;
    if (this.autoLatched) return true;
    if (fps < PERF_FPS_THRESHOLD) {
      this.lowSince ??= nowMs;
      if (nowMs - this.lowSince >= PERF_HOLD_MS) this.autoLatched = true;
    } else {
      this.lowSince = null;
    }
    return this.autoLatched;
  }
}

export interface Quality {
  readonly pixelRatio: number;
  readonly orbDetail: number;
  readonly dustCount: number;
}

export function qualityFor(perfActive: boolean, devicePixelRatio: number): Quality {
  return perfActive
    ? { pixelRatio: 1, orbDetail: 12, dustCount: 200 }
    : { pixelRatio: Math.min(2, devicePixelRatio), orbDetail: 24, dustCount: 500 };
}

/** Multiplier on every time-driven motion; honours prefers-reduced-motion. */
export function motionScale(reducedMotion: boolean): number {
  return reducedMotion ? 0.25 : 1;
}
```

`src/random.ts`:
```ts
/** mulberry32: small, fast, seeded. Layouts are reproducible for snapshots. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `pnpm --filter @hellotrillion/galaxy test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/galaxy/src/perf.ts packages/galaxy/src/random.ts packages/galaxy/test/perf.test.ts packages/galaxy/test/random.test.ts
git commit -s -m "Drop to performance mode on sustained low fps, and honour reduced motion (GAL-6)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Design tokens (GAL-11)

**Files:** Create `tokens.css`.

- [ ] **Step 1: Create `packages/galaxy/tokens.css`**

```css
/*
 * Trillion design tokens (GAL-11). The only file in apps/ or packages/
 * allowed to contain hex colours — scripts/check_raw_hex.py enforces it.
 * Dark is the default; light is a real theme, not an inversion.
 */
:root,
[data-theme='dark'] {
  --color-bg-0: #0E0F13;
  --color-bg-1: #16171D;
  --color-bg-2: #1E2028;
  --color-text-0: #F0F0EC;
  --color-text-1: #9B9B97;
  --color-text-2: #5C5C58;
  --color-accent: #2DD4A8;
  --color-amber: #F5A524;
  --color-danger: #EF4444;
  --color-border: rgb(240 240 236 / 0.08);
  --color-surface-glass: rgb(22 23 29 / 0.72);

  --mind-core: #2DD4A8;
  --mind-memory: #A78BFA;
  --mind-working: #67E8F9;
  --mind-agents: #E88FB3;
  --mind-knowledge: #F5A524;
}

[data-theme='light'] {
  --color-bg-0: #F6F6F3;
  --color-bg-1: #FFFFFF;
  --color-bg-2: #ECECE7;
  --color-text-0: #16171D;
  --color-text-1: #55565C;
  --color-text-2: #8A8B90;
  --color-accent: #0F9E7A;
  --color-amber: #B87200;
  --color-danger: #C62828;
  --color-border: rgb(22 23 29 / 0.1);
  --color-surface-glass: rgb(255 255 255 / 0.72);
}

:root {
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-full: 9999px;

  --motion-fast: 150ms;
  --motion-base: 200ms;
  --motion-slow: 300ms;

  /* Inter and JetBrains Mono are self-hosted by the app (no CDN); these stacks fall back cleanly. */
  --font-ui: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace;
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --motion-fast: 0ms;
    --motion-base: 0ms;
    --motion-slow: 0ms;
  }
}
```

- [ ] **Step 2: Verify the hex check passes and commit**

Run: `python3 scripts/check_raw_hex.py`
Expected: `Raw-hex check passed.`

```bash
git add packages/galaxy/tokens.css
git commit -s -m "Add the design tokens, dark and light (GAL-11)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Shaders and the orb (GAL-1)

**Files:** Create `src/shaders/noise.ts` and `src/orb/orb.ts`.

WebGL output is verified by eye in Task 10. This task's gate is typecheck plus the existing unit tests.

- [ ] **Step 1: Create `src/shaders/noise.ts`**

```ts
/**
 * 3D simplex noise.
 * Copyright (C) 2011 Ashima Arts; Stefan Gustavson. MIT License.
 * https://github.com/ashima/webgl-noise — see NOTICE.
 */
export const NOISE_GLSL = /* glsl */ `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(
            i.z + vec4(0.0, i1.z, i2.z, 1.0))
          + i.y + vec4(0.0, i1.y, i2.y, 1.0))
          + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
`;
```

Append to the repository's `NOTICE` file:
```

This product includes 3D simplex noise from webgl-noise
(https://github.com/ashima/webgl-noise), Copyright (C) 2011 Ashima Arts
and Stefan Gustavson, distributed under the MIT License.
```

- [ ] **Step 2: Create `src/orb/orb.ts`**

```ts
import {
  AdditiveBlending, BackSide, Group, IcosahedronGeometry, Mesh, MeshBasicMaterial,
  ShaderMaterial, TorusGeometry, Vector3,
} from 'three';
import type { Levels } from '../audio/levels';
import { NOISE_GLSL } from '../shaders/noise';
import type { OrbParams } from '../states';

const ORB_VERTEX = /* glsl */ `
${NOISE_GLSL}
uniform float uTime;
uniform float uNoiseSpeed;
uniform float uAmp;
uniform float uAudio;
uniform float uBass;
uniform float uBreath;
varying vec3 vNormal;
varying vec3 vViewDir;
varying float vDisp;
void main() {
  float t = uTime * uNoiseSpeed;
  vec3 p = position * 1.6;
  float n = snoise(p + t) * 0.5
          + snoise(p * 2.0 + t * 1.3) * 0.25
          + snoise(p * 4.0 + t * 1.7) * 0.125 * (0.4 + uAudio);
  float d = uBreath * 0.04
          + n * uAmp
          + n * uAudio * uAmp * 0.55
          + uBass * uAmp * 0.35 * sin(position.y * 6.0 + uTime * 4.0);
  d = clamp(d, -0.45, 0.45);
  vec3 displaced = position + normal * d;
  vec4 mv = modelViewMatrix * vec4(displaced, 1.0);
  vNormal = normalize(normalMatrix * normal);
  vViewDir = normalize(-mv.xyz);
  vDisp = d;
  gl_Position = projectionMatrix * mv;
}
`;

const ORB_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform vec3 uColor2;
uniform float uMix;
uniform float uOpacity;
uniform float uFresnel;
varying vec3 vNormal;
varying vec3 vViewDir;
varying float vDisp;
void main() {
  float fres = pow(1.0 - max(dot(normalize(vNormal), normalize(vViewDir)), 0.0), uFresnel);
  vec3 col = mix(uColor, uColor2, uMix);
  col = mix(col, uColor2, clamp(vDisp * 2.0, 0.0, 1.0) * 0.5);
  col += fres * col * 0.8;
  gl_FragColor = vec4(col, uOpacity * (0.35 + fres * 0.65));
}
`;

const GLOW_VERTEX = /* glsl */ `
varying vec3 vNormal;
void main() {
  vNormal = normalize(normalMatrix * normal);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const GLOW_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform float uOpacity;
varying vec3 vNormal;
void main() {
  float intensity = pow(max(0.0, 0.72 - dot(vNormal, vec3(0.0, 0.0, 1.0))), 2.0);
  gl_FragColor = vec4(uColor * intensity, intensity * uOpacity * 4.0);
}
`;

interface Ring {
  readonly mesh: Mesh<TorusGeometry, MeshBasicMaterial>;
  readonly spin: number;
}

/** Per-frame inputs, already eased and smoothed by the caller. */
export interface OrbFrame {
  readonly params: OrbParams;
  readonly levels: Levels;
  readonly time: number;
  readonly dt: number;
  readonly flare: number;
  readonly motion: number;
}

const RING_SPECS = [
  { radius: 1.55, tilt: [1.2, 0.3], spin: 0.21 },
  { radius: 1.65, tilt: [1.45, -0.5], spin: -0.14 },
  { radius: 1.75, tilt: [1.05, 0.9], spin: 0.09 },
] as const;

export class Orb {
  readonly group = new Group();
  private readonly core: Mesh<IcosahedronGeometry, ShaderMaterial>;
  private readonly glow: Mesh<IcosahedronGeometry, ShaderMaterial>;
  private readonly rings: Ring[] = [];
  private readonly u = {
    uTime: { value: 0 },
    uNoiseSpeed: { value: 0 },
    uAmp: { value: 0 },
    uAudio: { value: 0 },
    uBass: { value: 0 },
    uBreath: { value: 0 },
    uColor: { value: new Vector3() },
    uColor2: { value: new Vector3() },
    uMix: { value: 0 },
    uOpacity: { value: 0 },
    uFresnel: { value: 3 },
  };
  private readonly gu = {
    uColor: { value: new Vector3() },
    uOpacity: { value: 0 },
  };
  private spinAngle = 0;

  constructor(detail: number) {
    this.core = new Mesh(
      new IcosahedronGeometry(1.0, detail),
      new ShaderMaterial({
        uniforms: this.u,
        vertexShader: ORB_VERTEX,
        fragmentShader: ORB_FRAGMENT,
        wireframe: true,
        transparent: true,
        depthWrite: false,
      }),
    );
    this.glow = new Mesh(
      new IcosahedronGeometry(1.3, 12),
      new ShaderMaterial({
        uniforms: this.gu,
        vertexShader: GLOW_VERTEX,
        fragmentShader: GLOW_FRAGMENT,
        side: BackSide,
        blending: AdditiveBlending,
        transparent: true,
        depthWrite: false,
      }),
    );
    this.group.add(this.glow, this.core);

    for (const spec of RING_SPECS) {
      const mesh = new Mesh(
        new TorusGeometry(spec.radius, 0.004, 8, 256),
        new MeshBasicMaterial({
          transparent: true,
          opacity: 0,
          blending: AdditiveBlending,
          depthWrite: false,
        }),
      );
      mesh.rotation.set(spec.tilt[0], spec.tilt[1], 0);
      this.rings.push({ mesh, spin: spec.spin });
      this.group.add(mesh);
    }
  }

  setDetail(detail: number): void {
    this.core.geometry.dispose();
    this.core.geometry = new IcosahedronGeometry(1.0, detail);
  }

  update(f: OrbFrame): void {
    const { params: p, levels, time, dt, flare, motion } = f;
    const t = time * motion;

    // Processing: the colour mix swings on a 2s cycle, with a pulse each cycle.
    const cycle = 0.5 + 0.5 * Math.sin(t * Math.PI);
    const pulse = p.colorOscillation * Math.pow(Math.max(0, Math.sin(t * Math.PI)), 8) * 0.05;

    this.u.uTime.value = t;
    this.u.uNoiseSpeed.value = p.noiseSpeed;
    this.u.uAmp.value = p.displacementAmp * (1 + flare * 0.6) * (0.5 + 0.5 * motion);
    this.u.uAudio.value = levels.level;
    this.u.uBass.value = levels.bass;
    this.u.uBreath.value = Math.sin(t * 0.8);
    this.u.uColor.value.set(p.color[0], p.color[1], p.color[2]);
    this.u.uColor2.value.set(p.colorSecondary[0], p.colorSecondary[1], p.colorSecondary[2]);
    this.u.uMix.value = Math.min(1, p.colorMix + p.colorOscillation * cycle);
    this.u.uOpacity.value = p.opacity;
    this.u.uFresnel.value = p.fresnelPower;

    this.gu.uColor.value.copy(this.u.uColor.value);
    this.gu.uOpacity.value = p.glowOpacity * (1 + flare * 2);

    const scale = 1 + levels.level * 0.12 + levels.bass * 0.08 + flare * 0.1 + pulse;
    this.core.scale.setScalar(scale);
    this.glow.scale.setScalar(scale * (1 + levels.level * 0.1));

    this.spinAngle += dt * motion * (p.rotationSpeed + levels.level * 0.4);
    this.core.rotation.y = this.spinAngle;
    this.core.rotation.x = this.spinAngle * 0.35;

    for (const ring of this.rings) {
      ring.mesh.material.opacity = p.ringOpacity;
      ring.mesh.material.color.setRGB(p.colorSecondary[0], p.colorSecondary[1], p.colorSecondary[2]);
      ring.mesh.rotation.z += dt * motion * ring.spin;
      ring.mesh.visible = p.ringOpacity > 0.01;
    }
  }

  dispose(): void {
    this.core.geometry.dispose();
    this.core.material.dispose();
    this.glow.geometry.dispose();
    this.glow.material.dispose();
    for (const r of this.rings) {
      r.mesh.geometry.dispose();
      r.mesh.material.dispose();
    }
  }
}
```

- [ ] **Step 3: Typecheck and commit**

Run: `pnpm --filter @hellotrillion/galaxy typecheck && pnpm --filter @hellotrillion/galaxy test`
Expected: both succeed.

```bash
git add packages/galaxy/src/shaders packages/galaxy/src/orb NOTICE
git commit -s -m "Build the orb: displaced wireframe core, fresnel glow, three rings (GAL-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The landscape, in the same scene (GAL-4)

**Files:** Create `src/landscape/landscape.ts`.

- [ ] **Step 1: Create `src/landscape/landscape.ts`**

```ts
import {
  AdditiveBlending, BackSide, BufferAttribute, BufferGeometry, Group, Mesh, Points,
  ShaderMaterial, SphereGeometry, Vector3,
} from 'three';
import { seededRandom } from '../random';
import { NOISE_GLSL } from '../shaders/noise';

const SKY_VERTEX = /* glsl */ `
varying vec3 vDir;
void main() {
  vDir = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const SKY_FRAGMENT = /* glsl */ `
${NOISE_GLSL}
uniform float uTime;
uniform float uLight;
uniform vec3 uHueA;
uniform vec3 uHueB;
uniform vec3 uHueC;
varying vec3 vDir;
void main() {
  vec3 d = normalize(vDir);
  float n = snoise(d * 2.0 + vec3(0.0, 0.0, uTime * 0.01)) * 0.5 + 0.5;
  float n2 = snoise(d * 5.0 - uTime * 0.015) * 0.5 + 0.5;
  float neb = smoothstep(0.45, 1.0, n) * 0.6 + smoothstep(0.6, 1.0, n2) * 0.3;
  vec3 hue = mix(uHueA, uHueB, n2);
  hue = mix(hue, uHueC, smoothstep(0.3, 0.8, n));
  vec3 dark = vec3(0.035, 0.04, 0.055) + hue * neb * 0.35;
  vec3 light = mix(vec3(0.93, 0.94, 0.95), hue, neb * 0.22);
  gl_FragColor = vec4(mix(dark, light, uLight), 1.0);
}
`;

const DUST_VERTEX = /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
uniform float uTime;
uniform float uPixelRatio;
varying vec3 vColor;
varying float vTwinkle;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = aSize * uPixelRatio * (30.0 / -mv.z);
  vColor = aColor;
  vTwinkle = 0.6 + 0.4 * sin(uTime * 1.5 + position.x * 3.1 + position.y * 1.7);
  gl_Position = projectionMatrix * mv;
}
`;

const DUST_FRAGMENT = /* glsl */ `
uniform float uAlpha;
varying vec3 vColor;
varying float vTwinkle;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.0, d) * vTwinkle;
  gl_FragColor = vec4(vColor, a * uAlpha);
}
`;

// Cool cosmos around a warm-listening orb: teal, cyan, blue, purple.
const CLUSTER_HUES: readonly (readonly [number, number, number])[] = [
  [0.18, 0.83, 0.66],
  [0.12, 0.75, 0.88],
  [0.25, 0.5, 0.82],
  [0.5, 0.25, 0.75],
  [0.35, 0.4, 0.85],
];

export interface LandscapeFrame {
  readonly time: number;
  readonly dt: number;
  readonly motion: number;
  readonly light: number;
  readonly pixelRatio: number;
}

export class Landscape {
  readonly group = new Group();
  private readonly sky: Mesh<SphereGeometry, ShaderMaterial>;
  private dust: Points<BufferGeometry, ShaderMaterial>;
  private readonly su = {
    uTime: { value: 0 },
    uLight: { value: 0 },
    uHueA: { value: new Vector3(0.18, 0.83, 0.66) },
    uHueB: { value: new Vector3(0.25, 0.5, 0.82) },
    uHueC: { value: new Vector3(0.5, 0.25, 0.75) },
  };
  private readonly du = {
    uTime: { value: 0 },
    uPixelRatio: { value: 1 },
    uAlpha: { value: 0.85 },
  };
  private readonly seed: number;

  constructor(dustCount: number, seed: number) {
    this.seed = seed;
    this.sky = new Mesh(
      new SphereGeometry(60, 48, 32),
      new ShaderMaterial({
        uniforms: this.su,
        vertexShader: SKY_VERTEX,
        fragmentShader: SKY_FRAGMENT,
        side: BackSide,
        depthWrite: false,
      }),
    );
    this.sky.renderOrder = -1;
    this.dust = this.makeDust(dustCount);
    this.group.add(this.sky, this.dust);
  }

  private makeDust(count: number): Points<BufferGeometry, ShaderMaterial> {
    const rand = seededRandom(this.seed);
    const pos = new Float32Array(count * 3);
    const col = new Float32Array(count * 3);
    const size = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      // A shell between r=8 and r=40, flattened toward a galactic plane.
      const r = 8 + rand() * 32;
      const theta = rand() * Math.PI * 2;
      const phi = Math.acos(2 * rand() - 1);
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.cos(phi) * 0.45;
      pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta) - 10;
      const hue = CLUSTER_HUES[Math.floor(rand() * CLUSTER_HUES.length)] ?? CLUSTER_HUES[0]!;
      const lift = 0.6 + rand() * 0.4;
      col.set([hue[0] * lift, hue[1] * lift, hue[2] * lift], i * 3);
      size[i] = 0.6 + rand() * 1.8;
    }
    const geo = new BufferGeometry();
    geo.setAttribute('position', new BufferAttribute(pos, 3));
    geo.setAttribute('aColor', new BufferAttribute(col, 3));
    geo.setAttribute('aSize', new BufferAttribute(size, 1));
    return new Points(
      geo,
      new ShaderMaterial({
        uniforms: this.du,
        vertexShader: DUST_VERTEX,
        fragmentShader: DUST_FRAGMENT,
        transparent: true,
        blending: AdditiveBlending,
        depthWrite: false,
      }),
    );
  }

  setDustCount(count: number): void {
    this.group.remove(this.dust);
    this.dust.geometry.dispose();
    this.dust.material.dispose();
    this.dust = this.makeDust(count);
    this.group.add(this.dust);
  }

  update(f: LandscapeFrame): void {
    const t = f.time * f.motion;
    this.su.uTime.value = t;
    this.su.uLight.value = f.light;
    this.du.uTime.value = t;
    this.du.uPixelRatio.value = f.pixelRatio;
    // Additive dust washes out on a light sky; fade it rather than invert it.
    this.du.uAlpha.value = 0.85 * (1 - f.light * 0.7);
    this.dust.rotation.y += f.dt * f.motion * 0.01;
  }

  dispose(): void {
    this.sky.geometry.dispose();
    this.sky.material.dispose();
    this.dust.geometry.dispose();
    this.dust.material.dispose();
  }
}
```

- [ ] **Step 2: Typecheck and commit**

Run: `pnpm --filter @hellotrillion/galaxy typecheck`
Expected: succeeds.

```bash
git add packages/galaxy/src/landscape
git commit -s -m "Paint the nebula and dust into the orb's own scene (GAL-4)

One renderer, one WebGL context: the predecessor ran two.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Analysers and `createGalaxy()` (GAL-3, GAL-5)

**Files:** Create `src/audio/analysers.ts` and `src/index.ts`.

- [ ] **Step 1: Create `src/audio/analysers.ts`**

```ts
import { type AnalyserRole, type Levels, SILENT, applyRole, bandLevels } from './levels';

interface Attached {
  readonly analyser: AnalyserNode;
  readonly data: Uint8Array<ArrayBuffer>;
  readonly source: AudioNode;
}

/** Holds at most one analyser per role; reading is allocation-free per frame. */
export class AnalyserBank {
  private readonly attached = new Map<AnalyserRole, Attached>();

  attach(source: AudioNode, role: AnalyserRole): () => void {
    this.detach(role);
    const analyser = source.context.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.6;
    // Tap only: the analyser is a leaf, so it never changes what is heard.
    source.connect(analyser);
    const entry: Attached = {
      analyser,
      data: new Uint8Array(analyser.frequencyBinCount),
      source,
    };
    this.attached.set(role, entry);
    return () => {
      if (this.attached.get(role) === entry) this.detach(role);
    };
  }

  has(role: AnalyserRole): boolean {
    return this.attached.has(role);
  }

  read(role: AnalyserRole): Levels {
    const entry = this.attached.get(role);
    if (!entry) return SILENT;
    entry.analyser.getByteFrequencyData(entry.data);
    return applyRole(bandLevels(entry.data), role);
  }

  detach(role: AnalyserRole): void {
    const entry = this.attached.get(role);
    if (!entry) return;
    try {
      entry.source.disconnect(entry.analyser);
    } catch {
      // Already disconnected by its owner; nothing to undo.
    }
    this.attached.delete(role);
  }

  detachAll(): void {
    this.detach('mic');
    this.detach('playback');
  }
}
```

If `Uint8Array<ArrayBuffer>` fails typecheck on the installed TypeScript version, use `Uint8Array` instead. The generic form is required on TS ≥ 5.7 for `getByteFrequencyData`.

- [ ] **Step 2: Create `src/index.ts`**

```ts
import { PerspectiveCamera, Scene, WebGLRenderer } from 'three';
import { AnalyserBank } from './audio/analysers';
import {
  type AnalyserRole, type Levels, SILENT, smooth, syntheticListening, syntheticSpeech,
} from './audio/levels';
import { approach, stepParams } from './easing';
import { Landscape } from './landscape/landscape';
import { Orb } from './orb/orb';
import { type PerfMode, PerfGovernor, motionScale, qualityFor } from './perf';
import { ORB_STATES, type OrbParams, type OrbStateName } from './states';

export { ORB_STATE_NAMES, ORB_STATES, STATE_LABELS } from './states';
export type { OrbParams, OrbStateName } from './states';
export type { AnalyserRole, Levels } from './audio/levels';
export type { PerfMode } from './perf';

export type Theme = 'dark' | 'light';

export interface GalaxyOptions {
  readonly theme?: Theme;
  readonly perfMode?: PerfMode;
  /** Seed for dust layout; fix it for reproducible snapshots. */
  readonly seed?: number;
  /** Override the prefers-reduced-motion media query. */
  readonly reducedMotion?: boolean;
  /** Called whenever the state changes, for a live region (SUR-6). */
  readonly onStateChange?: (state: OrbStateName) => void;
  /** Called when perf mode switches on or off. */
  readonly onPerfChange?: (active: boolean) => void;
}

/** Reserved for the constellation (GAL-7, Phase 1). */
export interface SatelliteSpec {
  readonly id: string;
  readonly accent: string;
}

export interface Galaxy {
  setState(state: OrbStateName): void;
  getState(): OrbStateName;
  /** Tap an audio node for reactivity. Returns a function that detaches it. */
  attachAnalyser(node: AudioNode, role: AnalyserRole): () => void;
  /** A brief surge of light, for acknowledgements and small wins. */
  flare(strength?: number): void;
  /** GAL-7, Phase 1: no-op until the constellation lands. */
  addSatellite(spec: SatelliteSpec): void;
  /** GAL-7, Phase 1: no-op until the constellation lands. */
  removeSatellite(id: string): void;
  /** GAL-8, Phase 1: no-op until energy arcs land. */
  pulseArc(targetId: string): void;
  setPerfMode(mode: PerfMode): void;
  setTheme(theme: Theme): void;
  destroy(): void;
}

const MAX_DT = 0.1;

export function createGalaxy(canvas: HTMLCanvasElement, opts: GalaxyOptions = {}): Galaxy {
  const renderer = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  const scene = new Scene();
  const camera = new PerspectiveCamera(45, 1, 0.1, 200);
  camera.position.set(0, 0, 4.2);

  const governor = new PerfGovernor(opts.perfMode ?? 'auto');
  const media = typeof matchMedia === 'function'
    ? matchMedia('(prefers-reduced-motion: reduce)')
    : null;
  let reduced = opts.reducedMotion ?? media?.matches ?? false;
  const onMotionChange = (e: MediaQueryListEvent): void => {
    if (opts.reducedMotion === undefined) reduced = e.matches;
  };
  media?.addEventListener('change', onMotionChange);

  let perfActive = governor.sample(60, 0);
  let quality = qualityFor(perfActive, devicePixelRatio);
  const orb = new Orb(quality.orbDetail);
  const landscape = new Landscape(quality.dustCount, opts.seed ?? 1);
  scene.add(landscape.group, orb.group);
  renderer.setPixelRatio(quality.pixelRatio);

  const bank = new AnalyserBank();
  let state: OrbStateName = 'idle';
  let params: OrbParams = ORB_STATES.idle;
  let levels: Levels = SILENT;
  let flareLevel = 0;
  let theme: Theme = opts.theme ?? 'dark';
  let light = theme === 'light' ? 1 : 0;
  let fps = 60;
  let last = performance.now();
  let clock = 0;
  let raf = 0;

  const resize = (): void => {
    const w = canvas.clientWidth || 1;
    const h = canvas.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  const applyQuality = (): void => {
    const next = qualityFor(perfActive, devicePixelRatio);
    if (next.orbDetail !== quality.orbDetail) orb.setDetail(next.orbDetail);
    if (next.dustCount !== quality.dustCount) landscape.setDustCount(next.dustCount);
    if (next.pixelRatio !== quality.pixelRatio) renderer.setPixelRatio(next.pixelRatio);
    quality = next;
    opts.onPerfChange?.(perfActive);
  };

  const targetLevels = (): Levels => {
    if (state === 'speaking') {
      return bank.has('playback') ? bank.read('playback') : syntheticSpeech(clock);
    }
    if (state === 'listening') {
      return bank.has('mic') ? bank.read('mic') : syntheticListening(clock);
    }
    return SILENT;
  };

  const frame = (now: number): void => {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(MAX_DT, Math.max(0, (now - last) / 1000));
    last = now;
    clock += dt;
    if (dt > 0) fps += (1 / dt - fps) * 0.1;

    const wasActive = perfActive;
    perfActive = governor.sample(fps, now);
    if (perfActive !== wasActive) applyQuality();

    const motion = motionScale(reduced);
    params = stepParams(params, ORB_STATES[state], dt);
    levels = smooth(levels, targetLevels());
    flareLevel = approach(flareLevel, 0, 0.05, dt);
    light = approach(light, theme === 'light' ? 1 : 0, 0.08, dt);

    orb.update({ params, levels, time: clock, dt, flare: flareLevel, motion });
    landscape.update({ time: clock, dt, motion, light, pixelRatio: quality.pixelRatio });
    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(frame);

  return {
    setState(next) {
      if (next === state) return;
      state = next;
      opts.onStateChange?.(next);
    },
    getState: () => state,
    attachAnalyser: (node, role) => bank.attach(node, role),
    flare(strength = 1) {
      flareLevel = Math.min(1.5, flareLevel + Math.max(0, strength));
    },
    addSatellite() {},
    removeSatellite() {},
    pulseArc() {},
    setPerfMode(mode) {
      governor.setMode(mode);
      const was = perfActive;
      perfActive = governor.sample(fps, performance.now());
      if (perfActive !== was) applyQuality();
    },
    setTheme(next) {
      theme = next;
    },
    destroy() {
      cancelAnimationFrame(raf);
      observer.disconnect();
      media?.removeEventListener('change', onMotionChange);
      bank.detachAll();
      orb.dispose();
      landscape.dispose();
      renderer.dispose();
    },
  };
}
```

- [ ] **Step 3: Typecheck, test, build**

Run: `pnpm --filter @hellotrillion/galaxy typecheck && pnpm --filter @hellotrillion/galaxy test && pnpm --filter @hellotrillion/galaxy build`
Expected: all succeed. `packages/galaxy/dist/galaxy.js` and `dist/index.d.ts` exist.

- [ ] **Step 4: Commit**

```bash
git add packages/galaxy/src/audio/analysers.ts packages/galaxy/src/index.ts
git commit -s -m "Expose createGalaxy(): states, audio taps, flare, perf and theme (GAL-5)

Satellites and arcs have their final signatures and do nothing yet, so
code written against the API today keeps working when Phase 1 fills them in.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The demo page

**Files:** Create `demo/index.html`, `demo/main.ts` and `demo/demo.css`.

- [ ] **Step 1: Create `demo/index.html`**

```html
<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Trillion — Galaxy</title>
  </head>
  <body>
    <canvas id="galaxy" aria-hidden="true"></canvas>
    <main class="panel" aria-label="Galaxy controls">
      <h1>Trillion <span>Galaxy</span></h1>
      <p class="status" role="status" aria-live="polite" id="status">Idle</p>
      <div class="group" id="states" role="group" aria-label="Orb state"></div>
      <div class="group" role="group" aria-label="Audio">
        <button type="button" id="mic">Microphone: off</button>
        <button type="button" id="speak">Test speaking (soft tone)</button>
        <button type="button" id="flare-btn">Flare</button>
      </div>
      <div class="group" role="group" aria-label="Display">
        <button type="button" id="perf">Performance: auto</button>
        <button type="button" id="theme">Theme: dark</button>
      </div>
      <p class="note" id="note"></p>
    </main>
    <script type="module" src="./main.ts"></script>
  </body>
</html>
```

- [ ] **Step 2: Create `demo/demo.css`**

```css
html,
body {
  margin: 0;
  height: 100%;
  background: var(--color-bg-0);
  color: var(--color-text-0);
  font-family: var(--font-ui);
  overflow: hidden;
}

canvas {
  position: fixed;
  inset: 0;
  width: 100vw;
  height: 100vh;
  display: block;
}

.panel {
  position: fixed;
  left: 16px;
  bottom: 16px;
  max-width: min(420px, calc(100vw - 32px));
  padding: 16px;
  border-radius: var(--radius-lg);
  background: var(--color-surface-glass);
  border: 1px solid var(--color-border);
  backdrop-filter: blur(12px);
}

h1 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

h1 span {
  color: var(--color-text-1);
  font-weight: 400;
}

.status {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--color-accent);
}

.group {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

button {
  font: inherit;
  font-size: 12px;
  padding: 6px 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-border);
  background: var(--color-bg-2);
  color: var(--color-text-0);
  cursor: pointer;
  transition: border-color var(--motion-fast), background var(--motion-fast);
}

button:hover {
  border-color: var(--color-accent);
}

button:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

button[aria-pressed='true'] {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.note {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--color-text-1);
  min-height: 1em;
}
```

- [ ] **Step 3: Create `demo/main.ts`**

```ts
import '../tokens.css';
import './demo.css';
import {
  ORB_STATE_NAMES, STATE_LABELS, createGalaxy, type OrbStateName, type PerfMode, type Theme,
} from '../src/index';

const byId = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`demo: missing #${id}`);
  return el as T;
};

const status = byId<HTMLParagraphElement>('status');
const note = byId<HTMLParagraphElement>('note');
const stateButtons = new Map<OrbStateName, HTMLButtonElement>();

const galaxy = createGalaxy(byId<HTMLCanvasElement>('galaxy'), {
  onStateChange: (s) => {
    status.textContent = STATE_LABELS[s];
    for (const [name, btn] of stateButtons) btn.setAttribute('aria-pressed', String(name === s));
  },
  onPerfChange: (active) => {
    note.textContent = active ? 'Performance mode is on.' : '';
  },
});

const statesGroup = byId<HTMLDivElement>('states');
for (const name of ORB_STATE_NAMES) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = name;
  btn.setAttribute('aria-pressed', String(name === 'idle'));
  btn.addEventListener('click', () => galaxy.setState(name));
  stateButtons.set(name, btn);
  statesGroup.append(btn);
}

let audio: AudioContext | null = null;
const ctx = (): AudioContext => (audio ??= new AudioContext());

// Microphone: arming until the stream is live, then listening.
const micBtn = byId<HTMLButtonElement>('mic');
let mic: { stream: MediaStream; detach: () => void } | null = null;
micBtn.addEventListener('click', async () => {
  if (mic) {
    mic.detach();
    for (const t of mic.stream.getTracks()) t.stop();
    mic = null;
    micBtn.textContent = 'Microphone: off';
    galaxy.setState('idle');
    return;
  }
  galaxy.setState('arming');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const c = ctx();
    await c.resume();
    const detach = galaxy.attachAnalyser(c.createMediaStreamSource(stream), 'mic');
    mic = { stream, detach };
    micBtn.textContent = 'Microphone: on';
    note.textContent = '';
    galaxy.setState('listening');
  } catch {
    galaxy.setState('error');
    note.textContent = "I can't hear your microphone. Check the browser's permission for this page.";
  }
});

// Speaking: a quiet tone with a speech-like amplitude wobble, through the playback analyser.
byId<HTMLButtonElement>('speak').addEventListener('click', async () => {
  const c = ctx();
  await c.resume();
  const osc = c.createOscillator();
  const lfo = c.createOscillator();
  const lfoGain = c.createGain();
  const out = c.createGain();
  osc.frequency.value = 196;
  lfo.frequency.value = 4.3;
  lfoGain.gain.value = 0.03;
  out.gain.value = 0.04;
  lfo.connect(lfoGain).connect(out.gain);
  osc.connect(out).connect(c.destination);
  const detach = galaxy.attachAnalyser(out, 'playback');
  galaxy.setState('speaking');
  osc.start();
  lfo.start();
  const stopAt = c.currentTime + 3;
  osc.stop(stopAt);
  lfo.stop(stopAt);
  osc.onended = () => {
    detach();
    if (galaxy.getState() === 'speaking') galaxy.setState(mic ? 'listening' : 'idle');
  };
});

byId<HTMLButtonElement>('flare-btn').addEventListener('click', () => galaxy.flare());

const perfBtn = byId<HTMLButtonElement>('perf');
const perfModes: PerfMode[] = ['auto', 'on', 'off'];
let perfIndex = 0;
perfBtn.addEventListener('click', () => {
  perfIndex = (perfIndex + 1) % perfModes.length;
  const mode = perfModes[perfIndex] ?? 'auto';
  galaxy.setPerfMode(mode);
  perfBtn.textContent = `Performance: ${mode}`;
});

const themeBtn = byId<HTMLButtonElement>('theme');
let theme: Theme = 'dark';
themeBtn.addEventListener('click', () => {
  theme = theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset['theme'] = theme;
  galaxy.setTheme(theme);
  themeBtn.textContent = `Theme: ${theme}`;
});
```

- [ ] **Step 4: Verify by eye**

Run: `pnpm --filter @hellotrillion/galaxy dev`, then open `http://127.0.0.1:5173/`.

Expected:
- A teal wireframe orb breathes over a dark nebula with twinkling dust.
- Each state button changes the orb within about a second: arming is ember, listening is gold, processing is teal↔purple with rings, speaking is teal, error is red.
- "Microphone: on" asks for permission, goes ember, then gold, and the orb swells when you talk.
- "Test speaking" plays a soft 3-second tone, and the orb rides it.
- The theme toggle brightens the sky and the panel.
- The status line always names the state.
- The browser console shows no errors.

- [ ] **Step 5: Run the checks and commit**

Run: `pnpm --filter @hellotrillion/galaxy typecheck && python3 scripts/check_raw_hex.py && python3 scripts/containment_check.py`

```bash
git add packages/galaxy/demo
git commit -s -m "Add a demo page: every state, your mic, a test voice, perf and theme

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: README and the snapshot follow-up

- [ ] **Step 1:** Create `packages/galaxy/README.md`: a one-paragraph description, the `pnpm --filter @hellotrillion/galaxy dev` command, a `createGalaxy()` usage example matching `index.ts`, and a table of the six states. Note that `addSatellite`, `removeSatellite` and `pulseArc` are Phase 1 no-ops.
- [ ] **Step 2:** Add to the root README's "Architecture" section the line `Try the orb: pnpm install && pnpm --filter @hellotrillion/galaxy dev`.
- [ ] **Step 3:** Commit (`docs: galaxy readme`, signed off, with the co-author line).
- [ ] **Step 4:** After the branch is pushed, open an issue: "GAL-12: visual snapshot tests per state at a fixed seed (Playwright, GPU-capable runner)".
