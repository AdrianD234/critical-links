/**
 * The explicit map-view selector, in a real browser.
 *
 * The unit suite states which layers each mode shows; this proves the part
 * that only exists on screen — that the selector opens and closes properly,
 * that the keyboard drives it, that the chosen ground survives a reload, and
 * that no choice of ground ever touches the analysis or costs a request.
 *
 * Every LINZ-dependent assertion branches on whether this run has a key,
 * because CI deliberately has none: the keyless arm asserts the disabled
 * options and their reason, the keyed arm the modes themselves.
 */

import { expect, exploreUrl, test, waitForResult, watchConsole } from './fixtures.js';
import { mapReady, zoomToRoad } from './map-helpers.js';

const trigger = (page: import('@playwright/test').Page) =>
  page.getByRole('button', { name: /^map view/i });

/** Whether this run's build actually has a LINZ key, read off the live map. */
async function linzConfigured(page: import('@playwright/test').Page) {
  return page.evaluate(
    () => Boolean((window as unknown as { __map?: maplibregl.Map }).__map?.getSource?.('linz')),
  );
}

async function layerVisibility(page: import('@playwright/test').Page, id: string) {
  return page.evaluate((layer) => {
    const map = (window as unknown as { __map: maplibregl.Map }).__map;
    if (!map.getLayer(layer)) return 'absent';
    return String(map.getLayoutProperty(layer, 'visibility') ?? 'visible');
  }, id);
}

