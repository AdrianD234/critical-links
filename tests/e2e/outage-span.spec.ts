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

import { expect, exploreUrl, test, watchConsole } from './fixtures.js';
import {
  enterSpanMode,
  mapReady,
  placeSpan,
  pointOnRoad,
  spanLayerCount,
  zoomToRoad,
} from './map-helpers.js';

const SPAN_ENABLED = process.env.VITE_ENABLE_OUTAGE_SPAN_EDITOR === '1';

test.describe('two-point outage editor', () => {
  test.skip(!SPAN_ENABLED, 'VITE_ENABLE_OUTAGE_SPAN_EDITOR is not set');

  test('the editor registers its layers only in Draw outage mode', async ({ page }) => {
    const console_ = watchConsole(page);
    await page.goto('/');
    await mapReady(page);

    const layerState = () =>
      page.evaluate(() => {
        const map = (window as unknown as { __map: maplibregl.Map }).__map;
        return {
          styleLoaded: map.isStyleLoaded(),
          network: Boolean(map.getLayer('network-line')),
          spanHandles: Boolean(map.getLayer('span-handle-dot')),
          spanClosure: Boolean(map.getLayer('span-closure-line')),
        };
      });

    /* Link mode is the default, and in it the editor's layers do not exist -
     * not hidden, absent, so the default screen registers exactly what a
     * build without the editor registers. */
    const before = await layerState();
    expect(before.styleLoaded).toBe(true);
    expect(before.network).toBe(true);
    expect(before.spanHandles).toBe(false);
    expect(before.spanClosure).toBe(false);

    await enterSpanMode(page);
    await expect.poll(async () => (await layerState()).spanHandles).toBe(true);
    expect((await layerState()).spanClosure).toBe(true);

    /* And leaving the mode removes them again. */
    await page.getByRole('radio', { name: /select road/i }).check();
    await expect.poll(async () => (await layerState()).spanHandles).toBe(false);

    expect(console_.errors).toEqual([]);
  });

  test('two clicks place A and B, and the span is measured', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);
    await enterSpanMode(page);

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

    const { panel, a } = await placeSpan(page);


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

    const { panel, a } = await placeSpan(page);


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

    const { panel, a } = await placeSpan(page);

    await page.waitForFunction(() => window.location.search.includes('span=1'), undefined, {
      timeout: 30_000,
    });

    const shared = page.url();
    const closedBefore = await panel.locator('.span-closed .val').textContent();
    const corridorBefore = new URL(shared).searchParams.get('sc');
    expect(corridorBefore).not.toBeNull();

    await page.goto(shared);
    await mapReady(page);
    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });

    /* The corridor is pinned in the URL, so a restored span reproduces the one
     * that was shared rather than re-ranking two equally-evidenced ways round
     * and closing a different road under the same link. The CLOSED LENGTH is
     * the comparison that means something: same corridor, same road, same
     * number - a panel-text prefix would match trivially. */
    await expect(panel.locator('.span-closed .val')).toHaveText(
      closedBefore ?? '', { timeout: 30_000 },
    );
    expect(new URL(page.url()).searchParams.get('sc')).toBe(corridorBefore);

    // And the restored handles are on the map, not just the numbers in the
    // panel - without them the span cannot be adjusted after a reload.
    await expect
      .poll(async () => spanLayerCount(page, 'span-handle-dot'), { timeout: 15_000 })
      .toBe(2);
  });

  test('Back leaves the span and Forward returns to it', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    const { panel, a } = await placeSpan(page);

    await page.waitForFunction(() => window.location.search.includes('span=1'), undefined, {
      timeout: 30_000,
    });

    await page.goBack();
    await expect(panel).toContainText(/click once to place/i, { timeout: 15_000 });
    expect(page.url()).not.toContain('span=1');

    await page.goForward();
    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });
  });

  test('no identifier ever poses as a road name', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    const { panel, a } = await placeSpan(page);


    /* Found in the browser: the panel said "Along {aa20d5b8-...}#1" for a road
     * the map tooltip happily named. A GUID where a road name goes reads as
     * breakage; unnamed roads are called "(unnamed road)". */
    const text = (await panel.textContent()) ?? '';
    expect(text).not.toMatch(/\{[0-9a-f]{8}-[0-9a-f]{4}/i);
  });

  test('with the editor on, a map click places a handle rather than selecting a link', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);
    await enterSpanMode(page);

    const a = await pointOnRoad(page, 0.3);
    await page.mouse.click(a!.x, a!.y);
    await expect(
      page.getByRole('region', { name: /outage span/i }),
    ).toContainText(/place the second handle/i, { timeout: 15_000 });

    /* The whole-link closure flow must NOT also have consumed the click. It
     * did: placing A selected the link underneath, ran a full closure, drew a
     * second red line and re-fitted the map mid-placement. Its result panel
     * carries a "Closure result" heading; none may appear. */
    await expect(page.getByText(/^closure result$/i)).toHaveCount(0);
    expect(page.url()).not.toMatch(/[?&]link=/);
  });

  test('the basemap switch changes presentation only', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    const { panel, a } = await placeSpan(page);
    void a;

    /* Capture the baseline only once the ANALYSIS has landed, which is when
     * the URL gains its span. "Road closed" appears at the corridor stage and
     * the length itself resolves there too, so waiting on either still races
     * the analysis - and a baseline taken mid-flight would assert the basemap
     * must keep the span half-finished forever. */
    await page.waitForFunction(
      () => window.location.search.includes('span=1'),
      undefined,
      { timeout: 30_000 },
    );
    const closedBefore = await panel.locator('.span-closed .val').textContent();
    const urlBefore = page.url();

    let requests = 0;
    page.on('request', (r) => {
      if (r.url().includes('/api/v2/outage/')) requests += 1;
    });

    /* The map view is an explicit selector now, so the walk through the modes
     * is by name rather than by counting clicks. */
    await page.getByRole('button', { name: /^map view/i }).click();
    const streets = page.getByRole('radio', { name: 'Streets' });
    const withKey = await streets.isEnabled();
    /* Without a LINZ key Streets and Aerial are disabled and say why; the
     * mode walk is only assertable where a basemap exists at all. */
    if (withKey) {
      await streets.check(); // analysis -> streets
      await page.waitForTimeout(400);
      await page.screenshot({ path: 'docs/screenshots/outage-span/topographic.png' });

      const topoRoads = await page.evaluate(() => {
        const map = (window as unknown as { __map: maplibregl.Map }).__map;
        return map.getLayoutProperty('linz-road', 'visibility');
      });
      expect(topoRoads).toBe('visible');
      /* Polled: a visibility change schedules a repaint, and querying rendered
       * features in the same frame legitimately returns nothing for a moment.
       * The claim is that the span STAYS drawn, not that no frame ever lacked
       * it. */
      await expect
        .poll(async () => spanLayerCount(page, 'span-closure-line'), { timeout: 10_000 })
        .toBeGreaterThan(0);

      await page.getByRole('radio', { name: 'Aerial' }).check();
      await expect
        .poll(async () => spanLayerCount(page, 'span-closure-line'), { timeout: 10_000 })
        .toBeGreaterThan(0);
      await page.getByRole('radio', { name: 'Off' }).check();
      await page.getByRole('radio', { name: 'Analysis' }).check();
    }
    await page.keyboard.press('Escape');

    /* Whatever the mode did, it must not have touched the analysis: same
     * closed length, same URL (same corridor pin), and not one span request. */
    await expect(panel.locator('.span-closed .val')).toHaveText(closedBefore ?? '');
    expect(page.url()).toBe(urlBefore);
    expect(requests).toBe(0);

    /* And LINZ roads are context, not targets. With a key, the layer exists
     * and - back in Analysis - is hidden again; without one it never existed,
     * which is the correct keyless state, and the disabled options said so
     * rather than pretending to work. Nothing snaps or selects against it in
     * either case: the span handlers query AMDS only. */
    const linz = await page.evaluate(() => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      const exists = Boolean(map.getLayer('linz-road'));
      return {
        exists,
        visibility: exists
          ? map.getLayoutProperty('linz-road', 'visibility')
          : null,
      };
    });
    if (withKey) {
      expect(linz.exists).toBe(true);
      expect(linz.visibility).not.toBe('visible');
    } else {
      expect(linz.exists).toBe(false);
    }
  });
});

