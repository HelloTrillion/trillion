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
