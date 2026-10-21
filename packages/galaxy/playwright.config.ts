import { defineConfig } from '@playwright/test';

// Snapshot tests for the orb (GAL-12). WebGL is drawn in software (SwiftShader), the
// same way on a laptop and on a CI machine with no graphics card, so one set of
// reference pictures serves every platform. On one machine two runs match exactly;
// the small tolerance is for software drawing on another processor. It is tight on
// purpose: an orange-red error state, or processing without its rings, both fail.
const PORT = 5181;

export default defineConfig({
  testDir: './visual',
  snapshotPathTemplate: '{testDir}/snapshots/{arg}{ext}',
  timeout: 60_000,
  fullyParallel: true,
  reporter: [['list']],
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.002, threshold: 0.05, animations: 'disabled' },
  },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 480, height: 360 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    launchOptions: {
      args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
    },
  },
  webServer: {
    command: `pnpm exec vite --config visual/vite.config.ts`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