test.describe('map view selector', () => {
  test('offers all four choices, Analysis checked by default, and says what LINZ is for', async ({
    page,
  }) => {
    const console_ = watchConsole(page);
    await page.goto('/');
    await mapReady(page);

    await trigger(page).click();
    for (const name of ['Analysis', 'Streets', 'Aerial', 'Off']) {
      await expect(page.getByRole('radio', { name })).toBeVisible();
    }
    await expect(page.getByRole('radio', { name: 'Analysis' })).toBeChecked();

    /* The context disclaimer is part of the selector, not a tooltip. */
    await expect(
      page.getByText(
        /LINZ context only\. Routing and closure analysis use the AMDS represented network\./,
      ),
    ).toBeVisible();

    /* Escape closes and hands focus back to the trigger. */
    await page.keyboard.press('Escape');
    await expect(page.getByRole('radio', { name: 'Analysis' })).toHaveCount(0);
    await expect(trigger(page)).toBeFocused();
    await expect(trigger(page)).toHaveAttribute('aria-expanded', 'false');

    expect(console_.errors).toEqual([]);
  });

  test('each mode shows its own ground', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    test.skip(!(await linzConfigured(page)), 'needs a LINZ key; the keyless arm is below');

    await trigger(page).click();

    /* Analysis: quiet vector context, no LINZ streets, no photography. */
    expect(await layerVisibility(page, 'linz-water')).toBe('visible');
    expect(await layerVisibility(page, 'linz-road')).toBe('none');
    expect(await layerVisibility(page, 'linz-aerial')).toBe('none');

    await page.getByRole('radio', { name: 'Streets' }).check();
    expect(await layerVisibility(page, 'linz-road')).toBe('visible');
    expect(await layerVisibility(page, 'linz-road-label')).toBe('visible');
    expect(await layerVisibility(page, 'linz-aerial')).toBe('none');

    await page.getByRole('radio', { name: 'Aerial' }).check();
    expect(await layerVisibility(page, 'linz-aerial')).toBe('visible');
    /* The photography already shows the roads; the vector lines stay out of
     * its way. Names remain for orientation, and the vector fills would be
     * mud over imagery. */
    expect(await layerVisibility(page, 'linz-road')).toBe('none');
    expect(await layerVisibility(page, 'linz-road-label')).toBe('visible');
    expect(await layerVisibility(page, 'linz-water')).toBe('none');
    /* And the photography is genuinely rendering, not just switched on. */
    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const map = (window as unknown as { __map: maplibregl.Map }).__map;
            return map.areTilesLoaded();
          }),
        { timeout: 20_000 },
      )
      .toBe(true);

    await page.getByRole('radio', { name: 'Off' }).check();
    for (const id of ['linz-aerial', 'linz-water', 'linz-landcover', 'linz-road', 'linz-place-label']) {
      expect(await layerVisibility(page, id), `${id} in Off`).toBe('none');
    }
    /* The analytical network is not the map view's to hide. */
    expect(await layerVisibility(page, 'network-line')).toBe('visible');

    await page.getByRole('radio', { name: 'Analysis' }).check();
    expect(await layerVisibility(page, 'linz-water')).toBe('visible');
    expect(await layerVisibility(page, 'linz-aerial')).toBe('none');
  });

  test('the chosen view survives a reload', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);

    /* Off needs no LINZ key, so this asserts persistence in every build. */
    await trigger(page).click();
    await page.getByRole('radio', { name: 'Off' }).check();
    await expect(trigger(page)).toHaveAccessibleName('Map view: Off');

    await page.reload();
    await mapReady(page);
    await expect(trigger(page)).toHaveAccessibleName('Map view: Off');
    await trigger(page).click();
    await expect(page.getByRole('radio', { name: 'Off' })).toBeChecked();
    expect(await layerVisibility(page, 'linz-place-label')).not.toBe('visible');
  });

  test('an invalid stored view falls back to Analysis', async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => localStorage.setItem('nzcl.mapView', 'satellite'));
    await page.reload();
    await mapReady(page);
    await expect(trigger(page)).toHaveAccessibleName('Map view: Analysis');
  });

  test('switching views costs zero analytical requests and changes no result', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    await mapReady(page);

    const headlineBefore = await page.locator('.headline').textContent();
    const urlBefore = page.url();

    /* Every backend endpoint is under /api/; tiles for the analytical network
     * are /tiles/. Neither may be touched by a presentation change — the only
     * traffic a mode switch may cause is LINZ's own, to basemaps.linz.govt.nz,
     * whose paths also contain /tiles/ and so are excluded by host. */
    let apiRequests = 0;
    page.on('request', (r) => {
      const u = new URL(r.url());
      if (u.hostname.endsWith('linz.govt.nz')) return;
      if (u.pathname.includes('/api/') || u.pathname.startsWith('/tiles/')) {
        apiRequests += 1;
      }
    });

    await trigger(page).click();
    const withKey = await page.getByRole('radio', { name: 'Streets' }).isEnabled();
    const walk = withKey ? ['Streets', 'Aerial', 'Off', 'Analysis'] : ['Off', 'Analysis'];
    for (const name of walk) {
      await page.getByRole('radio', { name }).check();
      await page.waitForTimeout(250);
    }
    await page.keyboard.press('Escape');

    expect(apiRequests).toBe(0);
    await expect(page.locator('.headline')).toHaveText(headlineBefore ?? '');
    expect(page.url()).toBe(urlBefore);
  });

  test('the keyboard alone drives it: open, arrows, Escape, focus restored', async ({
    page,
  }) => {
    await page.goto('/');
    await mapReady(page);

    await trigger(page).focus();
    await page.keyboard.press('Enter');
    /* Focus lands on the current choice, so the arrows work at once. */
    await expect(page.getByRole('radio', { name: 'Analysis' })).toBeFocused();

    /* Arrow selection follows the ordinary radio model; disabled options are
     * skipped natively, so this holds with and without a LINZ key. */
    await page.keyboard.press('ArrowDown');
    const checked = page.locator('input[name="map-view"]:checked');
    await expect(checked).not.toHaveValue('analysis');
    const after = await checked.inputValue();
    const withKey = await page.getByRole('radio', { name: 'Streets' }).isEnabled();
    expect(after).toBe(withKey ? 'topo' : 'off');

    await page.keyboard.press('Escape');
    await expect(page.getByRole('radio', { name: 'Analysis' })).toHaveCount(0);
    await expect(trigger(page)).toBeFocused();
    /* The new mode was kept, and is announced on the trigger. */
    await expect(trigger(page)).toHaveAccessibleName(
      withKey ? 'Map view: Streets' : 'Map view: Off',
    );
  });

  test('LINZ streets cannot be selected as a road', async ({ page }) => {
    await page.goto('/');
    await mapReady(page);
    test.skip(!(await linzConfigured(page)), 'needs LINZ streets to try to select');

    /* At the national extent barely any LINZ street renders; the candidate
     * pixel has to be hunted at street level, where LINZ draws service lanes
     * and private ways the AMDS network does not represent. */
    await zoomToRoad(page);

    await trigger(page).click();
    await page.getByRole('radio', { name: 'Streets' }).check();
    await page.keyboard.press('Escape');
    await page.waitForTimeout(800);

    /* The structural claim: every layer-scoped map listener — click, hover,
     * anything — is registered against the app's own layers, never a LINZ
     * one. This is what makes LINZ streets unselectable by construction,
     * whatever pixel they happen to share with the network. */
    const interactive = await page.evaluate(() => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      const delegated =
        (map as unknown as {
          _delegatedListeners?: Record<
            string,
            Array<{ layer?: string; layers?: string[] }>
          >;
        })._delegatedListeners ?? {};
      const layers = new Set<string>();
      for (const arr of Object.values(delegated)) {
        for (const d of arr) {
          if (d.layer) layers.add(d.layer);
          for (const l of d.layers ?? []) layers.add(l);
        }
      }
      return [...layers];
    });
    expect(interactive.length).toBeGreaterThan(0);
    for (const layer of interactive) {
      expect(layer, `${layer} must not be an interactive LINZ layer`).not.toMatch(
        /^linz/,
      );
    }

    /* And the empirical half where the ground allows it: a pixel with a LINZ
     * street but no AMDS link within the forgiving 14px hit line must click
     * to nothing. Dense snapshots can cover every LINZ street, so finding no
     * such pixel just means this arm has nothing to add. */
    const spot = await page.evaluate(() => {
      const map = (window as unknown as { __map: maplibregl.Map }).__map;
      const rect = map.getCanvas().getBoundingClientRect();
      for (let gx = 16; gx < rect.width - 16; gx += 20) {
        for (let gy = 16; gy < rect.height - 16; gy += 20) {
          const linz = map.queryRenderedFeatures([gx, gy] as never, {
            layers: ['linz-road'],
          });
          if (!linz.length) continue;
          const hit = map.queryRenderedFeatures(
            [
              [gx - 10, gy - 10],
              [gx + 10, gy + 10],
            ] as never,
            { layers: ['network-hit'] },
          );
          if (!hit.length) return { x: rect.left + gx, y: rect.top + gy };
        }
      }
      return null;
    });
    if (spot) {
      await page.mouse.click(spot.x, spot.y);
      await page.waitForTimeout(800);
      expect(page.url()).not.toMatch(/[?&]link=/);
      await expect(page.getByText(/^closure result$/i)).toHaveCount(0);
    }
  });
});

test.describe('map view without a LINZ key', () => {
  test('Streets and Aerial are disabled with the reason; Analysis and Off still work', async ({
    page,
  }) => {
    await page.goto('/');
    await mapReady(page);
    test.skip(
      await linzConfigured(page),
      'this build has a LINZ key; CI asserts the keyless arm',
    );

    /* The whole control never disables — Off needs no key at all. */
    await expect(trigger(page)).toBeEnabled();
    await trigger(page).click();

    await expect(page.getByRole('radio', { name: 'Streets' })).toBeDisabled();
    await expect(page.getByRole('radio', { name: 'Aerial' })).toBeDisabled();
    await expect(page.getByRole('radio', { name: 'Analysis' })).toBeEnabled();
    await expect(page.getByRole('radio', { name: 'Off' })).toBeEnabled();
    await expect(
      page.getByText('LINZ Basemaps key not configured.', { exact: true }),
    ).toBeVisible();

    await page.getByRole('radio', { name: 'Off' }).check();
    await expect(page.getByRole('radio', { name: 'Off' })).toBeChecked();
    await expect(trigger(page)).toHaveAccessibleName('Map view: Off');
  });
});
