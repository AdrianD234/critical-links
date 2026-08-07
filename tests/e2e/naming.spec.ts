/*
 * The reported defect, as a test.
 *
 * A section of State Highway 3 near Te Mapara rendered as "(unnamed link)".
 * Link 373604 in the national snapshot. Two things had to be true for that to
 * happen: the name was never looked up properly, and the interface had one
 * phrase for every reason a name might be missing.
 *
 * These tests run against whatever snapshot is active. The link-373604 case is
 * skipped when it is not present — CI builds a small synthetic network — but
 * the rules that apply to every road are checked unconditionally, so the
 * guarantee does not evaporate on the machine where it matters least.
 */

import { expect } from '@playwright/test';

import { API, test } from './fixtures.js';

/** The link from the reported screenshot. */
const REPORTED_LINK = 373604;

/** Wording that must never reach a reader again. */
const BANNED = ['(unnamed link)', '(unnamed)', 'undefined', 'null'];

test.describe('road naming', () => {
  test('the reported link is named, and not by its corridor', async ({ request }) => {
    const res = await request.get(
      `${API}/api/v1/links/${REPORTED_LINK}/detour?geometry=false`,
    );
    test.skip(
      res.status() === 404,
      'link 373604 belongs to the national snapshot; not present here',
    );
    expect(res.ok()).toBeTruthy();

    const link = (await res.json()).selectedLink;
    expect(link.roadName, 'the reported link must have a name').toBeTruthy();
    expect(link.roadName).toBe('State Highway 3');

    /*
     * RAMM calls this stretch "Hamilton to New Plymouth". That is a corridor
     * spanning hundreds of kilometres and is never a road's display name — the
     * failure mode this assertion exists to catch.
     */
    expect(link.roadName).not.toContain('to New Plymouth');
    expect(link.roadName).not.toMatch(/^\d{2,3}-\d{3,4}/);

    /* And the classification has to travel with the name. */
    expect(link.naming?.status).toBe('route_designation_only');
    expect(link.naming?.source).toBe('linz_road_sections');
  });

  test('no link reports a name without saying where it came from', async ({
    request,
  }) => {
    const res = await request.get(`${API}/api/v1/links/search?name=&limit=200`);
    expect(res.ok(), 'the analysis API must be running').toBeTruthy();
    const results = (await res.json()).results as Array<{
      roadName: string | null;
      naming?: { status: string; source: string | null; label: string | null };
    }>;
    expect(results.length).toBeGreaterThan(0);

    for (const r of results) {
      if (!r.naming) continue; // snapshot predating the naming layer
      if (r.roadName) {
        expect(
          r.naming.source,
          `"${r.roadName}" is displayed with no recorded source`,
        ).toBeTruthy();
      } else {
        expect(
          r.naming.label,
          'a link with no name must say which kind of no-name it is',
        ).toBeTruthy();
      }
      for (const banned of BANNED) {
        expect(r.roadName ?? '').not.toContain(banned);
        expect(r.naming.label ?? '').not.toContain(banned);
      }
    }
  });

  test('a corridor name never appears in the road-name position', async ({
    request,
  }) => {
    const res = await request.get(`${API}/api/v1/links/search?name=&limit=200`);
    expect(res.ok()).toBeTruthy();
    for (const r of (await res.json()).results as Array<{ roadName: string | null }>) {
      if (!r.roadName) continue;
      /* "X to Y" spanning two cities is a corridor, not a name. */
      expect(r.roadName).not.toMatch(/\bHamilton to\b|\bto New Plymouth\b/i);
      /* RAMM route-section codes: "003-0076". */
      expect(r.roadName).not.toMatch(/^\d{2,3}[A-Za-z]?-\d{3,4}/);
      /* Reference-station strings the native fix replaced. */
      expect(r.roadName).not.toMatch(/^SH\s*\d+[NS]?\/\d+/i);
    }
  });

  test('the inspector heading is never the old placeholder', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(`/?link=${encodeURIComponent(twoWayLink.amdsId)}`);
    const heading = page.locator('#result');
    await expect(heading).toBeVisible();
    const text = (await heading.textContent()) ?? '';
    expect(text.trim().length).toBeGreaterThan(0);
    for (const banned of BANNED) {
      expect(text).not.toContain(banned);
    }
  });

  test('naming coverage is reported rather than left to be inferred', async ({
    request,
  }) => {
    const res = await request.get(`${API}/api/v1/network/metadata`);
    expect(res.ok()).toBeTruthy();
    const meta = await res.json();
    if (!meta.naming) return; // snapshot predating the naming layer

    expect(meta.naming.graphLinks).toBeGreaterThan(0);
    expect(meta.naming.namedLinks).toBeLessThanOrEqual(meta.naming.graphLinks);

    /*
     * Any source whose names are being displayed must have its attribution
     * published with them. That is the condition the licence is granted on,
     * so it is checked rather than remembered.
     */
    for (const a of meta.nameAttributions ?? []) {
      expect(a.attribution, `${a.source} is displayed with no attribution`)
        .toBeTruthy();
      expect(a.licence).toBeTruthy();
    }
  });
});
