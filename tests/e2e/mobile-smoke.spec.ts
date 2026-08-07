/**
 * Mobile smoke test — the only mobile coverage that remains.
 *
 * MOBILE DEVELOPMENT IS PAUSED. The bottom sheet and responsive shell are
 * frozen at their Stage 2 state: no new features, no design iteration, no
 * screenshots, no transition tuning. Product work targets 1440×900 and
 * 1280×800.
 *
 * This file exists so that ordinary desktop work cannot render the application
 * completely unusable on a phone without anyone noticing. It deliberately
 * checks only that the app is not broken — not that it is good. Anything
 * finer-grained would be maintaining mobile behaviour, which is exactly what
 * has been paused.
 *
 * Shared correctness, accessibility and security fixes still apply to every
 * layout; those are covered by the desktop suites, which exercise the same
 * components.
 */

import { exploreUrl, expect, panel, test, waitForResult } from './fixtures.js';

test.describe('mobile smoke', () => {
  test('loads, renders the map, and shows a result without breaking', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));

    /* 1. The application loads. */
    await expect(page.locator('.app-shell')).toBeVisible();

    /* 2. The map renders — not merely mounts. */
    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const m = (window as unknown as { __map?: any }).__map;
            if (!m?.isStyleLoaded?.() || !m.getLayer?.('network-line')) return 0;
            try {
              return m.queryRenderedFeatures({ layers: ['network-line'] }).length;
            } catch {
              return 0;
            }
          }),
        { timeout: 40_000, message: 'no network rendered on mobile' },
      )
      /* At least one, not a density figure: see the note in explore.spec.ts. */
      .toBeGreaterThan(0);

    /* 3. A selected result is reachable. */
    await waitForResult(page);
    await expect(panel(page)).toBeVisible();
    await expect(page.locator('.insp-head h2')).not.toBeEmpty();

    /* 4. No catastrophic overflow. Vertical scroll inside the sheet is
     *    expected; the page itself must never scroll sideways. */
    const overflow = await page.evaluate(() => ({
      scrollW: document.documentElement.scrollWidth,
      innerW: window.innerWidth,
    }));
    expect(
      overflow.scrollW,
      `page scrolls horizontally: ${overflow.scrollW}px in ${overflow.innerW}px`,
    ).toBeLessThanOrEqual(overflow.innerW + 1);
  });
});
