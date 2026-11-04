import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The app's screen for the VS Code extension: same source, with the Tauri API replaced by the extension host.
export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: [{ find: /^@tauri-apps\/api\/core$/, replacement: fileURLToPath(new URL('./src/vscode-host.ts', import.meta.url)) }],
  },
  build: {
    outDir: '../vscode/media',
    emptyOutDir: true,
    modulePreload: false,
    rollupOptions: {
      output: { entryFileNames: 'index.js', assetFileNames: 'index[extname]' },
    },
  },
});
