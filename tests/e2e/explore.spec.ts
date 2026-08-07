/**
 * The Explore workflow, in a real browser against the real backend.
 *
 * Every test here corresponds to a defect that survived a green unit-test run,
 * because each one is an interaction between real API shapes and real browser
 * behaviour rather than a property of a pure function.
 */

import {
  exploreUrl,
  expect,
  panel,
  renderedCount,
  test,
  waitForResult,
  watchConsole,
} from './fixtures.js';

test.describe('Explore', () => {
  test('loads, renders the network and opens with no selection', async ({
    page,
  }) => {
    const console_ = watchConsole(page);
    await page.goto('/');

    await expect(page.locator('.brand-name')).toHaveText('NZ Critical Links');
    await expect(page.getByRole('heading', { name: /select a road/i })).toBeVisible();

    /*
     * The map must actually have drawn the network, not merely mounted.
     *
     * "At least one" rather than a density figure. This asserted more than a
     * hundred rendered features, which is a property of the Wellington pilot
     * and unreachable on the seven-link CI fixture — so it could only ever
     * fail there, which it silently did for as long as the browser job was
     * unable to run at all. What the gate should prove is that tiles are
     * requested, decoded and painted; how many roads a real city has is not
     * this test's business.
     */
    await expect
      .poll(() => renderedCount(page, 'network-line'), {
        timeout: 30_000,
        message: 'network tiles never rendered',
      })
      .toBeGreaterThan(0);

    /*
     * Attribution is a licence condition, not decoration. The AMDS credit is
     * always required; the LINZ credit only when a basemap is actually
     * configured — CI runs without a key, and the app must work without one.
     */
    await expect(page.locator('.map-attrib')).toContainText('AMDS');

    const hasBasemap = await page.evaluate(
      () => !!(window as any).__map?.getSource?.('linz'),
    );
    if (hasBasemap) {
      await expect(page.locator('.map-attrib')).toContainText('LINZ');
    }

    expect(console_.errors).toEqual([]);
  });

  test('shows a result for a two-way link', async ({ page, twoWayLink }) => {
    const console_ = watchConsole(page);
    await page.goto(exploreUrl(twoWayLink.amdsId, { focus: 'reverse' }));
    await waitForResult(page);

    await expect(page.locator('.insp-head h2')).toHaveText(twoWayLink.roadName);
    await expect(page.locator('.headline .val')).not.toBeEmpty();
    await expect(page.locator('.status-pill')).toBeVisible();

    expect(console_.errors).toEqual([]);
  });

  test('never claims traffic reroutes', async ({ page, twoWayLink }) => {
    /* The model computes one shortest path between two nodes. It does not know
     * which traffic used the link or how trips redistribute. */
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);

    const text = await panel(page).innerText();
    expect(text).not.toMatch(/traffic that used/i);
    expect(text).not.toMatch(/must travel/i);
    expect(text).toMatch(/modelled closure/i);
  });

  test('describes the closure as modelled on the map', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);

    await expect(page.locator('.map-badge')).toContainText(/modelled closure/i);
    /*
     * The legend names the scope actually closed, not a generic "segment".
     *
     * "AMDS source-feature", not "AMDS-feature": a source feature is a
     * data-maintenance unit, and the wording says so because the scope closes
     * every graph child of one, which is routinely much more road than the
     * segment the reader selected.
     */
    await expect(page.locator('.map-legend')).toContainText(
      /modelled amds source-feature closure/i,
    );
  });
});

test.describe('one-way links', () => {
  /*
   * The blank-panel bug: the URL defaults to reverse, a one-way link has no
   * reverse result, the Reverse tab is disabled, and nothing moved the focus.
   */

  test('normalises direction when loaded from a clean URL', async ({
    page,
    oneWayLink,
  }) => {
    await page.goto(exploreUrl(oneWayLink.amdsId));
    await waitForResult(page);

    /* A result is shown, not a blank panel. */
    await expect(page.locator('.headline')).toBeVisible();

    /* Exactly one enabled tab carries the keyboard tab stop. */
    const stops = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLButtonElement>('.dirtabs button')]
        .filter((b) => !b.disabled && b.tabIndex === 0)
        .map((b) => b.textContent),
    );
    expect(stops).toHaveLength(1);

    /* Compare requires both directions. */
    await expect(page.getByRole('tab', { name: 'Compare' })).toBeDisabled();
  });

  test('normalises a permalink that requests the unavailable direction', async ({
    page,
    oneWayLink,
  }) => {
    await page.goto(exploreUrl(oneWayLink.amdsId, { focus: 'reverse' }));
    await waitForResult(page);

    /* The selected tab is an enabled one... */
    const selected = page.locator('.dirtabs button[aria-selected="true"]');
    await expect(selected).toBeEnabled();

    /* ...the URL was rewritten to match... */
    await expect
      .poll(() => new URL(page.url()).searchParams.get('focus'))
      .not.toBe('reverse');

    /* ...and the change was explained rather than done silently. */
    await expect(page.locator('.notice--info')).toContainText(/one-way/i);
  });

  test('replaces rather than pushes when normalising', async ({
    page,
    oneWayLink,
  }) => {
    /* Normalisation is not a navigation. Back must leave the app's history
     * entry for this link, not step through the direction correction. */
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /select a road/i })).toBeVisible();

    const before = await page.evaluate(() => history.length);
    await page.goto(exploreUrl(oneWayLink.amdsId, { focus: 'reverse' }));
    await waitForResult(page);
    const after = await page.evaluate(() => history.length);

    /* One entry for the navigation itself; the correction must not add another. */
    expect(after - before).toBeLessThanOrEqual(1);
  });

  test('does not headline a routine one-way result as "road cut off"', async ({
    page,
    oneWayLink,
  }) => {
    /*
     * The API's own statusMeaning warns that DISCONNECTED on a one-way
     * carriageway is routine and does not mean the area is cut off. An earlier
     * build headlined it "Road cut off: 0 m" while traffic in fact got past
     * with +2.46 km.
     */
    await page.goto(exploreUrl(oneWayLink.amdsId));
    await waitForResult(page);

    const status = await page.locator('.status-pill').innerText();
    if (!/no replacement path/i.test(status)) test.skip();

    const label = await page.locator('.headline .lab').innerText();
    const value = await page.locator('.headline .val').innerText().catch(() => '');

    /* If nothing is stranded, the hero must not claim road is cut off. */
    const stranded = await page.locator('.stranded').count();
    if (/road cut off/i.test(label)) {
      expect(stranded, 'claimed road cut off with no stranded panel').toBeGreaterThan(0);
      expect(value).not.toMatch(/^0\s*m$/);
    }
  });

  test('shows no permanent skeletons once a result has settled', async ({
    page,
    oneWayLink,
  }) => {
    /* A DISCONNECTED result legitimately has null metrics. Rendering those as
     * shimmer bars says "still calculating" about numbers that never arrive. */
    await page.goto(exploreUrl(oneWayLink.amdsId));
    await waitForResult(page);
    await page.waitForTimeout(1500);

    await expect(panel(page).locator('.skeleton')).toHaveCount(0);
  });
});

