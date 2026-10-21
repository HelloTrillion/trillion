import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

// Serves the snapshot page, on loopback only like everything else here.
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  server: { host: '127.0.0.1', port: 5181, strictPort: true },
  logLevel: 'warn',
});
