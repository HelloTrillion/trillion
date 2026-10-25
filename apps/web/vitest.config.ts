import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Tests read the Galaxy package from its source, so they never depend on it having been built first.
  resolve: {
    alias: { '@hellotrillion/galaxy': fileURLToPath(new URL('../../packages/galaxy/src/index.ts', import.meta.url)) },
  },
  test: { include: ['test/**/*.test.ts'] },
});
