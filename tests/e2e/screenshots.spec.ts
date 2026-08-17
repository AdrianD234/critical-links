/**
 * The review screenshots, captured by the test suite rather than by hand.
 *
 * Hand-captured screenshots go stale silently: they are taken once, committed,
 * and then keep showing a build from three weeks ago while everyone assumes
 * they show the current one. Generating them from the same fixtures the tests
 * use means a state that no longer exists cannot be photographed, and a
 * regression shows up in the image as well as in the assertion.
 *
 *   npx playwright test screenshots --project=desktop
 *
 * Skipped by default so the ordinary run stays fast; opt in with
 * NZCL_SCREENSHOTS=1.
 */

import { mkdirSync } from 'node:fs';

import { exploreUrl, expect, panel, test, waitForResult } from './fixtures.js';

const OUT = 'docs/design/implementation';
const ENABLED = process.env.NZCL_SCREENSHOTS === '1';

test.describe('review screenshots', () => {
  test.skip(!ENABLED, 'set NZCL_SCREENSHOTS=1 to regenerate');
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

  /** Settle animations before capturing, so frames are reproducible. */
  async function settle(page: import('@playwright/test').Page) {
    await page.waitForTimeout(2500);
  }

  test('reverse result', async ({ page, twoWayLink }, testInfo) => {
    await page.goto(exploreUrl(twoWayLink.amdsId, { focus: 'reverse' }));
    await waitForResult(page);
    await settle(page);
    await page.screenshot({
      path: `${OUT}/stage2-result-${testInfo.project.name}.png`,
    });
  });

  /*
   * The advanced scope, where the closure is larger than the selection.
   *
   * This slot used to photograph the forward/reverse comparison. That view
   * belonged to the endpoint measure, which computed both directions of one
   * link; this engine measures trips across a boundary, where "the other
   * direction" is a different crossing rather than the same one reversed. The
   * state worth photographing in its place is the one where the panel has to
   * warn before it reports.
   */
  test('source-feature scope', async ({ page, twoWayLink }, testInfo) => {
    await page.goto(exploreUrl(twoWayLink.amdsId, { scope: 'amds-feature' }));
    await waitForResult(page);
    await settle(page);
    await page.screenshot({
      path: `${OUT}/stage2-source-feature-${testInfo.project.name}.png`,
    });
  });

  test('one-way, forward only', async ({ page, oneWayLink }, testInfo) => {
    await page.goto(exploreUrl(oneWayLink.amdsId, { focus: 'reverse' }));
    await waitForResult(page);
    await settle(page);
    await page.screenshot({
      path: `${OUT}/stage2-oneway-${testInfo.project.name}.png`,
    });
  });

  test('snapshot mismatch', async ({ page, twoWayLink }, testInfo) => {
    await page.goto(
      exploreUrl(twoWayLink.amdsId, { snapshot: 'amds-wellington-2019-0000abcd' }),
    );
    await waitForResult(page);
    await settle(page);
    await page.screenshot({
      path: `${OUT}/stage2-snapshot-mismatch-${testInfo.project.name}.png`,
    });
  });

  test('calculating', async ({ page, twoWayLink }, testInfo) => {
    /* Hold the response open so the loading state can actually be photographed
     * rather than raced against. */
    await page.route('**/api/v2/links/**/boundary-analysis**', async (route) => {
      await new Promise((r) => setTimeout(r, 8000));
      await route.continue();
    });
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await expect(panel(page).locator('.skeleton').first()).toBeVisible();
    await page.waitForTimeout(600);
    await page.screenshot({
      path: `${OUT}/stage2-calculating-${testInfo.project.name}.png`,
    });
    await page.unroute('**/api/v2/links/**/boundary-analysis**');
  });

  test('search results', async ({ page, twoWayLink }, testInfo) => {
    await page.goto('/');
    await page.keyboard.press('/');
    await page.keyboard.type(twoWayLink.roadName.split(/\s+/)[0]!);
    await expect(page.getByRole('option').first()).toBeVisible();
    await page.waitForTimeout(500);
    await page.screenshot({
      path: `${OUT}/stage2-search-${testInfo.project.name}.png`,
    });
  });

  test('no represented replacement', async ({ page, request }, testInfo) => {
    /* Find a link the engine actually reports as disconnected, rather than
     * assuming one. If the snapshot has none, say so instead of shipping a
     * screenshot of a different state under this name. */
    const res = await request.get(
      `${process.env.NZCL_API_URL ?? 'http://127.0.0.1:8000'}` +
        `/api/v1/links/search?name=&limit=60`,
    );
    const { results } = await res.json();

    for (const link of results.slice(0, 25)) {
      await page.goto(exploreUrl(link.amdsId));
      await waitForResult(page);
      const status = await page.locator('.status-pill').first().innerText();
      if (/has no represented replacement/i.test(status)) {
        await settle(page);
        await page.screenshot({
          path: `${OUT}/stage2-no-replacement-${testInfo.project.name}.png`,
        });
        return;
      }
    }
    test.skip(true, 'no link without a represented replacement in this snapshot');
  });

  /*
   * There is deliberately no mobile screenshot here. Mobile development is
   * paused and mobile is out of visual acceptance; the frozen state is covered
   * by tests/e2e/mobile-smoke.spec.ts, which checks the app is not broken
   * rather than how it looks.
   */
});
