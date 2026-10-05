import { defineConfig } from 'vite';

// Fixed port so tauri.conf.json devUrl matches; no remote assets.
export default defineConfig({
  clearScreen: false,
  server: { port: 1420, strictPort: true, host: '127.0.0.1' },
  build: { target: 'es2022', outDir: 'dist', emptyOutDir: true },
});
