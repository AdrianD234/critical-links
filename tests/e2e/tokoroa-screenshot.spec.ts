/*
 * Before/after evidence for the reported Tokoroa case.
 *
 * Link {1073a927-4c97-4c9a-b41a-bf6f5edf0cad}#12 is the 5,201 m section of
 * State Highway 1 between Tokoroa and Atiamuri whose inspector headline read
 * "Name not recorded" and whose map chip read "No name", while the retired
 * engine reported 13.64 km cut off. The full reproduction is in
 * docs/audits/detour-v2/reported-tokoroa-case/README.md.
 *
 * This file used to drive the case through the engine switch: load the page,
 * click "V2 closure analysis", photograph the preview. There is no switch any
 * more and no preview to photograph — the closure engine is what answers — so
 * the captures are of the ordinary product, at both scopes, and the engine
 * assertions moved to python/tests/test_realdata_boundary.py where they can be
 * made exactly.
 *
 * Run against the national snapshot; skipped anywhere the link is absent, so a
 * checkout carrying only the Wellington pilot does not fail on it.
 *
 * Opt-in, for the same reason naming-screenshot.spec.ts is: this writes into
 * docs/ instead of asserting, and on CI it would overwrite committed images
 * with keyless renderings that have no basemap. A test that mutates tracked
 * files as a side effect is not a test.
 *
 *   NZCL_CAPTURE_SCREENSHOTS=1 NZCL_SHOT_LABEL=after \
 *     npx playwright test tokoroa-screenshot --project=desktop
 */

import { mkdirSync } from 'node:fs';

import { API, exploreUrl, expect, panel, test, waitForResult } from './fixtures.js';

const TOKOROA_AMDS_ID = '{1073a927-4c97-4c9a-b41a-bf6f5edf0cad}#12';
const LABEL = process.env.NZCL_SHOT_LABEL ?? 'after';
const OUT = 'docs/screenshots/detour-v2/tokoroa';

test.skip(
  !process.env.NZCL_CAPTURE_SCREENSHOTS,
  'evidence capture: set NZCL_CAPTURE_SCREENSHOTS=1 to regenerate',
);

/** Is the reported link in the active snapshot at all? */
async function probe(
  request: import('@playwright/test').APIRequestContext,
  scope: string,
) {
  return request.get(
    `${API}/api/v2/links/${encodeURIComponent(TOKOROA_AMDS_ID)}` +
      `/boundary-analysis?scope=${scope}&geometry=false&corridor=false` +
      `&isolation=true&allMovements=false`,
  );
}

test.describe('reported Tokoroa case', () => {
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

  test('the default scope — the segment that was clicked', async ({
    page,
    request,
  }) => {
    const res = await probe(request, 'segment');
    test.skip(
      !res.ok(),
      'the Tokoroa link is not in the active snapshot (national ingest required)',
    );

    await page.goto(exploreUrl(TOKOROA_AMDS_ID));

    /*
     * Wait for the result rather than for a fixed delay. This closure removes
     * arcs from a 731,286-arc graph and takes a couple of seconds; a sleep
     * would either flake or waste time.
     */
    await waitForResult(page);
    /* Settle the panel animation so the frame is reproducible. */
    await page.waitForTimeout(2500);

    /* The name defect this case was reported for. The heading must carry the
     * road's label, not "Name not recorded". */
    await expect(page.locator('.insp-head h2')).not.toHaveText(
      /name not recorded/i,
    );

    await page.screenshot({ path: `${OUT}/${LABEL}-full.png` });
    await panel(page).screenshot({ path: `${OUT}/${LABEL}-inspector.png` });
  });

  test('the advanced scope, and the confidence it comes with', async ({
    page,
    request,
  }) => {
    /*
     * Three unresolved near-miss endpoints lie within 25 m of this closure, so
     * the engine returns topologyConfidence: low for it under source-feature
     * scope.
     *
     * The assertion matters more than the image. A confidence field that
     * nothing surfaces is not a safeguard, and this one is the difference
     * between "these roads lose access" and "these roads lose access unless
     * the ingest joined two endpoints it decided not to join".
     */
    const res = await probe(request, 'source_feature');
    test.skip(!res.ok(), 'the Tokoroa link is not in the active snapshot');
    const body = await res.json();
    test.skip(
      body.isolation?.topologyConfidence !== 'low',
      'this snapshot does not report low confidence for the reported link',
    );

    await page.goto(exploreUrl(TOKOROA_AMDS_ID, { scope: 'amds-feature' }));
    await waitForResult(page);

    /* Both caveats, above the figures they qualify. */
    const cost = page
      .locator('.notice--warn')
      .filter({ hasText: /graph segments/i })
      .first();
    await expect(cost).toBeVisible({ timeout: 60_000 });

    const warning = page
      .locator('.notice--warn')
      .filter({ hasText: 'Topology confidence low' })
      .first();
    await expect(warning).toBeVisible({ timeout: 60_000 });
    await expect(warning).toContainText('near-miss');

    await page.waitForTimeout(2500);
    await panel(page).screenshot({ path: `${OUT}/${LABEL}-source-feature.png` });
    await warning.screenshot({ path: `${OUT}/${LABEL}-low-confidence.png` });
  });
});
