/**
 * Map interaction helpers shared by the browser suites.
 *
 * Moved out of outage-span.spec.ts once the map-view suite needed the same
 * moves: waiting for a drawn network, zooming to a road the snapshot actually
 * contains, and placing an A/B span. Coordinates are taken from the network
 * the map has actually rendered rather than hard-coded, so the same helpers
 * run against the CI fixture and against a national snapshot without knowing
 * which they are looking at.
 */

import { expect } from './fixtures.js';

type Page = import('@playwright/test').Page;

/** Wait for MapLibre to have a style and drawn network. */
export async function mapReady(page: Page) {
  await page.waitForFunction(
    () => {
      const map = (window as unknown as { __map?: maplibregl.Map }).__map;
      return Boolean(map?.isStyleLoaded?.() && map.getLayer('network-line'));
    },
    undefined,
    { timeout: 60_000 },
  );
}

/**
 * Zoom to a road before clicking on one.
 *
 * At the national home extent one pixel covers hundreds of metres, so two
 * points eight kilometres apart project to about four pixels and a click
 * back-projects to somewhere no road is within the snap radius. The snap then
 * correctly reports finding nothing, and the test looks like a broken editor.
 *
 * A person zooms in to draw a span for exactly the same reason.
 */
export async function zoomToRoad(page: Page) {
  await page.waitForFunction(
    () => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      const feats = map.queryRenderedFeatures({ layers: ['network-line'] });
      if (!feats.length) return false;
      const g = feats[0].geometry;
      const coords =
        g.type === 'LineString'
          ? (g.coordinates as GeoJSON.Position[])
          : g.type === 'MultiLineString'
            ? (g.coordinates[0] as GeoJSON.Position[])
            : null;
      if (!coords?.length) return false;
      const mid = coords[Math.floor(coords.length / 2)] as [number, number];
      map.jumpTo({ center: mid, zoom: 15 });
      return true;
    },
    undefined,
    { timeout: 30_000 },
  );
  await page.waitForFunction(
    () => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      return map.getZoom() > 13 && map.queryRenderedFeatures({ layers: ['network-line'] }).length > 0;
    },
    undefined,
    { timeout: 30_000 },
  );
  await page.waitForTimeout(600);
}

/**
 * A point on a rendered road, in screen pixels.
 *
 * `offset` walks along the same feature so two calls give two points on one
 * road rather than two points on the same spot.
 */
export async function pointOnRoad(page: Page, offset: number) {
  return page.evaluate((frac) => {
    const map = (window as unknown as { __map: maplibregl.Map }).__map;
    const feats = map.queryRenderedFeatures({ layers: ['network-line'] });
    // The longest rendered line gives the most room for two distinct handles.
    let best: GeoJSON.Position[] | null = null;
    for (const f of feats) {
      const g = f.geometry;
      const coords =
        g.type === 'LineString'
          ? (g.coordinates as GeoJSON.Position[])
          : g.type === 'MultiLineString'
            ? (g.coordinates[0] as GeoJSON.Position[])
            : null;
      if (coords && (!best || coords.length > best.length)) best = coords;
    }
    if (!best || best.length < 2) return null;
    const rectFor = () => map.getCanvas().getBoundingClientRect();
    /* Walk outward from the requested position until a vertex lands inside the
     * visible canvas. A long road often runs off-screen, and the first choice
     * being off-canvas is ordinary rather than exceptional. */
    const order: number[] = [];
    const start = Math.min(best.length - 1, Math.max(0, Math.round((best.length - 1) * frac)));
    for (let d = 0; d < best.length; d += 1) {
      if (start + d < best.length) order.push(start + d);
      if (d > 0 && start - d >= 0) order.push(start - d);
    }
    for (const idx of order) {
      const q = map.project(best[idx] as [number, number]);
      const r = rectFor();
      const x = Math.round(r.left + q.x);
      const y = Math.round(r.top + q.y);
      // Inset a little so a point never lands on the canvas edge.
      if (x > r.left + 8 && x < r.right - 8 && y > r.top + 8 && y < r.bottom - 8) {
        return { x, y };
      }
    }
    return null;
  }, offset);
}

/** Switch the closure method to Draw outage. */
export async function enterSpanMode(page: Page) {
  const radio = page.getByRole('radio', { name: /draw outage/i });
  if (!(await radio.isChecked())) await radio.check();
}

/**
 * Place A and B and wait for the span to resolve.
 *
 * The B click retries once from a fresh projection. Between the two clicks the
 * map is still settling - tiles loading, glyphs arriving with a LINZ key - and
 * a projection taken a moment earlier can land a pixel or two off the road at
 * a zoom where that is further than the snap radius. A person just clicks
 * again; so does this.
 */
export async function placeSpan(page: Page) {
  /* The editor is a MODE now, defaulting to the ordinary link workflow, so
   * every span interaction starts by choosing it - exactly as a person does. */
  await enterSpanMode(page);
  const panel = page.getByRole('region', { name: /outage span/i });

  const a = await pointOnRoad(page, 0.2);
  expect(a).not.toBeNull();
  await page.mouse.click(a!.x, a!.y);
  await expect(panel).toContainText(/place the second handle/i, { timeout: 15_000 });

  const b = await pointOnRoad(page, 0.8);
  expect(b).not.toBeNull();
  await page.mouse.click(b!.x, b!.y);
  try {
    await expect(panel).toContainText(/road closed/i, { timeout: 10_000 });
  } catch {
    const again = await pointOnRoad(page, 0.75);
    expect(again).not.toBeNull();
    await page.mouse.click(again!.x, again!.y);
    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });
  }
  return { panel, a: a!, b: b! };
}

export async function spanLayerCount(page: Page, layer: string) {
  return page.evaluate((id) => {
    const map = (window as unknown as { __map: maplibregl.Map }).__map;
    if (!map.getLayer(id)) return -1;
    return map.queryRenderedFeatures({ layers: [id] }).length;
  }, layer);
}
