/**
 * Topology sensitivity, in a real desktop browser.
 *
 * ONE spec, deliberately. It exists because two path-level defects survived a
 * fully green script-level suite: candidates were ranked by a synthetic
 * crossing id, and the API never passed the canonical route into the candidate
 * search. Both produced a confident FALSE NEGATIVE - "no sensitivity found" -
 * while every unit test passed. Only exercising a real path found them.
 *
 * WHY THE ENDPOINT IS STUBBED HERE, stated plainly rather than buried.
 *
 * CI runs against a small synthetic fixture snapshot. It does not contain the
 * Darfield network, so link 234872, Clintons Road and McLaughlins Road do not
 * exist there and never can. The real numbers are proved against the real
 * snapshot by `python/tests/test_topology_sensitivity_api.py`, which asserts
 * 7,944.412 m canonical and 4,915.533 m counterfactual through the ordinary
 * API.
 *
 * What only a browser can prove is the part this spec keeps: that the client
 * renders the states, that the counterfactual is visibly distinct from the
 * canonical figure and never occupies its row, and that a stale response for
 * a previous selection is discarded. Those are properties of the client and
 * are independent of which snapshot is loaded, so stubbing the transport
 * tests them honestly rather than weakening the claim.
 */

import { exploreUrl, expect, test, waitForResult, watchConsole } from './fixtures.js';

/**
 * Select the V2 engine.
 *
 * V1 is the default and stays the default - `useState<Engine>('v1')` in
 * ExploreScreen - and the engine is component state, not a URL parameter.
 * So navigating alone leaves V1 mounted and the V2 panel simply does not
 * exist in the tree. An earlier version of this spec asserted on the panel
 * without clicking, and failed for that reason rather than for a defect.
 *
 * The switch is dev-only by design, and Playwright's webServer runs `vite
 * dev`, so it is present. Its ABSENCE is asserted rather than skipped: a
 * spec that quietly skips when the control is missing would go green in
 * exactly the situation it is meant to catch.
 */
async function selectV2Engine(page: import('@playwright/test').Page) {
  const v2 = page.getByRole('button', { name: 'V2 closure analysis' });
  await expect(
    v2,
    'the dev engine switch must be present - Playwright runs a vite dev server',
  ).toHaveCount(1);
  await v2.click();
}

const CAUSAL = 'Clintons Road x McLaughlins Road';

/** The shape the server returns for the Greendale case, to the metre. */
function greendalePayload(token: string) {
  return {
    available: true,
    token,
    state: 'TOPOLOGY_SENSITIVE',
    message: 'Topology-sensitive',
    topologySensitive: true,
    headline:
      'Topology-sensitive. The canonical represented route is 7944 m, but falls to ' +
      '4916 m if the unresolved Clintons Road x McLaughlins Road crossing is an ' +
      'at-grade junction.',
    canonicalAnswer: { isCanonical: true, status: 'OK', distanceM: 7944.412441731978 },
    counterfactuals: [
      {
        isCanonical: false,
        assumedJunctionCrossingIds: [-1],
        assumedJunctions: [
          {
            crossingId: -1,
            label: CAUSAL,
            classifierDisposition: null,
            classifierReason: null,
          },
        ],
        status: 'OK',
        distanceM: 4915.5332100612995,
        tested: true,
        untestedReason: null,
        individuallyChangesAnswer: true,
        whatChanged: ['distance 7944.4 m -> 4915.5 m'],
        assumptionKind: 'individual',
      },
    ],
    testedCandidates: 3,
    candidateCap: 3,
    capNote: null,
    analysisComplete: true,
  };
}

test.describe('Topology sensitivity', () => {
  test('the assumed route is shown separately from the canonical one', async ({
    page,
    twoWayLink,
  }) => {
    const console_ = watchConsole(page);

    await page.route('**/topology-sensitivity**', async (route) => {
      const token = new URL(route.request().url()).searchParams.get('token') ?? '';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(greendalePayload(token)),
      });
    });

    /* Default scope: V1 renders the headline this waits on, and V1 does not
     * support segment scope. Switching engine resets the scenario to the V2
     * default anyway, so forcing a V2-only scope in the URL only breaks the
     * V1 render that has to happen first. */
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    await selectV2Engine(page);

    /* Sensitivity is gated on the canonical boundary result having arrived -
     * it is a qualification of that answer, so that answer is on screen
     * first. */
    const panel = page.getByTestId('topology-sensitivity');
    await expect(panel).toBeVisible({ timeout: 90_000 });

    const state = page.getByTestId('ts-state');
    await expect(state).toHaveAttribute('data-state', 'TOPOLOGY_SENSITIVE', {
      timeout: 90_000,
    });
    await expect(state).toHaveText('Topology-sensitive');

    /* The crossing is NAMED. "unnamed road x unnamed road" was a real defect -
     * the governed naming layer held the names and the query read the wrong
     * column. */
    await expect(page.getByTestId('ts-crossing')).toHaveText(CAUSAL);

    /* Canonical and counterfactual are different elements, and the
     * counterfactual never occupies the canonical row. That separation is what
     * this whole feature rests on. */
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

  test('a response for a previous selection is discarded', async ({
    page,
    twoWayLink,
  }) => {
    /*
     * The defect class: the user selects link A, then link B, and A's slower
     * answer arrives second and renders against B. The output looks entirely
     * normal and is about the wrong road.
     *
     * Here every response carries a token that is NOT the current selection.
     * The panel must refuse all of them and stay in its checking state rather
     * than render a road the user has left.
     */
    await page.route('**/topology-sensitivity**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(greendalePayload('a-previous-selection')),
      });
    });

    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    await selectV2Engine(page);

    const panel = page.getByTestId('topology-sensitivity');
    await expect(panel).toBeVisible({ timeout: 90_000 });

    /* The stale payload must never be rendered, and the panel must not claim
     * a finding it does not have. */
    await expect(page.getByTestId('ts-crossing')).toHaveCount(0);
    await expect(page.getByTestId('ts-assumed')).toHaveCount(0);
    await expect(page.getByTestId('ts-state')).toHaveText(
      /Checking topology sensitivity/,
    );
  });
});
