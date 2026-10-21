# @hellotrillion/galaxy

Trillion's visual engine: a wireframe orb over a nebula landscape, drawn by three.js in a single WebGL context. The orb eases between six states defined in one table (`src/states.ts`), swells with the microphone while listening and with playback while speaking, drops to a lighter performance mode when the frame rate stays low, and honours `prefers-reduced-motion`. It has no dependency on the rest of Trillion, so it can be embedded anywhere a canvas can.

## Try it

From the repository root:

```sh
pnpm install
pnpm --filter @hellotrillion/galaxy dev
```

Then open http://127.0.0.1:5173/. The demo page has a button for every state, your microphone, a soft test tone for speaking, a flare, the performance toggle and the theme toggle.

## Usage

```ts
import '@hellotrillion/galaxy/tokens.css';
import { createGalaxy, STATE_LABELS } from '@hellotrillion/galaxy';

const canvas = document.querySelector('canvas')!;
const galaxy = createGalaxy(canvas, {
  theme: 'dark',          // or 'light'
  perfMode: 'auto',       // 'auto' | 'on' | 'off'
  seed: 1,                // fixes the dust layout
  onStateChange: (state) => {
    liveRegion.textContent = STATE_LABELS[state];
  },
  onPerfChange: (active) => console.log('performance mode', active),
});

galaxy.setState('arming');
const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
const ctx = new AudioContext();
const detachMic = galaxy.attachAnalyser(ctx.createMediaStreamSource(stream), 'mic');
galaxy.setState('listening');

// Later: tap the node your speech plays through.
const detachVoice = galaxy.attachAnalyser(playbackGainNode, 'playback');
galaxy.setState('speaking');

galaxy.flare();           // a brief surge of light
galaxy.setTheme('light');
galaxy.setPerfMode('on');
galaxy.destroy();         // stops the loop and frees GPU resources
```

`attachAnalyser` only taps the node: it never changes what is heard. When no playback analyser is attached, speaking falls back to a gentle synthetic pulse so the orb never looks dead.

**Gold means the microphone is live, and nothing else.** If you call `setState('listening')` before a microphone is attached, the orb shows `arming` (ember) until `attachAnalyser(node, 'mic')` is called, and `onStateChange` reports `arming` too, so a screen reader never announces listening when nothing is recording. `getState()` still returns what you asked for. For demos and screenshots, `createGalaxy(canvas, { demo: true })` lets `listening` pulse gold without a microphone.

`addSatellite`, `removeSatellite` and `pulseArc` already have their final signatures but are no-ops in this release; the constellation (GAL-7) and energy arcs (GAL-8) arrive in Phase 1.

## States

| State | Look | Meaning |
|---|---|---|
| `idle` | Dim teal, slow breath | Nothing is happening |
| `arming` | Ember | The mic was requested; no audio has arrived yet |
| `listening` | Gold, swells with your voice | The mic is live (shows `arming` until a mic is attached) |
| `processing` | Teal and purple, with three rings | Thinking |
| `speaking` | Bright teal, rides the playback | The agent is talking |
| `error` | Red | Something went wrong |

## Development

```sh
pnpm --filter @hellotrillion/galaxy typecheck
pnpm --filter @hellotrillion/galaxy test
pnpm --filter @hellotrillion/galaxy build
pnpm --filter @hellotrillion/galaxy test:visual
```

`test:visual` photographs every state in dark and light at a fixed seed and compares each with its reference in `visual/snapshots/` (GAL-12). It draws in software, so it needs no graphics card; the first run may need `pnpm --filter @hellotrillion/galaxy exec playwright install chromium`. After changing the look on purpose, run it with `--update-snapshots` and look at the new pictures before committing them.
