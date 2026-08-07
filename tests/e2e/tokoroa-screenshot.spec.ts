/*
 * Before/after evidence for the reported Tokoroa case.
 *
 * Link {1073a927-4c97-4c9a-b41a-bf6f5edf0cad}#12 is the 5,201 m section of
 * State Highway 1 between Tokoroa and Atiamuri whose inspector headline read
 * "Name not recorded" and whose map chip read "No name", while V1 reported
 * 13.64 km cut off. The full reproduction is in
 * docs/audits/detour-v2/reported-tokoroa-case/README.md.
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

import { API, exploreUrl, panel, test, waitForResult } from './fixtures.js';

const TOKOROA_AMDS_ID = '{1073a927-4c97-4c9a-b41a-bf6f5edf0cad}#12';
const LABEL = process.env.NZCL_SHOT_LABEL ?? 'after';
const OUT = 'docs/screenshots/detour-v2/tokoroa';

test.skip(
  !process.env.NZCL_CAPTURE_SCREENSHOTS,
  'evidence capture: set NZCL_CAPTURE_SCREENSHOTS=1 to regenerate',
);

test.describe('reported Tokoroa case', () => {
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

  test('inspector for the selected link', async ({ page, request }) => {
    const probe = await request.get(
      `${API}/api/v1/links/${encodeURIComponent(TOKOROA_AMDS_ID)}/detour?geometry=false`,
    );
    test.skip(
      !probe.ok(),
      'the Tokoroa link is not in the active snapshot (national ingest required)',
    );

    await page.goto(exploreUrl(TOKOROA_AMDS_ID, { focus: 'reverse' }));

    /*
     * Wait for the result rather than for a fixed delay. The detour on this
     * link removes 34 arcs from a 731,286-arc graph and takes a few seconds;
     * a sleep would either flake or waste time.
     */
    await waitForResult(page);
    /* Settle the panel animation so the frame is reproducible. */
    await page.waitForTimeout(2500);

    await page.screenshot({ path: `${OUT}/${LABEL}-full.png` });
    await panel(page).screenshot({ path: `${OUT}/${LABEL}-inspector.png` });
  });
});