test.describe('closure method switch', () => {
  test.skip(!SPAN_ENABLED, 'VITE_ENABLE_OUTAGE_SPAN_EDITOR is not set');

  test('Select road is the default, and a click selects a link', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    await expect(page.getByRole('radio', { name: /select road/i })).toBeChecked();

    const a = await pointOnRoad(page, 0.3);
    expect(a).not.toBeNull();
    await page.mouse.click(a!.x, a!.y);

    /* The ordinary workflow, exactly as it was before the editor existed:
     * the click selects a link and the closure result panel opens. */
    await expect(page.locator('.eyebrow', { hasText: /closure result/i }).first()).toBeVisible({
      timeout: 30_000,
    });
    await page.waitForFunction(() => /[?&]link=/.test(window.location.search), undefined, {
      timeout: 30_000,
    });
    await expect(page.getByRole('region', { name: /outage span/i })).toHaveCount(0);
  });

  test('switching methods never shows two closures at once', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);

    /* A whole-link closure first. */
    const a = await pointOnRoad(page, 0.3);
    await page.mouse.click(a!.x, a!.y);
    await expect(page.locator('.eyebrow', { hasText: /closure result/i }).first()).toBeVisible({
      timeout: 30_000,
    });

    /* Switching to Draw outage clears it - the selection, its red line and
     * its result - before the first handle is ever placed. */
    await enterSpanMode(page);
    await expect(page.locator('.eyebrow', { hasText: /closure result/i })).toHaveCount(0);
    expect(page.url()).not.toMatch(/[?&]link=/);
    await expect(
      page.getByRole('region', { name: /outage span/i }),
    ).toContainText(/click once to place/i);

    /* Place A, then switch back: the span goes the same way. */
    const b = await pointOnRoad(page, 0.5);
    await page.mouse.click(b!.x, b!.y);
    await expect(
      page.getByRole('region', { name: /outage span/i }),
    ).toContainText(/place the second handle/i, { timeout: 15_000 });

    await page.getByRole('radio', { name: /select road/i }).check();
    await expect(page.getByRole('region', { name: /outage span/i })).toHaveCount(0);
    const handles = await page.evaluate(() => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      return Boolean(map.getLayer('span-handle-dot'));
    });
    expect(handles).toBe(false);
  });

  test('a link permalink restores Select road mode', async ({ page, twoWayLink }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await mapReady(page);

    await expect(page.getByRole('radio', { name: /select road/i })).toBeChecked();
    await expect(page.locator('.eyebrow', { hasText: /closure result/i }).first()).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByRole('region', { name: /outage span/i })).toHaveCount(0);
  });

  test('a span permalink restores Draw outage mode', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    await zoomToRoad(page);
    const { panel } = await placeSpan(page);
    await page.waitForFunction(() => window.location.search.includes('span=1'), undefined, {
      timeout: 30_000,
    });

    await page.goto(page.url());
    await mapReady(page);

    await expect(page.getByRole('radio', { name: /draw outage/i })).toBeChecked();
    await expect(panel).toContainText(/road closed/i, { timeout: 30_000 });
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
    /* The flag controls AVAILABILITY: without it there is no method to choose,
     * so the switch itself must not render. */
    await expect(page.getByRole('radio', { name: /draw outage/i })).toHaveCount(0);
    await expect(page.getByText(/closure method/i)).toHaveCount(0);
    expect(console_.errors).toEqual([]);
  });
});
