/**
 * The reviewer's merge conditions, in the browser, on the roads they were
 * raised about.
 *
 * The engine half of each condition is pinned in
 * python/tests/test_realdata_boundary.py. This is the half the engine cannot
 * check: every one of these conditions is about what a reader SEES, and a
 * response carrying the right fields in the right states still fails the
 * condition if the panel renders them somewhere nobody looks, or renders a
 * label where a reason was asked for.
 *
 * These name specific national links, so they need the national snapshot. On
 * any other snapshot they skip, and say which — the alternative is a suite that
 * asserts against whichever road happens to be first in a synthetic fixture,
 * which would pass without testing the conditions at all.
 *
 * Screenshots are written as evidence rather than compared: the conditions are
 * about wording and placement, which the assertions check, and a pixel
 * comparison would fail on a font before it failed on a missing caveat.
 */

import { mkdirSync } from 'node:fs';

import { API, expect, panel, test, waitForResult } from './fixtures.js';

const OUT = 'docs/screenshots/v2-promotion/merge-conditions';

/** The snapshot these links belong to. */
const NATIONAL = 'amds-national-2026-07-28-5b359d84';

/**
 * A national-snapshot URL at the advanced scope.
 *
 * All 26 reviewed cases are source-feature closures, so the conditions drawn
 * from them are stated at that scope. `v=2` because these are links this build
 * would write, not stale ones — the migration notice has its own tests.
 */
function advancedUrl(linkId: number): string {
  const q = new URLSearchParams({
    link: String(linkId),
    metric: 'distance',
    vehicle: 'car',
    scope: 'amds-feature',
    v: '2',
  });
  return `/?${q}`;
}

let onNational = false;

test.beforeAll(async ({ request }) => {
  const res = await request.get(`${API}/api/v1/network/metadata`);
  expect(res.ok(), 'the analysis API must be running').toBeTruthy();
  onNational = (await res.json()).snapshotId === NATIONAL;
});

test.beforeEach(() => {
  test.skip(
    !onNational,
    `these conditions name links in ${NATIONAL}; the active snapshot is a ` +
      'different one, so there is nothing here to assert against',
  );
  mkdirSync(OUT, { recursive: true });
});

/* ------------------------------------------------------------ condition 2 */

test.describe('condition 2 — the advanced scope shows its cost first', () => {
  /*
   * Source-feature scope removes an AMDS source record's every graph child.
   * That cost belongs where the choice is used, above the figures it changes.
   * It was in a collapsed disclosure, which a reader reaches after acting.
   */
  test('states the kilometres and the segment count above the result', async ({
    page,
  }) => {
    await page.goto(advancedUrl(375011));
    await waitForResult(page);

    const notice = page.locator('.notice--warn').first();
    await expect(notice).toBeVisible();
    /* 1.38 km across 13 graph segments — both numbers, not one. A reader shown
     * only the distance cannot tell one road from thirteen fragments of a
     * maintenance record. */
    await expect(notice).toContainText(/1\.38\s*km/i);
    await expect(notice).toContainText(/13 graph segments/i);

    /* Above the hero, not merely present. */
    const order = await page.evaluate(() => {
      const n = document.querySelector('.notice--warn');
      const h = document.querySelector('.headline');
      if (!n || !h) return 'missing';
      return n.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING
        ? 'notice first'
        : 'headline first';
    });
    expect(order).toBe('notice first');

    await page.screenshot({
      path: `${OUT}/condition-2-source-feature-cost.png`,
      fullPage: false,
    });
  });

  test('the ordinary default shows no such warning', async ({ page }) => {
    /* The cost is a property of the advanced choice. Showing it on every
     * segment closure would train readers past it. */
    const q = new URLSearchParams({
      link: '375011',
      metric: 'distance',
      vehicle: 'car',
      scope: 'segment',
      v: '2',
    });
    await page.goto(`/?${q}`);
    await waitForResult(page);
    await expect(panel(page)).not.toContainText(/graph segments/i);
  });
});

/* ------------------------------------------------------------ condition 3 */

