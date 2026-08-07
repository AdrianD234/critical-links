/*
 * The synthetic snapshot must behave like a miniature of a real one.
 *
 * The browser gate was red for weeks and nobody could see why, because the
 * routing tests were green: they read `geom_2193` and the graph tables, and
 * every defect lived in the columns only the map reads. The snapshot row
 * carried no coverage metadata and no extents, so the application had nothing
 * to fit and opened at the national fallback where none of the seven links are
 * drawn; the counts stayed at zero while seven links sat in the table; and
 * `geom_4326` held NZTM eastings and northings under a WGS84 label — a
 * longitude of 1,749,100 degrees.
 *
 * These tests exist so that a fixture which is not a faithful miniature fails
 * here, once and legibly, rather than as a dozen timeouts elsewhere.
 *
 * Everything is asserted against whatever snapshot is active, so this is
 * meaningful against a real one too — it just cannot check the synthetic
 * labelling there, and says so.
 */

import { expect, test } from './fixtures.js';
import { API } from './fixtures.js';

/** New Zealand, generously. A fixture outside this is not in the country. */
const NZ = { west: 166, east: 179, south: -48, north: -34 };

async function metadata(request: import('@playwright/test').APIRequestContext) {
  const res = await request.get(`${API}/api/v1/network/metadata`);
  expect(res.ok(), 'the analysis API must be running').toBeTruthy();
  return res.json();
}

test.describe('snapshot contract', () => {
  test('reports its coverage rather than leaving it unknown', async ({
    request,
  }) => {
    const meta = await metadata(request);
    expect(meta.coverage, 'coverage metadata is missing entirely').toBeTruthy();
    expect(meta.coverage.kind).not.toBe('unknown');
    expect(['national', 'regional', 'synthetic']).toContain(meta.coverage.kind);
    expect(meta.coverage.name).toBeTruthy();
    expect(meta.coverage.name).not.toBe('Unnamed extract');
  });

  test('a non-national snapshot gives the map somewhere to open', async ({
    request,
  }) => {
    const meta = await metadata(request);
    test.skip(
      meta.coverage?.isNational === true,
      'a national snapshot has no display extent by design: the map fits NZ',
    );

    const ext = meta.coverage?.displayExtentWgs84;
    expect(ext, 'display extent is null: the map has nothing to fit to').toBeTruthy();

    /* A real envelope, in the right hemisphere, not a degenerate point. */
    expect(ext.southWest.lon).toBeLessThan(ext.northEast.lon);
    expect(ext.southWest.lat).toBeLessThan(ext.northEast.lat);
    for (const corner of [ext.southWest, ext.northEast]) {
      expect(corner.lon).toBeGreaterThan(NZ.west);
      expect(corner.lon).toBeLessThan(NZ.east);
      expect(corner.lat).toBeGreaterThan(NZ.south);
      expect(corner.lat).toBeLessThan(NZ.north);
    }
  });

  test('the snapshot row agrees with its own rows', async ({ request }) => {
    const res = await request.get(`${API}/health`);
    expect(res.ok()).toBeTruthy();
    const health = await res.json();
    const meta = await metadata(request);

    /* Zero here was the tell: seven links existed and the row said none. */
    expect(health.links).toBeGreaterThan(0);
    expect(health.arcs).toBeGreaterThan(0);
    expect(health.nodes).toBeGreaterThan(0);
    expect(health.links).toBe(meta.graph.links);
    expect(health.arcs).toBe(meta.graph.arcs);
    expect(health.nodes).toBe(meta.graph.nodes);
  });

  test('route geometry is valid WGS84, not NZTM wearing a WGS84 label', async ({
    request,
    twoWayLink,
  }) => {
    const res = await request.get(
      `${API}/api/v1/links/${encodeURIComponent(twoWayLink.amdsId)}/detour`,
    );
    expect(res.ok()).toBeTruthy();
    const body = await res.json();

    const collections = [
      body.closure?.geoJson,
      body.forward?.routeGeoJson,
      body.reverse?.routeGeoJson,
    ].filter(Boolean);
    expect(collections.length, 'nothing was returned to check').toBeGreaterThan(0);

    let checked = 0;
    for (const fc of collections) {
      for (const f of fc.features ?? []) {
        const coords = (f.geometry?.coordinates ?? []).flat(
          f.geometry?.type === 'MultiLineString' ? 1 : 0,
        ) as number[][];
        for (const [lon, lat] of coords) {
          expect(Number.isFinite(lon) && Number.isFinite(lat)).toBe(true);
          expect(Math.abs(lon), `longitude ${lon} is not a longitude`).toBeLessThanOrEqual(180);
          expect(Math.abs(lat), `latitude ${lat} is not a latitude`).toBeLessThanOrEqual(90);
          expect(lon).toBeGreaterThan(NZ.west);
          expect(lon).toBeLessThan(NZ.east);
          expect(lat).toBeGreaterThan(NZ.south);
          expect(lat).toBeLessThan(NZ.north);
          checked += 1;
        }
      }
    }
    expect(checked, 'no coordinates were examined').toBeGreaterThan(0);
  });

  test('the map opens on the snapshot, not on the whole country', async ({
    page,
    request,
  }) => {
    const meta = await metadata(request);
    test.skip(meta.coverage?.isNational === true, 'national opens on NZ by design');

    await page.goto('/');
    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const m = (window as unknown as { __map?: any }).__map;
            return m?.isStyleLoaded?.() ? m.getZoom() : 0;
          }),
        { timeout: 30_000, message: 'the map never settled' },
      )
      /*
       * The national fallback sits around 4.5. Anything above 8 means the map
       * fitted the extract. This is the assertion that would have caught the
       * whole failure: with no display extent the map sat at 4.57 and drew
       * nothing, and every downstream test timed out waiting for tiles.
       */
      .toBeGreaterThan(8);
  });

  test('names still resolve without an enrichment pass', async ({ request }) => {
    /*
     * The fixture has no `link_names` rows. The display view falls back to the
     * name ingested onto the link itself, and that fallback is what keeps
     * every pre-enrichment snapshot — pilots, fixtures, anything older —
     * showing the names it always had.
     */
    const res = await request.get(`${API}/api/v1/links/search?name=&limit=50`);
    expect(res.ok()).toBeTruthy();
    const { results } = await res.json();
    expect(results.length).toBeGreaterThan(0);
    expect(
      results.some((r: { roadName: string | null }) => Boolean(r.roadName)),
      'no link in the snapshot has a name',
    ).toBe(true);
  });
});
