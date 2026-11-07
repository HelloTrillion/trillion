/**
 * GAL-12: every orb state, in dark and in light, drawn at a fixed seed and
 * compared with its reference picture.
 *
 * After a change to how the orb looks on purpose, make new references and look at
 * them before committing:
 *
 *     pnpm --filter @hellotrillion/galaxy test:visual --update-snapshots
 */

import { expect, test } from '@playwright/test';
import { ORB_STATE_NAMES } from '../src/states';

// Four seconds at 60 fps: every state has finished easing in by then.
const SETTLED = 240;

for (const theme of ['dark', 'light'] as const) {
  for (const state of ORB_STATE_NAMES) {
    test(`${state}, ${theme}`, async ({ page }) => {
      await page.goto('/');
      await page.locator('body[data-ready="true"]').waitFor();
      await page.evaluate(([s, t, n]) => window.snap(s, t, n), [state, theme, SETTLED] as const);
      await expect(page.locator('#galaxy')).toHaveScreenshot(`${state}-${theme}.png`);
    });
  }
}

// GAL-9: a large supernova at its brightest (its rise is 15% of 3.6 s), wave on its way.
test('supernova, large, dark', async ({ page }) => {
  await page.goto('/');
  await page.locator('body[data-ready="true"]').waitFor();
  await page.evaluate(([n]) => window.snap('idle', 'dark', n, { tier: 'large', frames: 32 }), [SETTLED] as const);
  await expect(page.locator('#galaxy')).toHaveScreenshot('supernova-large-dark.png');
});

test('the same state at the same seed draws the same picture', async ({ page }) => {
  // The tolerance above is for software drawing between machines. On one machine
  // two runs must agree, or the snapshots would flicker for no reason.
  // It draws twice, and software drawing on a CI machine is slow: three times the time.
  test.slow();
  const shots: Buffer[] = [];
  for (let i = 0; i < 2; i++) {
    await page.goto('/');
    await page.locator('body[data-ready="true"]').waitFor();
    await page.evaluate(([n]) => window.snap('processing', 'dark', n), [SETTLED] as const);
    shots.push(await page.locator('#galaxy').screenshot());
  }
  expect(shots[0]?.equals(shots[1] as Buffer)).toBe(true);
});

test('the picture is not blank', async ({ page }) => {
  // A canvas WebGL failed to draw on is one flat colour, and would match a flat
  // reference forever. A flat picture compresses to almost nothing, and looks the
  // same as the page with the canvas hidden.
  await page.goto('/');
  await page.locator('body[data-ready="true"]').waitFor();
  await page.evaluate(([n]) => window.snap('speaking', 'dark', n), [SETTLED] as const);
  const drawn = await page.locator('#galaxy').screenshot();
  await page.locator('#galaxy').evaluate((c) => {
    (c as HTMLCanvasElement).style.visibility = 'hidden';
  });
  const empty = await page.screenshot({ clip: { x: 0, y: 0, width: 480, height: 360 } });
  expect(drawn.length).toBeGreaterThan(20_000);
  expect(drawn.equals(empty)).toBe(false);
});
