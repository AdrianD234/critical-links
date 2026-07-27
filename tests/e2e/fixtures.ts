/**
 * Shared setup for the browser tests.
 *
 * Link identifiers are resolved from the live API rather than hard-coded, so
 * the suite keeps working across snapshots. A test that depends on a specific
 * road is explicit about *why* that road — one-way, two-way, long enough to
 * click reliably — rather than on the identifier itself.
 */

import { expect, test as base, type Page } from '@playwright/test';

export const API = process.env.NZCL_API_URL ?? 'http://127.0.0.1:8000';

export interface LinkFixture {
  amdsId: string;
  roadName: string;
  lengthM: number;
  oneway: boolean;
}

/**
 * Noise that is not the application's fault.
 *
 * Kept explicit and narrow rather than filtering by type: the point of the
 * console check is to catch real faults, and a broad filter would quietly
 * swallow them alongside the noise.
 */
const IGNORED_CONSOLE = [
  /* Advertisement injected by React in development. */
  /React DevTools/,
  /* Vite's HMR chatter. */
  /\[vite\]/,
  /*
   * Headless Chromium's software GL emits performance advisories on every
   * readPixels. A property of the CI renderer, not of the page — a real GPU
   * never produces them.
   */
  /GL Driver Message.*ReadPixels/,
  /GPU stall/,
];

/** A console listener that fails the test on anything unexpected. */
export function watchConsole(page: Page): { errors: string[] } {
  const errors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() !== 'error' && msg.type() !== 'warning') return;
    const text = msg.text();
    if (IGNORED_CONSOLE.some((p) => p.test(text))) return;
    errors.push(`${msg.type()}: ${text}`);
  });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  return { errors };
}

/**
 * How many features of a layer are currently drawn.
 *
 * Returns 0 rather than throwing while the style is still loading. Querying a
 * layer that does not exist yet raises inside MapLibre, and polling for
 * "has the map drawn yet" would otherwise generate the very console errors the
 * test is checking for.
 */
export async function renderedCount(page: Page, layer: string): Promise<number> {
  return page.evaluate((id) => {
    const m = (window as unknown as { __map?: any }).__map;
    if (!m || !m.isStyleLoaded?.() || !m.getLayer?.(id)) return 0;
    try {
      return m.queryRenderedFeatures({ layers: [id] }).length;
    } catch {
      return 0;
    }
  }, layer);
}

/**
 * Find a link matching a predicate, without naming a specific road.
 *
 * The suite has to run against two very different snapshots: the real
 * Wellington extract locally, and the small fixture network CI builds. Naming
 * "Moonshine Road" would tie every test to one dataset and make the CI run
 * either impossible or a different, weaker suite.
 *
 * So the tests state the *property* they need — two-way, one-way, long enough
 * to have a meaningful result — and discover a link that has it.
 */
async function findLink(
  request: import('@playwright/test').APIRequestContext,
  predicate: (l: LinkFixture) => boolean,
  what: string,
): Promise<LinkFixture> {
  /* A blank name matches everything the search endpoint will return. */
  const res = await request.get(`${API}/api/v1/links/search?name=&limit=200`);
  expect(res.ok(), 'the analysis API must be running').toBeTruthy();
  const body = await res.json();

  const link = (body.results as LinkFixture[])
    .filter((r) => r.roadName)
    .find(predicate);

  expect(
    link,
    `no ${what} found in the active snapshot; the browser suite needs one`,
  ).toBeTruthy();
  return link!;
}

export const test = base.extend<{
  /** A two-way link, so both directions are represented. */
  twoWayLink: LinkFixture;
  /** A one-way link, so reverse is genuinely absent from the response. */
  oneWayLink: LinkFixture;
}>({
  /*
   * A two-way link that actually produces a replacement path.
   *
   * "Two-way" alone is not enough: the first two-way link in the snapshot may
   * be a rural dead end whose closure strands it, and a suite built on that
   * would never exercise the ordinary successful-result path at all. So the
   * candidates are probed until one returns OK — the property the tests need.
   */
  twoWayLink: async ({ request }, use) => {
    const res = await request.get(`${API}/api/v1/links/search?name=&limit=200`);
    expect(res.ok(), 'the analysis API must be running').toBeTruthy();
    const { results } = await res.json();

    const candidates = (results as LinkFixture[]).filter(
      (l) => l.roadName && !l.oneway && l.lengthM > 100,
    );

    for (const link of candidates.slice(0, 12)) {
      const d = await request.get(
        `${API}/api/v1/links/${encodeURIComponent(link.amdsId)}` +
          `/detour?direction=both&geometry=false`,
      );
      if (!d.ok()) continue;
      const body = await d.json();
      if (body.reverse?.status === 'OK' && body.forward?.status === 'OK') {
        await use(link);
        return;
      }
    }

    throw new Error(
      'no two-way link with a computable detour found in the active snapshot',
    );
  },

  oneWayLink: async ({ request }, use) => {
    await use(
      await findLink(request, (l) => l.oneway && l.lengthM > 100, 'one-way link'),
    );
  },
});

export { expect };

/** The app's URL for a given link and options. */
export function exploreUrl(
  amdsId: string,
  params: Record<string, string> = {},
): string {
  const q = new URLSearchParams({
    link: amdsId,
    metric: 'distance',
    vehicle: 'car',
    scope: 'amds-feature',
    ...params,
  });
  return `/?${q}`;
}

/**
 * The result panel, whatever shape it currently takes.
 *
 * A desktop column and a mobile bottom sheet are different elements, and a
 * selector that only knows about one silently passes on the other by finding
 * nothing to assert against.
 */
export function panel(page: Page) {
  return page.locator('.inspector, .sheet').first();
}

/** Wait until the inspector is showing a settled result rather than skeletons. */
export async function waitForResult(page: Page) {
  await expect(page.locator('.headline')).toBeVisible();
  await expect(page.locator('.headline .skeleton')).toHaveCount(0, {
    timeout: 45_000,
  });
}
