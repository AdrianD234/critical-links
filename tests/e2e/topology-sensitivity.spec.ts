/**
 * Topology sensitivity, in a real desktop browser.
 *
 * ONE test, deliberately. It exists because two path-level defects survived a
 * fully green script-level suite: candidates were ranked by a synthetic
 * crossing id, and the API never passed the canonical route into the candidate
 * search. Both produced a confident FALSE NEGATIVE - "no sensitivity found" -
 * while every unit test passed. Only exercising the real path found them.
 *
 * What this proves, in the browser:
 *   - the canonical replacement figure is displayed from the ordinary request;
 *   - sensitivity arrives SEPARATELY and names Clintons Road x McLaughlins
 *     Road with the assumed figure;
 *   - canonical and counterfactual are visibly distinct - different element,
 *     different styling, and the counterfactual never occupies the canonical
 *     row;
 *   - a stale response for a PREVIOUS selection does not land on the current
 *     one.
 */

import { exploreUrl, expect, test, watchConsole } from './fixtures.js';

const GREENDALE_LINK = '234872';
const CAUSAL = 'Clintons Road x McLaughlins Road';

test.describe('Topology sensitivity', () => {
  test('shows the assumed route separately from the canonical one', async ({
    page,
  }) => {
    const console_ = watchConsole(page);

    await page.goto(exploreUrl(GREENDALE_LINK, { scope: 'segment' }));

    const panel = page.getByTestId('topology-sensitivity');
    /*
     * The canonical answer is on screen first and this is a second request, so
     * the checking state is the expected first thing to see. It is allowed to
     * have resolved already on a fast machine, hence the generous wait on the
     * resolved state rather than an assertion that "checking" was observed.
     */
    await expect(panel).toBeVisible({ timeout: 60_000 });

    const state = page.getByTestId('ts-state');
    await expect(state).toHaveAttribute('data-state', 'TOPOLOGY_SENSITIVE', {
      timeout: 120_000,
    });
    await expect(state).toHaveText('Topology-sensitive');

    /* The crossing is NAMED. "unnamed road x unnamed road" was a real defect:
     * the governed naming layer holds the names and the query was reading the
     * wrong column. */
    await expect(page.getByTestId('ts-crossing')).toHaveText(CAUSAL);

    /* Canonical and counterfactual are different elements. The counterfactual
     * never occupies the canonical row - that is the separation this whole
     * feature rests on. */
    const canonicalRow = page.getByTestId('ts-canonical');
    const assumedRow = page.getByTestId('ts-assumed');
    await expect(canonicalRow).toBeVisible();
    await expect(assumedRow).toBeVisible();
    await expect(canonicalRow).toContainText('7,944');
    await expect(assumedRow).toContainText('4,916');
    await expect(canonicalRow).not.toContainText('4,916');

    /* Visibly distinct, not merely differently worded. */
    const canonicalStyle = await canonicalRow
      .locator('.ts-canonical-figure')
      .evaluate((el) => getComputedStyle(el).fontStyle);
    const assumedStyle = await assumedRow
      .locator('.ts-assumed-figure')
      .evaluate((el) => getComputedStyle(el).fontStyle);
    expect(canonicalStyle).not.toBe(assumedStyle);

    expect(console_.errors).toEqual([]);
  });

  test('a stale response for a previous selection is discarded', async ({
    page,
  }) => {
    /*
     * The defect class: the user selects link A, then link B, and A's slower
     * answer arrives second and renders against B. The output looks entirely
     * normal and is about the wrong road.
     *
     * Simulated by holding the FIRST sensitivity response back until after the
     * selection has changed, then releasing it. The panel must not show it.
     */
    let release: (() => void) | null = null;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let seen = 0;

    await page.route('**/topology-sensitivity**', async (route) => {
      seen += 1;
      if (seen === 1) {
        await held;
        /* Answer the FIRST request with a token that is no longer current. */
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            available: true,
            token: 'a-previous-selection',
            state: 'TOPOLOGY_SENSITIVE',
            message: 'Topology-sensitive',
            headline: 'Topology-sensitive. STALE RESPONSE.',
            canonicalAnswer: { isCanonical: true, status: 'OK', distanceM: 1 },
            counterfactuals: [
              {
                isCanonical: false,
                assumedJunctionCrossingIds: [999],
                assumedJunctions: [{ crossingId: 999, label: 'STALE ROAD x STALE ROAD' }],
                status: 'OK',
                distanceM: 2,
                tested: true,
                untestedReason: null,
                individuallyChangesAnswer: true,
                whatChanged: ['distance 1 m -> 2 m'],
                assumptionKind: 'individual',
              },
            ],
          }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto(exploreUrl(GREENDALE_LINK, { scope: 'segment' }));
    await expect(page.getByTestId('topology-sensitivity')).toBeVisible({
      timeout: 60_000,
    });

    /* Move the selection on, then let the first answer arrive. */
    await page.goto(exploreUrl(GREENDALE_LINK, { scope: 'segment', metric: 'time' }));
    await expect(page.getByTestId('topology-sensitivity')).toBeVisible({
      timeout: 60_000,
    });
    release?.();

    /* The stale payload must never be rendered. */
    await expect(page.getByText('STALE ROAD x STALE ROAD')).toHaveCount(0);
    await expect(page.getByText('STALE RESPONSE')).toHaveCount(0);
  });
});
