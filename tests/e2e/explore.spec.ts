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
  legacyUrl,
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
     * The legend names the scope actually closed, read back from the response
     * rather than from the control. The default scope is the selected segment,
     * so that is what it must say — labelling a segment closure as a
     * source-feature one would claim more road was removed than was.
     */
    await expect(page.locator('.map-legend')).toContainText(
      /modelled segment closure/i,
    );
  });

  /*
   * The advanced scope still names itself, and still warns.
   *
   * "AMDS source-feature", not "AMDS-feature": a source feature is a
   * data-maintenance unit, and the wording says so because the scope closes
   * every graph child of one, which is routinely much more road than the
   * segment the reader selected. Demoting it from the default must not quietly
   * demote the warning that goes with it.
   */
  test('names the advanced scope, and warns about it', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId, { scope: 'amds-feature' }));
    await waitForResult(page);

    await expect(page.locator('.map-legend')).toContainText(
      /modelled amds source-feature closure/i,
    );
  });
});

test.describe('direction scope', () => {
  /*
   * A directed closure withdraws ONE direction of travel and has to name which.
   *
   * The direction tabs went away with the measure that needed them — they
   * switched between two already-computed halves of an endpoint result, and
   * this engine measures a crossing, where the other direction is a different
   * crossing rather than the same one reversed. But `direction` scope still
   * needs a control saying which traversal to withdraw, and without one a
   * reader who selects that scope is stuck on whichever traversal the URL
   * happened to carry.
   */
  test('offers a direction to withdraw, and only under that scope', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    await page.locator('.scenario-summary-btn').click();

    /* Not under the default scope: a control that changes nothing implies a
     * choice that matters. */
    await expect(page.getByRole('group', { name: 'Direction' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Direction', exact: true }).click();
    await waitForResult(page);

    const group = page.getByRole('group', { name: 'Direction' });
    await expect(group).toBeVisible();
    await expect(group.getByRole('button', { name: 'Forward' })).toBeVisible();
    await expect(group.getByRole('button', { name: 'Reverse' })).toBeVisible();
  });

  test('the chosen direction reaches the URL', async ({ page, twoWayLink }) => {
    /* It is part of what was analysed, so a permalink that omitted it would
     * restore a different closure from the one on screen. */
    await page.goto(exploreUrl(twoWayLink.amdsId, { scope: 'direction' }));
    await waitForResult(page);
    await page.locator('.scenario-summary-btn').click();

    await page
      .getByRole('group', { name: 'Direction' })
      .getByRole('button', { name: 'Forward' })
      .click();
    await waitForResult(page);

    await expect
      .poll(() => new URL(page.url()).searchParams.get('focus'))
      .toBe('forward');
  });
});

test.describe('links made before the promotion', () => {
  /*
   * The migration policy, in the browser.
   *
   * A link with no semantics marker was written by a build whose default scope
   * was the whole AMDS source feature, measured between that feature's own two
   * endpoints. Honouring the scope as written would answer under this engine's
   * measure while looking like the saved result, so the scope is moved to the
   * default and the change is disclosed. The disclosure is the entire policy:
   * without it this is the silent reinterpretation it exists to prevent.
   */
  test('migrates the scope and says so', async ({ page, twoWayLink }) => {
    await page.goto(legacyUrl(twoWayLink.amdsId));
    await waitForResult(page);

    const notice = page.locator('.notice--info').first();
    await expect(notice).toBeVisible();
    await expect(notice).toContainText(/before the current engine/i);
    await expect(notice).toContainText(/amds source-feature/i);
    await expect(notice).toContainText(/segment/i);

    /* And the URL now describes what is actually on screen. */
    await expect
      .poll(() => new URL(page.url()).searchParams.get('scope'))
      .toBe('segment');
    await expect
      .poll(() => new URL(page.url()).searchParams.get('v'))
      .toBe('2');
  });

  test('offers the original scope back rather than stranding it', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(legacyUrl(twoWayLink.amdsId));
    await waitForResult(page);

    await page
      .getByRole('button', { name: /close the modelled amds source-feature/i })
      .click();
    await waitForResult(page);

    await expect
      .poll(() => new URL(page.url()).searchParams.get('scope'))
      .toBe('amds-feature');
    /* The notice is spent once acted on. One that survived its own resolution
     * would keep describing a migration that has been undone. */
    await expect(page.locator('.notice--info')).toHaveCount(0);
  });

  test('does not show the notice for a current link', async ({
    page,
    twoWayLink,
  }) => {
    /* A notice that fires when nothing changed teaches the reader to dismiss
     * the one that fires when something did. */
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    await expect(page.locator('.notice--info')).toHaveCount(0);
  });
});

test.describe('one-way links', () => {
  /*
   * A one-way carriageway was where the retired engine went wrong most
   * visibly. It asked for a path from a link's end back to its own start,
   * which a one-way link does not have and never did, called the absence
   * DISCONNECTED, and headlined it "Road cut off: 0 m" while traffic in fact
   * got past with +2.46 km.
   *
   * That question is not asked any more — this engine measures a trip ACROSS
   * the closure, and a one-way link has such a trip like any other — so the
   * tests are about the finding, not about a direction control. The direction
   * tabs went with the measure that needed them: "the other direction" here is
   * a different crossing rather than the same one reversed, so there is
   * nothing to put beside it.
   */

  test('produces a result rather than a blank panel', async ({
    page,
    oneWayLink,
  }) => {
    await page.goto(exploreUrl(oneWayLink.amdsId));
    await waitForResult(page);

    await expect(page.locator('.headline')).toBeVisible();
    await expect(page.locator('.headline .lab')).not.toBeEmpty();
    await expect(page.locator('.status-pill')).toBeVisible();
  });

  test('never headlines "road cut off" without something cut off', async ({
    page,
    oneWayLink,
  }) => {
    await page.goto(exploreUrl(oneWayLink.amdsId));
    await waitForResult(page);

    const label = await page.locator('.headline .lab').innerText();
    if (!/road cut off/i.test(label)) return;

    /*
     * The claim is only allowed when the undirected isolation result supports
     * it. It is computed on its own graph and does not depend on any route
     * search, so the panel block that reports it is the evidence: the hero may
     * not say more than that block does.
     */
    const value = await page.locator('.headline .val').innerText();
    expect(value).not.toMatch(/^0\s*(m|km)$/);
    await expect(panel(page)).toContainText(
      /lose access in the represented physical-access graph/i,
    );
  });

  test('never turns a bounded search into a finding about the road', async ({
    page,
    oneWayLink,
  }) => {
    /*
     * "Partial analysis" and "Analysis unresolved" are statements about the
     * search. If either is the headline, nothing beside it may read as a
     * settled measurement of this closure.
     */
    await page.goto(exploreUrl(oneWayLink.amdsId));
    await waitForResult(page);

    const label = await page.locator('.headline .lab').innerText();
    if (!/partial analysis|analysis unresolved/i.test(label)) return;

    await expect(page.locator('.headline .val')).toHaveCount(0);
  });

  test('shows no permanent skeletons once a result has settled', async ({
    page,
    oneWayLink,
  }) => {
    /* A result with no replacement legitimately has null metrics. Rendering
     * those as shimmer bars says "still calculating" about numbers that never
     * arrive. */
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

/** The snapshot-mismatch notice, told apart from every other warning. */
function mismatchNotice(page: import('@playwright/test').Page) {
  return page
    .locator('.notice--warn')
    .filter({ hasText: /snapshot/i })
    .filter({ hasText: /reproduced/i });
}

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

    const notice = mismatchNotice(page);
    await expect(notice).toBeVisible();
    await expect(notice).toContainText(/not been reproduced/i);
  });

  test('shows no mismatch notice for a current permalink', async ({
    page,
    twoWayLink,
  }) => {
    await page.goto(exploreUrl(twoWayLink.amdsId));
    await waitForResult(page);
    /*
     * The mismatch notice specifically, not every warning on the panel.
     *
     * This asserted that NO `.notice--warn` was present, which was true when
     * the only warning a result could carry was this one. The panel now warns
     * about several things a reader has to meet before the figures — a
     * source-feature closure's size, a low topology confidence, a bounded
     * search, a withheld route — so counting them all would fail on a result
     * that is behaving correctly, and would have to be deleted rather than
     * fixed the first time it did.
     */
    await expect(mismatchNotice(page)).toHaveCount(0);
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
    /* The control is now a presentation-mode cycle, and its accessible name
     * states the CURRENT mode - "Basemap: analysis", "Basemap: topographic",
     * "Basemap: off" - or, with no LINZ key, why it is disabled. What it must
     * never say is "imagery": every mode is styled vector data, and calling it
     * imagery would promise photography nothing here provides. */
    const basemap = page.getByRole('button', { name: /^basemap/i });
    await expect(basemap).toBeVisible();
    await expect(basemap).toHaveAccessibleName(
      /basemap: (analysis|topographic|off)|basemap unavailable/i,
    );
    await expect(page.getByRole('button', { name: /imagery/i })).toHaveCount(0);
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
