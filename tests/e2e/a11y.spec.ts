/**
 * Accessibility scan.
 *
 * axe-core does not prove a screen is usable — it cannot tell whether the
 * announcement a live region makes is the right one, or whether a tab order is
 * sensible. It does catch the mechanical failures reliably, which is worth
 * having as a gate so they cannot creep back in unnoticed.
 *
 * Scoped to the app shell rather than the whole page: the MapLibre canvas is a
 * third-party subtree this project does not control, and failing the build on
 * its markup would mean either disabling the scan or patching the library.
 */

import AxeBuilder from '@axe-core/playwright';

import { exploreUrl, expect, test, waitForResult } from './fixtures.js';

const RULESET = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

async function scan(page: import('@playwright/test').Page) {
  return new AxeBuilder({ page })
    .withTags(RULESET)
    .exclude('.maplibregl-map')
    .analyze();
}

test.describe('accessibility', () => {
  test('the empty state has no violations', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /select a road/i })).toBeVisible();

    const results = await scan(page);
    expect(
      results.violations.map((v) => `${v.id}: ${v.help}`),
      JSON.stringify(results.violations, null, 2),
    ).toEqual([]);
  });

  test('a settled result has no violations', async ({ page, twoWayLink }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);

    const results = await scan(page);
    expect(
      results.violations.map((v) => `${v.id}: ${v.help}`),
      JSON.stringify(results.violations, null, 2),
    ).toEqual([]);
  });

  test('the scenario controls have no violations when open', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    await page.locator('.scenario-summary-btn').click();
    await expect(page.locator('.ctls')).toBeVisible();

    const results = await scan(page);
    expect(
      results.violations.map((v) => `${v.id}: ${v.help}`),
      JSON.stringify(results.violations, null, 2),
    ).toEqual([]);
  });

  test('the open map-view selector has no violations', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /^map view/i }).click();
    await expect(page.getByRole('radio', { name: 'Analysis' })).toBeVisible();

    const results = await scan(page);
    expect(
      results.violations.map((v) => `${v.id}: ${v.help}`),
      JSON.stringify(results.violations, null, 2),
    ).toEqual([]);
  });

  test('the About dialog has no violations', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /about this analysis/i }).click();
    await expect(page.getByRole('dialog')).toBeVisible();

    const results = await scan(page);
    expect(
      results.violations.map((v) => `${v.id}: ${v.help}`),
      JSON.stringify(results.violations, null, 2),
    ).toEqual([]);
  });

  test('the result is reachable by keyboard alone', async ({
    page,
    twoWayLink,
  }) => {
    /* The skip link exists so a keyboard user does not have to tab through the
     * map's controls to reach the answer. */
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);

    await page.keyboard.press('Tab');
    const first = await page.evaluate(() =>
      document.activeElement?.textContent?.trim(),
    );
    expect(first).toMatch(/skip to the closure result/i);
  });

  test('every interactive control has an accessible name', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);

    const unnamed = await page.evaluate(() => {
      const out: string[] = [];
      for (const el of document.querySelectorAll('button, [role="tab"], [role="option"]')) {
        const name =
          el.getAttribute('aria-label') ??
          el.getAttribute('title') ??
          el.textContent?.trim();
        if (!name) out.push(el.outerHTML.slice(0, 120));
      }
      return out;
    });
    expect(unnamed).toEqual([]);
  });
});
