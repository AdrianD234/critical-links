/**
 * A visual sample of the engine's ACTUAL new default: Car · Distance · Road
 * segment.
 *
 * WHY THIS EXISTS
 *
 * The 26 cases a reviewer went through are all source-feature closures. That is
 * the advanced scope. None of them is a human sample of what an ordinary user
 * of this build will see when they click a road, and "the boundary measure
 * reduces to the endpoint measure when the closure has only its own two ends"
 * is a contract that holds in principle — this is the check that it holds in
 * practice, on real roads, with pictures.
 *
 * Twenty links, one from each of twenty latitude bands running the length of
 * the country, chosen deterministically so the sample is the same every run and
 * a change in it is a change in the data rather than in the dice. Sixteen local
 * roads and four state highways, 180 m to 3.2 km.
 *
 * The assertions are PROPERTIES, not pinned figures. A sample that asserted
 * "Longwood Road is +1.2 km" would fail on the next ingest for a reason that
 * has nothing to do with this promotion, and the properties are what a reviewer
 * is actually checking: that the headline is one the engine may emit, that a
 * diversion has both a figure and a route, that nothing claims a road is cut
 * off without the isolation block behind it, and that every case comes back
 * inside the interactive ceiling.
 *
 * The screenshots and the index they are listed in are the deliverable. They
 * are written, not compared: what is being reviewed is whether the panel reads
 * correctly, which no pixel comparison can answer.
 */

import { mkdirSync, writeFileSync } from 'node:fs';

import { API, expect, panel, test, waitForResult } from './fixtures.js';

const OUT = 'docs/screenshots/v2-promotion/production-sample';
const NATIONAL = 'amds-national-2026-07-28-5b359d84';

/** The interactive ceiling. Not the 1 s aspiration, which is not yet met. */
const CEILING_MS = 5000;

interface Case {
  linkId: number;
  name: string;
  lat: number;
  lon: number;
  stateHighway: boolean;
  lengthM: number;
}

/**
 * Twenty ordinary roads, one per latitude band, deterministic.
 *
 * Selected by `ntile(20) OVER (ORDER BY lat)` then `md5(link_id::text)` within
 * each band — ordering by anything meaningful inside a band (id, length, state
 * highway) biases the sample towards whatever was ingested first, which on this
 * data is the state-highway network.
 */
const CASES: Case[] = [
  { linkId: 261129, name: 'Longwood Road (Riverton Ward)', lat: -46.344, lon: 167.951, stateHighway: false, lengthM: 841.4 },
  { linkId: 45311, name: 'Chalmerston Road', lat: -45.855, lon: 170.449, stateHighway: false, lengthM: 234.9 },
  { linkId: 294861, name: 'De Bettencor Place', lat: -44.972, lon: 169.248, stateHighway: false, lengthM: 315.3 },
  { linkId: 275032, name: 'Buckleys Road', lat: -43.948, lon: 172.003, stateHighway: false, lengthM: 1448.7 },
  { linkId: 273953, name: 'High Street', lat: -43.804, lon: 171.937, stateHighway: false, lengthM: 197.2 },
  { linkId: 225475, name: 'Mount Thomas Road', lat: -43.291, lon: 172.501, stateHighway: false, lengthM: 182.2 },
  { linkId: 370140, name: 'State Highway 67', lat: -41.753, lon: 171.630, stateHighway: true, lengthM: 238.9 },
  { linkId: 11610, name: 'Makara Road', lat: -41.272, lon: 174.705, stateHighway: false, lengthM: 624.6 },
  { linkId: 81788, name: 'Carters Line', lat: -41.003, lon: 175.638, stateHighway: false, lengthM: 291.2 },
  { linkId: 346129, name: 'Ford Road', lat: -39.988, lon: 176.569, stateHighway: false, lengthM: 1402.3 },
  { linkId: 4612, name: 'State Highway 2', lat: -39.819, lon: 176.643, stateHighway: true, lengthM: 273.0 },
  { linkId: 348548, name: 'Puketitiri Road', lat: -39.462, lon: 176.789, stateHighway: false, lengthM: 3200.1 },
  { linkId: 1970, name: 'State Highway 35', lat: -38.469, lon: 178.268, stateHighway: true, lengthM: 1209.7 },
  { linkId: 41852, name: 'Sing Road', lat: -37.958, lon: 175.272, stateHighway: false, lengthM: 707.8 },
  { linkId: 20074, name: 'Pembroke Street', lat: -37.799, lon: 175.281, stateHighway: false, lengthM: 183.5 },
  { linkId: 303374, name: 'Tuakau Bridge-Port Waikato Road', lat: -37.385, lon: 174.739, stateHighway: false, lengthM: 1860.8 },
  { linkId: 221799, name: 'Bertram Road', lat: -36.951, lon: 175.169, stateHighway: false, lengthM: 198.8 },
  { linkId: 244750, name: 'Taupaki Road', lat: -36.797, lon: 174.574, stateHighway: false, lengthM: 180.5 },
  { linkId: 183276, name: 'Gumdigger Rise', lat: -36.607, lon: 174.672, stateHighway: false, lengthM: 248.2 },
  { linkId: 241361, name: 'State Highway 10', lat: -34.991, lon: 173.483, stateHighway: true, lengthM: 616.8 },
];

/** Every headline the boundary engine may emit. Nothing else may appear. */
const HEADLINES = [
  'Through movement diverts',
  'Through movement has no represented replacement',
  'No through movement identified',
  'Partial analysis',
  'Analysis unresolved',
];

