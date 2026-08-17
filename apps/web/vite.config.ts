import { fileURLToPath } from 'node:url';

import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * The browser talks to the backend through RELATIVE urls (`/api/v1/...`,
 * `/tiles/...`, `/health`) and Vite proxies them in development.
 *
 * This exists because the repository contains two implementations of the same
 * API. With an absolute base url baked into the client there were two plausible
 * backends on two ports, and `.env.example` pointed at the wrong one - a fresh
 * clone would silently have talked to the reference TypeScript service while
 * appearing to work. Relative urls remove the ambiguity: whatever serves the
 * page serves the API.
 */
export default defineConfig(({ mode }) => {
  // The repo keeps a single .env at its root. Vite defaults envDir to the
  // config's own directory, so without envDir the VITE_* variables are never
  // loaded and the client falls back to its defaults.
  // fileURLToPath, not URL.pathname: on Windows a file URL yields "/C:/...",
  // which is not a usable filesystem path.
  const env = loadEnv(mode, fileURLToPath(new URL("../..", import.meta.url)), "");
  const target = env.API_ORIGIN || `http://127.0.0.1:${env.API_PORT || 8000}`;

  return {
    plugins: [react()],
    envDir: '../..',
    server: {
      /*
       * Bind the IPv4 loopback explicitly rather than leaving it to whichever
       * family `localhost` resolves to first.
       *
       * Vite's default `localhost` resolves to ::1 on Windows, and a server
       * listening only there is unreachable at http://127.0.0.1:5173 — which
       * is the address the Playwright config probes, and the one anything
       * IPv4-only reaches for. The failure is confusing rather than loud: the
       * server prints a healthy banner, a browser still works because it tries
       * both families, and the test runner waits two minutes before declaring
       * the server unavailable.
       *
       * Deliberately the address and not `true`. `true` (or `0.0.0.0`) would
       * also fix it, and would additionally publish the dev server on every
       * network interface the machine has. Pass `--host` on the command line
       * for the occasion when that is actually wanted; it overrides this.
       */
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': { target, changeOrigin: true },
        '/tiles': { target, changeOrigin: true },
        '/health': { target, changeOrigin: true },
        '/openapi.json': { target, changeOrigin: true },
      },
    },
  };
});
