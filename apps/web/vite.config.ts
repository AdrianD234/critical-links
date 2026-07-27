import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // The repo keeps a single .env at its root. Vite defaults envDir to the
  // config's own directory, so without this VITE_* variables are silently
  // never loaded and the client falls back to its hard-coded API base.
  envDir: '../..',
  server: { port: 5173, strictPort: true },
});
