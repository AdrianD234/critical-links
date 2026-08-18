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

/*
 * There is no engine to select any more.
 *
 * This spec used to click the dev engine switch, because V1 was the
 * default and the V2 panel did not exist until V2 was chosen. The V2
 * promotion makes V2 the sole ordinary product engine and removes the
 * switch entirely, so the panel is present from the first render.
 *
 * The absence of the switch is asserted below rather than assumed: if one
 * came back, this spec would be silently testing a different surface.
 */

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
    /* V2 is the product engine now; no switch exists to click. */
    await expect(
      page.getByRole('button', { name: 'V2 closure analysis' }),
      'the engine switch must be gone - V2 is the product engine',
    ).toHaveCount(0);

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
    /* The app formats distances in km, so 7,944.412 m renders as 7.94 km and
     * 4,915.533 m as 4.92 km. Asserting the rendered text rather than the raw
     * metres is the point of a browser test. */
    await expect(canonicalRow).toContainText('7.94');
    await expect(assumedRow).toContainText('4.92');
    await expect(canonicalRow).not.toContainText('4.92');

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
    /* V2 is the product engine now; no switch exists to click. */
    await expect(
      page.getByRole('button', { name: 'V2 closure analysis' }),
      'the engine switch must be gone - V2 is the product engine',
    ).toHaveCount(0);

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
