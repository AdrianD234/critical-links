/**
 * The map-view acceptance screenshots.
 *
 * Gated twice: on NZCL_SCREENSHOTS=1 like every screenshot generator, and at
 * runtime on a LINZ key, because a keyless render would silently overwrite the
 * tracked key-rendered set with pictures of the graphite ground.
 *
 * The four-mode comparison set is one page session — same centre, same zoom,
 * same settled analytical result — with only the map view changed between
 * captures, so the images differ in exactly the thing under review.
 */

import { mkdirSync } from 'node:fs';

import { API, expect, exploreUrl, test, waitForResult } from './fixtures.js';
import { mapReady, placeSpan, zoomToRoad } from './map-helpers.js';

const OUT = 'docs/screenshots/map-view';
const ENABLED = process.env.NZCL_SCREENSHOTS === '1';
const SPAN_ENABLED = process.env.VITE_ENABLE_OUTAGE_SPAN_EDITOR === '1';

test.describe('map view screenshots', () => {
  test.skip(!ENABLED, 'set NZCL_SCREENSHOTS=1 to regenerate the documentation set');
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

  /* One canonical viewport; the laptop project would double every file. */
  test.beforeEach(({ }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'desktop only');
  });

  async function requireKey(page: import('@playwright/test').Page) {
    const keyed = await page.evaluate(
      () => Boolean((window as unknown as { __map?: maplibregl.Map }).__map?.getSource?.('linz')),
    );
    test.skip(!keyed, 'screenshots need a real LINZ key');
  }

  async function settled(page: import('@playwright/test').Page) {
    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const map = (window as unknown as { __map: maplibregl.Map }).__map;
            return map.areTilesLoaded();
          }),
        { timeout: 30_000 },
      )
      .toBe(true);
    await page.waitForTimeout(1500);
  }

  async function setMode(page: import('@playwright/test').Page, name: string) {
    await page.getByRole('button', { name: /^map view/i }).click();
    await page.getByRole('radio', { name }).check();
    await page.keyboard.press('Escape');
  }

  test('the four modes over one selected-link closure', async ({ page, request }) => {
    /* The picture has to show what each mode ADDS, and a remote state-highway
     * gorge — which the generic two-way fixture is free to pick — has no
     * streets to show. So the comparison closes a road that is plausibly in a
     * town: two-way, named "… Street", and probed to actually divert, the
     * same property probe the fixtures use. Still snapshot-agnostic — any
     * urban snapshot has one. */
    const res = await request.get(`${API}/api/v1/links/search?name=street&limit=200`);
    expect(res.ok(), 'the analysis API must be running').toBeTruthy();
    const { results } = (await res.json()) as {
      results: { amdsId: string; roadName: string; lengthM: number; oneway: boolean }[];
    };
    let urban: string | null = null;
    for (const l of results.filter(
      (l) => /street$/i.test(l.roadName ?? '') && !l.oneway && l.lengthM > 100,
    ).slice(0, 12)) {
      const d = await request.get(
        `${API}/api/v2/links/${encodeURIComponent(l.amdsId)}` +
          `/boundary-analysis?scope=segment&geometry=false&corridor=false` +
          `&isolation=false&allMovements=false`,
      );
      if (!d.ok()) continue;
      if ((await d.json()).headline === 'Through movement diverts') {
        urban = l.amdsId;
        break;
      }
    }
    expect(urban, 'no divertible two-way "… Street" link in this snapshot').toBeTruthy();

    await page.goto(exploreUrl(urban!));
    await waitForResult(page);
    await mapReady(page);
    await requireKey(page);

    /* The result fits its own bounds; the comparison instead anchors on the
     * closure at a street-level zoom where LINZ context exists — identically
     * for all four captures. */
    await page.evaluate(() => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      const data = (
        map.getSource('closure') as { serialize(): { data: GeoJSON.FeatureCollection } }
      ).serialize().data;
      const g = data.features[0]?.geometry;
      const coords =
        g?.type === 'LineString'
          ? (g.coordinates as GeoJSON.Position[])
          : g?.type === 'MultiLineString'
            ? (g.coordinates[0] as GeoJSON.Position[])
            : null;
      if (!coords?.length) throw new Error('no closure geometry to frame');
      const mid = coords[Math.floor(coords.length / 2)] as [number, number];
      map.jumpTo({ center: mid, zoom: 14.2 });
    });

    /* Aerial doubles as "Aerial with a selected-link closure": the whole
     * comparison set shares this result. */
    for (const [mode, file] of [
      ['Analysis', 'analysis'],
      ['Streets', 'streets'],
      ['Aerial', 'aerial'],
      ['Off', 'off'],
    ] as const) {
      await setMode(page, mode);
      await settled(page);
      await page.screenshot({ path: `${OUT}/${file}.png` });
    }

    await setMode(page, 'Analysis');
    await page.getByRole('button', { name: /^map view/i }).click();
    await page.screenshot({ path: `${OUT}/selector-open.png` });
    await page.keyboard.press('Escape');
  });

  test('aerial over farmland, forest and urban streets', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await requireKey(page);
    await setMode(page, 'Aerial');

    /* Fixed, deliberately chosen ground: Canterbury plains for bright
     * paddocks, the Whirinaki forest for dark bush, central Wellington for
     * urban streets. The snapshot's analytical extent does not matter here —
     * the subject is the photography under the network. */
    for (const [file, lon, lat, zoom] of [
      ['aerial-farmland', 172.24, -43.71, 14],
      ['aerial-forest', 176.71, -38.65, 13],
      ['aerial-urban', 174.776, -41.29, 15],
    ] as const) {
      await page.evaluate(
        ([x, y, z]) => {
          const map = (window as unknown as { __map: maplibregl.Map }).__map;
          map.jumpTo({ center: [x, y] as never, zoom: z });
        },
        [lon, lat, zoom],
      );
      await settled(page);
      await page.screenshot({ path: `${OUT}/${file}.png` });
    }
  });

  test('aerial under an A/B outage', async ({ page }) => {
    test.skip(!SPAN_ENABLED, 'VITE_ENABLE_OUTAGE_SPAN_EDITOR is not set');
    await page.goto('/');
    await mapReady(page);
    await requireKey(page);

    await zoomToRoad(page);
    await placeSpan(page);
    await setMode(page, 'Aerial');
    await settled(page);
    await page.screenshot({ path: `${OUT}/aerial-ab-outage.png` });
  });
});