test.describe('history', () => {
  test('Back returns to the previous road in one action', async ({
    page,
    twoWayLink,
    oneWayLink,
  }) => {
    /*
     * A map click selects an internal numeric id, which goes into the URL
     * before the result resolves it to an AMDS id. Keying history on the
     * identifier saw two different strings for one selection and pushed twice,
     * so Back returned to the same road in its numeric form.
     */
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    const first = await page.locator('.insp-head h2').innerText();

    await page.goto(exploreUrl(oneWayLink.amdsId));
    await waitForResult(page);
    const second = await page.locator('.insp-head h2').innerText();
    expect(second).not.toBe(first);

    await page.goBack();
    await waitForResult(page);
    await expect(page.locator('.insp-head h2')).toHaveText(first);
  });

  test('a scenario change does not add a history entry', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);

    const before = await page.evaluate(() => history.length);

    await page.locator('.scenario-summary-btn').click();
    await page.getByRole('button', { name: 'Heavy', exact: true }).click();
    await waitForResult(page);

    expect(await page.evaluate(() => history.length)).toBe(before);
  });
});

test.describe('snapshot permalinks', () => {
  test('flags a permalink made against a different snapshot', async ({
    page,
    twoWayLink,
  }) => {
    /* The API has no snapshot parameter yet, so such a link is recalculated
     * rather than reproduced. Presenting the recalculated figures as the saved
     * result would be dishonest. */
    await page.goto(
      exploreUrl(twoWayLink.amdsId, { snapshot: 'amds-wellington-1999-deadbeef' }),
    );
    await waitForResult(page);

    const notice = page.locator('.notice--warn').first();
    await expect(notice).toBeVisible();
    await expect(notice).toContainText(/not been reproduced/i);
  });

  test('shows no mismatch notice for a current permalink', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    await expect(page.locator('.notice--warn')).toHaveCount(0);
  });
});

test.describe('controls', () => {
  test('has no enabled control that does nothing', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(2000);

    /* Data-quality flags used to toggle its own pressed state while the map
     * consumed nothing. Anything not yet implemented must be disabled. */
    const quality = page.getByRole('button', { name: /data-quality/i });
    await expect(quality).toBeDisabled();
  });

  test('the info button opens About, not the scenario controls', async ({
    page,
  }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /about this analysis/i }).click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(/not traffic assignment/i);
    await expect(dialog).toContainText(/modelled/i);

    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  });

  test('the About dialog stays closed until asked for', async ({ page }) => {
    /* Styling the element `display: grid` overrode the UA's `display: none`
     * for a closed <dialog>, so it rendered permanently over the top bar. */
    await page.goto('/');
    await page.waitForTimeout(1200);
    await expect(page.locator('dialog.about')).toBeHidden();
  });

  test('the basemap control is named for what it shows', async ({ page }) => {
    await page.goto('/');
    /* Styled vector water, landcover and buildings — not imagery. */
    await expect(page.getByRole('button', { name: /basemap context/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /basemap imagery/i })).toHaveCount(0);
  });
});

test.describe('search', () => {
  test('results are reachable and selectable by keyboard', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto('/');

    /* "/" focuses search from anywhere — the convention for a map application. */
    await page.keyboard.press('/');
    await expect(page.getByRole('combobox')).toBeFocused();

    /* Search for a road the active snapshot actually contains, whichever
     * snapshot that is. */
    const term = twoWayLink.roadName.split(/\s+/)[0]!;
    await page.keyboard.type(term);
    await expect(page.getByRole('listbox')).toBeVisible();
    await expect(page.getByRole('option').first()).toBeVisible();

    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');

    await waitForResult(page);
    await expect(page.locator('.insp-head h2')).toContainText(term);
  });
});
