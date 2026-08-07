/*
 * Evidence for the reported case, captured rather than described.
 *
 * Link 373604 is the section of State Highway 3 near Te Mapara whose tooltip
 * read "(unnamed link)". Run with `--project=desktop` against the national
 * snapshot; skipped anywhere the link is not present.
 *
 * Separate from screenshots.spec.ts because that file covers the review set for
 * the Explore workflow; this one exists to show a single defect closed.
 */

import { expect } from '@playwright/test';

import { API, test } from './fixtures.js';

const REPORTED_AMDS_ID = '{1991823e-c175-4c71-a684-70f578b699be}';
const OUT = 'docs/screenshots/naming';

/*
 * Opt-in, because this writes into `docs/` rather than asserting anything.
 *
 * It is evidence capture, not a gate: on CI it would overwrite committed
 * screenshots with keyless renderings that have no basemap, and a test that
 * mutates tracked files as a side effect is not a test. Run it deliberately:
 *
 *     NZCL_CAPTURE_SCREENSHOTS=1 npx playwright test naming-screenshot --project=desktop
 *
 * The behaviour it illustrates is asserted for real in naming.spec.ts, which
 * does run everywhere.
 */
test.skip(
  !process.env.NZCL_CAPTURE_SCREENSHOTS,
  'evidence capture: set NZCL_CAPTURE_SCREENSHOTS=1 to regenerate',
);

test.describe('naming evidence', () => {
  test('the reported link, named', async ({ page, request }) => {
    const probe = await request.get(
      `${API}/api/v1/links/${encodeURIComponent(REPORTED_AMDS_ID)}/detour?geometry=false`,
    );
    test.skip(probe.status() === 404, 'not the national snapshot');

    await page.goto(`/?link=${encodeURIComponent(REPORTED_AMDS_ID)}`);
    const heading = page.locator('#result');
    await expect(heading).toHaveText('State Highway 3');

    await page.waitForTimeout(2500); // let the map settle before capturing
    await page.screenshot({
      path: `${OUT}/373604-named.png`,
      clip: { x: 0, y: 0, width: 1440, height: 900 },
    });

    /* The inspector alone, where the name and its provenance are shown. */
    const inspector = page.locator('.inspector').first();
    if (await inspector.count()) {
      await inspector.screenshot({ path: `${OUT}/373604-inspector.png` });
    }
  });

  test('naming coverage, as the application reports it', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /about|info/i }).first().click();
    const dialog = page.locator('dialog[open]');
    await expect(dialog).toBeVisible();

    /*
     * The section, not the whole dialog. About is long enough that road names
     * sit below the fold, and a screenshot of the dialog captures the part
     * that has nothing to do with naming.
     */
    const section = dialog.locator('section', {
      has: page.getByRole('heading', { name: 'Road names' }),
    });
    await expect(section).toBeVisible();
    await section.scrollIntoViewIfNeeded();
    await section.screenshot({ path: `${OUT}/naming-coverage.png` });
  });
});