test.describe('condition 3 — low topology confidence keeps a reason', () => {
  /* The seven cases review singled out: V2 asks the better question and the
   * local graph is uncertain, so the real-world claim stays caveated. */
  for (const linkId of [375011, 157091, 169247, 17097, 313963, 147489, 114903]) {
    test(`link ${linkId} shows the reason, not just the label`, async ({
      page,
    }) => {
      await page.goto(advancedUrl(linkId));
      await waitForResult(page);

      const text = await panel(page).innerText();
      expect(text).toMatch(/topology confidence low/i);
      /* The reason names WHAT is uncertain. A label that said only "low"
       * would pass a naive contains-check and tell a reader nothing. */
      expect(text).toMatch(/near-miss/i);
      expect(text).toMatch(/tolerance used when the network was ingested/i);

      if (linkId === 375011) {
        await page.screenshot({
          path: `${OUT}/condition-3-topology-confidence-375011.png`,
        });
      }
    });
  }
});

/* ------------------------------------------------------------ condition 4 */

test.describe('condition 4 — the measured crossing is identified', () => {
  test('names the roads in and out, the count, and lists the rest', async ({
    page,
  }) => {
    /* 375011 has 132 included crossings. "Added distance 340 m" with no
     * subject is a number about an unnamed one of them. */
    await page.goto(advancedUrl(375011));
    await waitForResult(page);

    const text = await panel(page).innerText();
    /* Which crossing, by road. */
    expect(text).toMatch(/in from .+, out to .+/i);
    expect(text).toMatch(/Herbert Street/i);
    expect(text).toMatch(/McAndrew Street/i);
    /* How many there were to choose from. */
    expect(text).toMatch(/crossings identified/i);
    expect(text).toMatch(/132/);

    /* And a way to inspect the alternatives. */
    const others = page.getByRole('group').filter({ hasText: /other .* crossings identified/i });
    const summary = page.locator('summary', {
      hasText: /other .* crossings identified/i,
    });
    await expect(summary).toBeVisible();
    await summary.click();
    /* The list carries the intact distance that ranked them, so a reader can
     * see WHY this one was chosen rather than being told it was. */
    await expect(page.locator('.b-row').first()).toBeVisible();
    void others;

    await page.screenshot({
      path: `${OUT}/condition-4-movement-context-375011.png`,
      fullPage: true,
    });
  });

  test('does not claim the chosen crossing is the busiest', async ({ page }) => {
    /* Nothing in the pipeline knows traffic volumes. The choice is by modelled
     * impact, and the panel has to say which it is. */
    await page.goto(advancedUrl(375011));
    await waitForResult(page);
    const text = await panel(page).innerText();
    expect(text).toMatch(/not as the busiest/i);
    expect(text).not.toMatch(/most used|busiest crossing is/i);
  });
});

/* ------------------------------------------------------------ condition 5 */

test.describe('condition 5 — a lost crossing is never a road cut off', () => {
  const CASES: [number, string][] = [
    [8887, 'Bluff Highway East'],
    [33082, 'Titahi Bay roundabout'],
    [27644, 'The Boulevard eastbound'],
    [29348, 'Swanston Street'],
    [51258, 'SH74 connector'],
  ];

  for (const [linkId, where] of CASES) {
    test(`${linkId} (${where}) says the crossing, not the road`, async ({
      page,
    }) => {
      await page.goto(advancedUrl(linkId));
      await waitForResult(page);

      /* The required shape, both lines. */
      await expect(page.locator('.headline .lab')).toHaveText(
        /one modelled through movement has no represented replacement/i,
      );
      await expect(page.locator('.headline .sub')).toContainText(
        /no physical isolation is identified in the represented graph/i,
      );

      /*
       * And nowhere in the panel — not in a heading, not in a detail sentence
       * rendered verbatim from the response, not in the map badge. This is the
       * assertion that matters: the headline being right is not enough if a
       * sentence further down says the road is cut off.
       */
      const text = await panel(page).innerText();
      expect(
        text.toLowerCase(),
        `${linkId} (${where}) said something was cut off while the ` +
          'represented graph shows nothing separated',
      ).not.toContain('cut off');

      await page.screenshot({
        path: `${OUT}/condition-5-lost-crossing-${linkId}.png`,
      });
    });
  }
});
