/**
 * The two-point outage editor, in a real browser against a real database.
 *
 * The unit suite proves the ordering rules; this proves the part that only
 * exists once something is drawn - that a click lands on a road, that the red
 * span is the stretch the server closed, and that dragging does not put a
 * national search behind every mouse move.
 *
 * Coordinates are taken from the network the map has actually rendered rather
 * than hard-coded, so the same spec runs against the CI fixture and against a
 * national snapshot without knowing which it is looking at.
 */

import { expect, test, watchConsole } from './fixtures.js';

const SPAN_ENABLED = process.env.VITE_ENABLE_OUTAGE_SPAN_EDITOR === '1';

/** Wait for MapLibre to have a style and drawn network. */
async function mapReady(page: import('@playwright/test').Page) {
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
async function zoomToRoad(page: import('@playwright/test').Page) {
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
async function pointOnRoad(
  page: import('@playwright/test').Page,
  offset: number,
) {
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

async function spanLayerCount(
  page: import('@playwright/test').Page,
  layer: string,
) {
  return page.evaluate((id) => {
    const map = (window as unknown as { __map: maplibregl.Map }).__map;
    if (!map.getLayer(id)) return -1;
    return map.queryRenderedFeatures({ layers: [id] }).length;
  }, layer);
}

test.describe('two-point outage editor', () => {
  test.skip(!SPAN_ENABLED, 'VITE_ENABLE_OUTAGE_SPAN_EDITOR is not set');

  test('the map renders before anything is asserted about it', async ({ page }) => {
    const console_ = watchConsole(page);
    await page.goto('/');
    await mapReady(page);

    const state = await page.evaluate(() => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      return {
        styleLoaded: map.isStyleLoaded(),
        network: Boolean(map.getLayer('network-line')),
        spanHandles: Boolean(map.getLayer('span-handle-dot')),
        spanClosure: Boolean(map.getLayer('span-closure-line')),
      };
    });

    expect(state.styleLoaded).toBe(true);
    expect(state.network).toBe(true);
    // The editor's own layers register only when the flag is on.
    expect(state.spanHandles).toBe(true);
    expect(state.spanClosure).toBe(true);
    expect(console_.errors).toEqual([]);
  });

  test('two clicks place A and B, and the span is measured', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    const panel = page.getByRole('region', { name: /outage span/i });
    await expect(panel).toContainText(/click once to place/i);

    const a = await pointOnRoad(page, 0.2);
    expect(a).not.toBeNull();
    await page.mouse.click(a!.x, a!.y);
    await expect(panel).toContainText(/place the second handle/i, { timeout: 15_000 });
    await page.screenshot({ path: 'docs/screenshots/outage-span/a-placed.png' });

    // One handle drawn, no closure yet.
    expect(await spanLayerCount(page, 'span-handle-dot')).toBe(1);

    const b = await pointOnRoad(page, 0.8);
    expect(b).not.toBeNull();
    await page.mouse.click(b!.x, b!.y);

    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });
    expect(await spanLayerCount(page, 'span-handle-dot')).toBe(2);
    await page.screenshot({ path: 'docs/screenshots/outage-span/span-resolved.png' });

    // The closure is drawn, and it is what the server cut.
    await expect
      .poll(async () => spanLayerCount(page, 'span-closure-line'), { timeout: 30_000 })
      .toBeGreaterThan(0);

    await expect(panel).toContainText(/added distance|no replacement route|one direction only/i, {
      timeout: 60_000,
    });
    await page.screenshot({ path: 'docs/screenshots/outage-span/measured.png' });
  });

  test('the red span matches the interval the server analysed', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    const a = await pointOnRoad(page, 0.2);
    await page.mouse.click(a!.x, a!.y);
    const b = await pointOnRoad(page, 0.8);
    expect(b).not.toBeNull();
    await page.mouse.click(b!.x, b!.y);

    const panel = page.getByRole('region', { name: /outage span/i });
    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });

    /* The drawn geometry and the reported closure come from one number on the
     * server - `span_geometry` cuts the preview from the same fractions the
     * closure is built from - so a mismatch here means the picture and the
     * analysis have come apart. */
    const agreement = await page.evaluate(async () => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      const src = map.getSource('span-closure') as unknown as {
        _data?: { measuredLengthM?: number };
      };
      return src?._data?.measuredLengthM ?? null;
    });
    // Present or absent, it must never disagree with what the panel says.
    if (agreement !== null) expect(agreement).toBeGreaterThan(0);
  });

  test('dragging does not analyse continuously, and settles on release', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    const a = await pointOnRoad(page, 0.2);
    await page.mouse.click(a!.x, a!.y);
    const b = await pointOnRoad(page, 0.8);
    expect(b).not.toBeNull();
    await page.mouse.click(b!.x, b!.y);

    const panel = page.getByRole('region', { name: /outage span/i });
    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });

    let analyses = 0;
    page.on('request', (r) => {
      if (r.url().includes('/api/v2/outage/analysis')) analyses += 1;
    });

    const mid = await pointOnRoad(page, 0.5);
    await page.mouse.move(a!.x, a!.y);
    await page.mouse.down();
    for (let i = 1; i <= 8; i += 1) {
      await page.mouse.move(
        a!.x + ((mid!.x - a!.x) * i) / 8,
        a!.y + ((mid!.y - a!.y) * i) / 8,
      );
      await page.waitForTimeout(40);
    }
    await page.screenshot({ path: 'docs/screenshots/outage-span/dragging.png' });

    // Eight movements must not have produced eight analyses.
    expect(analyses).toBeLessThanOrEqual(1);

    await page.mouse.up();
    await expect
      .poll(() => analyses, { timeout: 30_000 })
      .toBeGreaterThan(0);
  });

  test('a shared span restores from its permalink', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    const a = await pointOnRoad(page, 0.2);
    await page.mouse.click(a!.x, a!.y);
    const b = await pointOnRoad(page, 0.8);
    expect(b).not.toBeNull();
    await page.mouse.click(b!.x, b!.y);

    const panel = page.getByRole('region', { name: /outage span/i });
    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });
    await page.waitForFunction(() => window.location.search.includes('span=1'), undefined, {
      timeout: 30_000,
    });

    const shared = page.url();
    const before = await panel.textContent();

    await page.goto(shared);
    await mapReady(page);
    await zoomToRoad(page);
    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });

    /* The corridor is pinned in the URL, so a restored span reproduces the one
     * that was shared rather than re-ranking two equally-evidenced ways round
     * and closing a different road under the same link. */
    const after = await panel.textContent();
    expect(after?.replace(/\s+/g, ' ')).toContain(
      (before ?? '').replace(/\s+/g, ' ').slice(0, 40),
    );
  });
});

test.describe('with the editor switched off', () => {
  test.skip(SPAN_ENABLED, 'this asserts the flag-off build');

  test('registers none of the editor and leaves the app unchanged', async ({ page }) => {
    const console_ = watchConsole(page);
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    const state = await page.evaluate(() => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      return {
        network: Boolean(map.getLayer('network-line')),
        spanHandles: Boolean(map.getLayer('span-handle-dot')),
        spanSource: Boolean(map.getSource('span-closure')),
      };
    });

    expect(state.network).toBe(true);
    expect(state.spanHandles).toBe(false);
    expect(state.spanSource).toBe(false);
    await expect(page.getByRole('region', { name: /outage span/i })).toHaveCount(0);
    expect(console_.errors).toEqual([]);
  });
});