/** The hero labels those headlines are rendered as. */
const HERO_LABELS = [
  /added distance — through movement/i,
  /road cut off/i,
  /one modelled through movement has no represented replacement/i,
  /no through movement identified/i,
  /partial analysis/i,
  /analysis unresolved/i,
];

function segmentUrl(linkId: number): string {
  const q = new URLSearchParams({
    link: String(linkId),
    metric: 'distance',
    vehicle: 'car',
    scope: 'segment',
    v: '2',
  });
  return `/?${q}`;
}

interface Observed extends Case {
  hero: string;
  headline: string;
  runtimeMs: number;
  cutOffClaimed: boolean;
}

const observed: Observed[] = [];
let onNational = false;

test.beforeAll(async ({ request }) => {
  const res = await request.get(`${API}/api/v1/network/metadata`);
  expect(res.ok(), 'the analysis API must be running').toBeTruthy();
  onNational = (await res.json()).snapshotId === NATIONAL;
  mkdirSync(OUT, { recursive: true });
});

test.beforeEach(() => {
  test.skip(
    !onNational,
    `this sample names links in ${NATIONAL}; the active snapshot is a ` +
      'different one, so there is nothing here to photograph',
  );
});

for (const c of CASES) {
  test(`${c.linkId} — ${c.name}`, async ({ page }) => {
    /* No V1 request from any of these, either. The dedicated no-fallback spec
     * forces failures; this one covers twenty ordinary successes, which is the
     * other half of the same claim. */
    const v1: string[] = [];
    page.on('request', (r) => {
      if (/\/api\/v1\/links\/.+\/detour/.test(r.url())) v1.push(r.url());
    });

    await page.goto(segmentUrl(c.linkId));
    await waitForResult(page);

    const hero = await page.locator('.headline .lab').innerText();
    expect(
      HERO_LABELS.some((p) => p.test(hero)),
      `unrecognised hero label: ${hero}`,
    ).toBe(true);

    const text = await panel(page).innerText();

    /*
     * "Road cut off" is only ever allowed with the undirected isolation result
     * behind it. This is the same rule as condition 5, applied to a sample
     * nobody chose for its outcome.
     */
    const cutOffClaimed = /road cut off/i.test(hero);
    if (cutOffClaimed) {
      expect(text).toMatch(/lose access in the represented physical-access graph/i);
      const value = await page.locator('.headline .val').innerText();
      expect(value).not.toMatch(/^0\s*(m|km)$/);
    }

    /* A diversion has to have both a figure and a route: a penalty with no
     * line is a number nobody can check, and a line with no penalty is a
     * picture with no finding. */
    if (/added distance/i.test(hero)) {
      await expect(page.locator('.headline .val')).not.toBeEmpty();
      expect(text).toMatch(/replacement route/i);
    }

    /* The scope actually closed, read back from the response. */
    await expect(page.locator('.map-badge')).toContainText(
      /modelled closure — segment/i,
    );

    /* The hedges survive at the default scope, not only at the advanced one. */
    expect(text).toMatch(/represented/i);

    expect(v1, 'an ordinary result called the retired engine').toEqual([]);

    /* Performance, measured on the request the panel is built from. */
    const res = await page.request.get(
      `${API}/api/v2/links/${c.linkId}/boundary-analysis` +
        `?scope=segment&metric=distance&vehicle=car&geometry=true` +
        `&corridor=true&isolation=true&allMovements=false`,
    );
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(HEADLINES).toContain(body.headline);
    expect(
      body.runtimeMs,
      `${c.name} took ${body.runtimeMs} ms, over the ${CEILING_MS} ms ceiling`,
    ).toBeLessThan(CEILING_MS);

    await page.screenshot({
      path: `${OUT}/${String(c.linkId).padStart(6, '0')}-${c.name
        .replace(/[^a-z0-9]+/gi, '-')
        .toLowerCase()}.png`,
    });

    observed.push({
      ...c,
      hero,
      headline: body.headline,
      runtimeMs: body.runtimeMs,
      cutOffClaimed,
    });
  });
}

test.afterAll(() => {
  if (!onNational || observed.length === 0) return;
  /*
   * An index, so the screenshots can be reviewed as a set rather than opened
   * one at a time. What a reader is checking is whether the headline matches
   * the picture, so both are in one row.
   */
  observed.sort((a, b) => b.lat - a.lat);
  const rows = observed
    .map(
      (o) =>
        `| ${o.linkId} | ${o.name} | ${o.stateHighway ? 'SH' : 'local'} | ` +
        `${o.lengthM} m | ${o.lat}, ${o.lon} | ${o.headline} | ${o.hero} | ` +
        `${o.runtimeMs} ms |`,
    )
    .join('\n');

  writeFileSync(
    `${OUT}/README.md`,
    `# Production sample — Car · Distance · Road segment\n\n` +
      `Twenty ordinary segment-scope closures, one per latitude band, on ` +
      `snapshot \`${NATIONAL}\`. Generated by ` +
      `\`tests/e2e/production-sample.spec.ts\`; the screenshots beside this ` +
      `file are the review material.\n\n` +
      `This is the sample of the engine's actual new default. The 26 cases ` +
      `reviewed before promotion were all source-feature closures, which is ` +
      `the advanced scope.\n\n` +
      `Ordered north to south.\n\n` +
      `| link | road | class | length | centroid | headline | hero | runtime |\n` +
      `| --- | --- | --- | --- | --- | --- | --- | --- |\n${rows}\n\n` +
      `Every case above: no request reached the retired detour engine, the ` +
      `map badge named a segment closure, and the panel retained the ` +
      `represented-network wording. Runtimes are the engine's own, under the ` +
      `5 s interactive ceiling.\n`,
    'utf8',
  );
});
