/**
 * A failure of the closure engine must stay a failure of the closure engine.
 *
 * WHY THIS FILE EXISTS
 *
 * Falling back to the retired detour engine is the single most attractive wrong
 * fix available here. It is three lines, it turns a red panel green, and it
 * would be almost undetectable: the number that appeared would look entirely
 * plausible. It would also be an answer to a different question — the retired
 * engine closes every graph segment of one AMDS source feature and measures
 * between that feature's own two endpoints, while this one closes the selected
 * segment and measures trips across its boundary — and it would hide the exact
 * failure someone needed to see.
 *
 * So this asserts on the REQUESTS THE BROWSER ACTUALLY MADE, not on what the
 * source says. Every check reading a source file passes just as happily against
 * a fallback added at runtime, behind a feature flag, or in a dependency.
 *
 * The failures are forced by interception rather than waited for, because the
 * whole point is the path that is hard to reach: on a healthy backend a V2
 * error never happens, so a suite that only exercised the happy path would
 * prove nothing about what happens when it does.
 */

import type { Page, Request } from '@playwright/test';

import { exploreUrl, expect, test, waitForResult } from './fixtures.js';

/** The retired engine's route. Nothing the user does may reach it. */
const V1_DETOUR = /\/api\/v1\/links\/.+\/detour/;

/** The boundary analysis. Every user-facing figure comes from here. */
const V2_BOUNDARY = /\/api\/v2\/links\/.+\/boundary-analysis/;

/**
 * Every request the page makes, recorded from before the first navigation.
 *
 * Paths rather than whole URLs: the assertions are about which route was
 * called, and a query string carrying a link id would make the failure message
 * unreadable without telling the reader anything.
 */
function recordRequests(page: Page): { paths: string[]; urls: string[] } {
  const paths: string[] = [];
  const urls: string[] = [];
  page.on('request', (r: Request) => {
    urls.push(r.url());
    try {
      paths.push(new URL(r.url()).pathname);
    } catch {
      paths.push(r.url());
    }
  });
  return { paths, urls };
}

function v1DetourCalls(urls: string[]): string[] {
  return urls.filter((u) => V1_DETOUR.test(u));
}

test.describe('no fallback to the retired engine', () => {
  test('the ordinary workflow never calls the V1 detour route', async ({
    page,
    twoWayLink,
  }) => {
    const seen = recordRequests(page);

    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);

    /* The analysis really did run, so "no V1 call" is not vacuously true
     * because nothing was analysed at all. */
    expect(
      seen.urls.filter((u) => V2_BOUNDARY.test(u)).length,
      'the boundary analysis was never requested, so this proves nothing',
    ).toBeGreaterThan(0);

    expect(v1DetourCalls(seen.urls)).toEqual([]);
  });

  test('a forced V2 failure issues no V1 request', async ({
    page,
    twoWayLink,
  }) => {
    const seen = recordRequests(page);

    /*
     * A 500 from the analysis, and nothing else touched. Fulfilled rather than
     * aborted so the client takes the "the server answered badly" path, which
     * is the one a real backend fault produces.
     */
    let boundaryAttempts = 0;
    await page.route(V2_BOUNDARY, async (route) => {
      boundaryAttempts += 1;
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'forced failure for the fallback test' }),
      });
    });

    await page.goto(exploreUrl(twoWayLink.amdsId));

    /* The panel reports the failure as its own state. */
    await expect(page.locator('.headline .lab')).toHaveText(
      /analysis unresolved/i,
      { timeout: 30_000 },
    );

    /* The client retries a 5xx twice before giving up, so more than one attempt
     * is expected. What matters is that every one of them went to V2. */
    expect(boundaryAttempts).toBeGreaterThan(0);
    expect(
      v1DetourCalls(seen.urls),
      'a V2 failure fell back to the retired engine',
    ).toEqual([]);

    /*
     * And no figure was invented to fill the space. "Analysis unresolved" with
     * a number beside it would be the same defect wearing an honest label.
     */
    const panelText = await page.locator('.inspector, .sheet').first().innerText();
    expect(panelText).not.toMatch(/added distance/i);
  });

  test('retrying after a forced failure still issues no V1 request', async ({
    page,
    twoWayLink,
  }) => {
    /*
     * Retry is the moment a fallback is most tempting: the user has asked
     * again, and answering with something is more satisfying than answering
     * with the same error. It must ask the same engine.
     */
    const seen = recordRequests(page);

    await page.route(V2_BOUNDARY, async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'forced unavailability' }),
      });
    });

    await page.goto(exploreUrl(twoWayLink.amdsId));
    await expect(page.locator('.headline .lab')).toHaveText(
      /analysis unresolved/i,
      { timeout: 30_000 },
    );

    const before = seen.urls.filter((u) => V2_BOUNDARY.test(u)).length;
    await page.getByRole('button', { name: /try again/i }).click();
    await expect
      .poll(() => seen.urls.filter((u) => V2_BOUNDARY.test(u)).length, {
        timeout: 30_000,
        message: 'Try again did not re-request the analysis',
      })
      .toBeGreaterThan(before);

    expect(v1DetourCalls(seen.urls)).toEqual([]);
  });

  test('a connection failure issues no V1 request', async ({
    page,
    twoWayLink,
  }) => {
    /*
     * The transport-level failure, not the HTTP one. It reaches the client
     * through a different branch — a thrown fetch rather than a non-ok
     * response — and a fallback added on that branch would be invisible to the
     * checks above.
     */
    const seen = recordRequests(page);
    await page.route(V2_BOUNDARY, (route) => route.abort('connectionrefused'));

    await page.goto(exploreUrl(twoWayLink.amdsId));
    await expect(page.locator('.headline .lab')).toHaveText(
      /analysis unresolved/i,
      { timeout: 30_000 },
    );

    expect(v1DetourCalls(seen.urls)).toEqual([]);
  });

  test('the whole session touches only the V1 routes that are engine-neutral', async ({
    page,
    twoWayLink,
  }) => {
    /*
     * An inventory, not a spot check.
     *
     * Two `/api/v1/` routes remain in use and neither belongs to the retired
     * detour engine: `network/metadata` describes the snapshot, and
     * `links/search` finds a road by name. Both answer the same thing whichever
     * engine is analysing, and there is no `/api/v2/` equivalent of either to
     * move to.
     *
     * Listing them exhaustively is what makes this a gate rather than a
     * comment. A V1 call added anywhere — a new panel, a dependency, a
     * half-finished fallback — appears here as an unexpected path and fails,
     * and the failure names the path.
     */
    const seen = recordRequests(page);

    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);

    /* Exercise the controls too: a fallback wired into a scenario change would
     * not show up on a page that only ever loaded. */
    await page.locator('.scenario-summary-btn').click();
    await page.getByRole('button', { name: 'Heavy', exact: true }).click();
    await waitForResult(page);

    const ALLOWED_V1 = new Set([
      '/api/v1/network/metadata',
      '/api/v1/links/search',
    ]);

    const unexpected = [
      ...new Set(
        seen.paths.filter((p) => p.startsWith('/api/v1/') && !ALLOWED_V1.has(p)),
      ),
    ];

    expect(
      unexpected,
      'a request reached a V1 route that is not engine-neutral',
    ).toEqual([]);
  });
});
