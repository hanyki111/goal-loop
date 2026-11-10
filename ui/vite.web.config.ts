import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The app's screen for `goal-loop ui` (a browser served by the Python package): same source, with the Tauri API
// replaced by a fetch-based host. The output is package data, so the CLI serves it without Node.
export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: [{ find: /^@tauri-apps\/api\/core$/, replacement: fileURLToPath(new URL('./src/web-host.ts', import.meta.url)) }],
  },
  build: {
    outDir: '../goal_loop_tool/web_files',
    emptyOutDir: true,
    modulePreload: false,
    rollupOptions: {
      output: { entryFileNames: 'index.js', assetFileNames: 'index[extname]' },
    },
  },
});
