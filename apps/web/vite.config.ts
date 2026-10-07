import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const here = (p: string): string => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  // The Galaxy is used from source, so the app builds without building the package first.
  resolve: {
    alias: [{ find: /^@hellotrillion\/galaxy$/, replacement: here('../../packages/galaxy/src/index.ts') }],
  },
  // Loopback only, like the core (never-list #6). `trillion run --dev` accepts this origin.
  server: { host: '127.0.0.1', port: 5174, strictPort: true },
  build: { target: 'es2022', sourcemap: false },
});
