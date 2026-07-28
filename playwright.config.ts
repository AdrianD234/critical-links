import { defineConfig, devices } from '@playwright/test';

/**
 * Browser-level tests for the Explore screen.
 *
 * These run against the REAL backend and the real snapshot, not fixtures. The
 * defects this suite exists to catch — a blank panel on a one-way link, a
 * doubled history entry, a route drawn across a gap — are all interactions
 * between real API shapes and real browser behaviour, and every one of them
 * survived a green unit-test run.
 *
 * The API is expected to be already running; the web server is started here.
 * `NZCL_SKIP_WEBSERVER=1` attaches to a dev server you are already running.
 */
export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './tests/e2e/.output',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],

  timeout: 60_000,
  expect: { timeout: 15_000 },

  use: {
    baseURL: process.env.NZCL_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },

  /*
   * Desktop and laptop are the supported product viewports. Mobile development
   * is paused, so the mobile project runs one smoke test rather than the full
   * suite — enough that a desktop change cannot make the app unusable on a
   * phone unnoticed, and not so much that it amounts to maintaining mobile
   * behaviour while the desktop model is still moving.
   */
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
      testIgnore: /mobile-smoke\.spec\.ts/,
    },
    {
      name: 'laptop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
      testIgnore: /mobile-smoke\.spec\.ts/,
    },
    {
      /*
       * Chromium at a phone viewport, not the iPhone device profile: the
       * bundled iPhone profiles default to WebKit, which would make CI depend
       * on a second browser download to test the app's own responsive shell
       * rather than WebKit's rendering.
       */
      name: 'mobile-smoke',
      use: { ...devices['Pixel 7'] },
      testMatch: /mobile-smoke\.spec\.ts/,
    },
  ],

  webServer: process.env.NZCL_SKIP_WEBSERVER
    ? undefined
    : {
        command: 'npm run dev --workspace apps/web',
        url: 'http://127.0.0.1:5173',
        reuseExistingServer: true,
        timeout: 120_000,
      },
});
