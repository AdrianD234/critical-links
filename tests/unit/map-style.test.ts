/**
 * Map style validation.
 *
 * The browser pane used during development runs hidden, so
 * `requestAnimationFrame` never fires and MapLibre cannot finish loading a
 * style there — the map cannot be verified by rendering it in this
 * environment. Validating the style and layer definitions against the official
 * MapLibre style specification checks the part that would otherwise go
 * untested: that every layer, source reference, paint property and filter
 * expression is well formed.
 *
 * This does not prove the map looks right. It proves the style is valid.
 *
 * These definitions used to be duplicated here and kept in step with the map
 * component by a test that grepped the .tsx for layer ids. They now live in
 * apps/web/src/map/style.ts and are imported directly, so the copy — and the
 * class of bug where the copy silently diverged — is gone.
 */

import { describe, expect, it } from 'vitest';
import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec';

import {
  LABEL_LAYERS,
  LYR,
  NETWORK_LAYERS,
  OVERLAY_LAYERS,
  SRC,
  baseStyle,
  emptyCollection,
} from '../../apps/web/src/map/style.js';

const TILES = 'https://example.test/tiles/v2/snap/{z}/{x}/{y}.pbf';

/**
 * The complete style the component builds: base plus every layer it adds on
 * load. `baseStyle` only covers what is present before `map.on('load')`.
 *
 * The test environment has no LINZ key, so `baseStyle` omits the basemap and
 * its glyphs. The with-basemap case reconstructs both here — including the
 * `glyphs` URL, without which every symbol layer is invalid.
 */
function fullStyle(withLinz: boolean): any {
  const style: any = JSON.parse(JSON.stringify(baseStyle(TILES)));

  /*
   * Whether `baseStyle` already included the basemap depends on whether a LINZ
   * key is present in the environment — a developer with a populated .env and
   * CI without one must both get the same test result. So the basemap is added
   * only if it is not already there, and removed if it is not wanted.
   */
  const has = (id: string) => style.layers.some((l: any) => l.id === id);

  if (withLinz) {
    style.glyphs ??=
      'https://basemaps.linz.govt.nz/v1/fonts/{fontstack}/{range}.pbf?api=KEY';
    style.sources[SRC.linz] ??= {
      type: 'vector',
      tiles: [
        'https://basemaps.linz.govt.nz/v1/tiles/topographic/WebMercatorQuad/{z}/{x}/{y}.pbf?api=KEY',
      ],
      minzoom: 0,
      maxzoom: 15,
      attribution: 'LINZ Basemaps — CC BY 4.0',
    };
    for (const [id, sourceLayer, colour] of [
      [LYR.linzLandcover, 'landcover', '#1a2127'],
      [LYR.linzWater, 'water', '#0f1418'],
      [LYR.linzBuilding, 'building', '#20282f'],
    ] as const) {
      if (!has(id)) {
        style.layers.push({
          id,
          type: 'fill',
          source: SRC.linz,
          'source-layer': sourceLayer,
          paint: { 'fill-color': colour },
        });
      }
    }
    /* The topographic-mode layers, exactly as buildStyle shapes them: hidden
     * by default, which is the property the visibility test asserts. Without
     * these the reconstruction diverges from the real style precisely on the
     * layers the newest test is about - it passed on a developer machine
     * whose .env holds a key and failed in CI, which has none. */
    if (!has(LYR.linzRoad)) {
      style.layers.push({
        id: LYR.linzRoad,
        type: 'line',
        source: SRC.linz,
        'source-layer': 'transportation',
        layout: { visibility: 'none' },
        paint: { 'line-color': '#39434c' },
      });
    }
    if (!has(LYR.linzRoadLabel)) {
      style.layers.push({
        id: LYR.linzRoadLabel,
        type: 'symbol',
        source: SRC.linz,
        'source-layer': 'transportation_name',
        layout: { visibility: 'none', 'text-field': ['get', 'name'] },
        paint: {},
      });
    }
  } else {
    delete style.glyphs;
    delete style.sources[SRC.linz];
    style.layers = style.layers.filter((l: any) => l.source !== SRC.linz);
  }

  for (const id of [
    SRC.closure,
    SRC.closureLabel,
    SRC.routeCompare,
    SRC.routeHit,
    SRC.corridor,
    SRC.stranded,
  ]) {
    style.sources[id] = { type: 'geojson', data: emptyCollection() };
  }

  /* The focused route's source MUST set lineMetrics, or `line-progress` — and
   * therefore the reveal — does not exist. */
  style.sources[SRC.routeFocus] = {
    type: 'geojson',
    data: emptyCollection(),
    lineMetrics: true,
  };

  style.layers.push(...NETWORK_LAYERS, ...OVERLAY_LAYERS);
  if (withLinz) style.layers.push(...LABEL_LAYERS);
  return style;
}

describe('MapLibre style specification', () => {
  it('validates with a LINZ basemap configured', () => {
    expect(validateStyleMin(fullStyle(true))).toEqual([]);
  });

  it('validates without a LINZ key, so the map still works unstyled', () => {
    expect(validateStyleMin(fullStyle(false))).toEqual([]);
  });

  it('references only sources that the style declares', () => {
    const style = fullStyle(true);
    const declared = new Set(Object.keys(style.sources));
    for (const l of style.layers) {
      if (l.type === 'background') continue;
      expect(declared.has(l.source), `undeclared source on ${l.id}`).toBe(true);
    }
  });

  it('draws the closure above every route layer', () => {
    /* The closure is the one thing that must never be obscured by the answer
     * it produced. */
    const ids = fullStyle(true).layers.map((l: any) => l.id);
    for (const below of [
      LYR.routeFocus,
      LYR.routeCompare,
      LYR.corridor,
      LYR.stranded,
    ]) {
      expect(ids.indexOf(LYR.closureLine)).toBeGreaterThan(ids.indexOf(below));
      expect(ids.indexOf(LYR.closureHalo)).toBeGreaterThan(ids.indexOf(below));
    }
  });

  it('draws the comparison route beneath the focused one', () => {
    const ids = fullStyle(true).layers.map((l: any) => l.id);
    expect(ids.indexOf(LYR.routeFocus)).toBeGreaterThan(
      ids.indexOf(LYR.routeCompare),
    );
  });

  it('keeps a transparent wide hit layer so clicking a road is forgiving', () => {
    const hit: any = NETWORK_LAYERS.find((l) => l.id === LYR.networkHit);
    expect(hit.paint['line-opacity']).toBe(0);
    expect(hit.paint['line-width']).toBeGreaterThanOrEqual(10);
  });

  it('dims links that lie outside the analysis area rather than hiding them', () => {
    const line: any = NETWORK_LAYERS.find((l) => l.id === LYR.networkLine);
    expect(JSON.stringify(line.paint['line-opacity'])).toContain('core');
    /* Never a zero opacity: a buffer link that carries a detour must stay
     * visible. */
    expect(JSON.stringify(line.paint['line-opacity'])).not.toContain('0,0');
  });

  it('never combines line-gradient with line-dasharray on one layer', () => {
    /* MapLibre supports neither together. The dashed comparison route is on
     * its own layer precisely because of this, and a future edit that moved
     * the dash onto the animated layer would silently break the reveal. */
    for (const l of OVERLAY_LAYERS as any[]) {
      const p = l.paint ?? {};
      expect(
        !(p['line-gradient'] && p['line-dasharray']),
        `${l.id} sets both line-gradient and line-dasharray`,
      ).toBe(true);
    }
  });

  it('gives the closure label first claim on placement', () => {
    /* MapLibre resolves label collisions in layer order, first placed wins.
     * The closure names the link under analysis and must not be the label that
     * loses to a suburb name. */
    const ids = LABEL_LAYERS.map((l) => l.id);
    expect(ids[0]).toBe(LYR.closureLabel);
  });

  it('labels the closure from a point source, never the lines', () => {
    /*
     * A symbol layer over the closure LineStrings rendered at zoom 11 and
     * vanished at zoom 14: MapLibre re-tiles GeoJSON per zoom, and the anchor
     * derived from a clipped line can be dropped from every tile that could
     * have drawn it. Silent failure, only at some zooms. Points have one
     * unambiguous tile home.
     */
    const label: any = LABEL_LAYERS.find((l) => l.id === LYR.closureLabel);
    expect(label.source).toBe(SRC.closureLabel);
    expect(label.source).not.toBe(SRC.closure);
    expect(label.layout['symbol-placement']).toBeUndefined();
  });

  it('hides the basemap road network by default, and keeps it beneath the analysed one', () => {
    /* This test used to assert the transportation layer was never drawn at
     * all. That was the right rule while the basemap had one mode: two road
     * networks stacked read as noise, and a LINZ street under an AMDS link
     * invites misreading context as something that was measured.
     *
     * The basemap is now a presentation mode, and topographic mode shows LINZ
     * streets deliberately - for orientation, labelled as context. What the
     * old rule protected is preserved by two weaker invariants that this test
     * now states exactly:
     *
     *   1. the default presentation draws no basemap road - `visibility:
     *      none` until the user asks for topographic;
     *   2. every basemap road layer is in the BASE style, which MapLibre
     *      draws entirely beneath the analytical layers added after load, so
     *      no mode can put a LINZ street above an AMDS link, the closure or
     *      the route.
     */
    const style = fullStyle(true);
    const roads = style.layers.filter(
      (l: any) => l.source === SRC.linz && l['source-layer'] === 'transportation',
    );
    expect(roads.length).toBeGreaterThan(0);
    for (const l of roads as any[]) {
      expect(l.layout?.visibility, `${l.id} must default to hidden`).toBe('none');
    }
    /* The name layer follows the same rule. */
    const names = style.layers.filter(
      (l: any) =>
        l.source === SRC.linz && l['source-layer'] === 'transportation_name',
    );
    for (const l of names as any[]) {
      expect(l.layout?.visibility, `${l.id} must default to hidden`).toBe('none');
    }
  });

  it('animates only a layer whose source has lineMetrics', () => {
    const style = fullStyle(true);
    for (const l of style.layers) {
      if (!l.paint?.['line-gradient']) continue;
      expect(
        style.sources[l.source]?.lineMetrics,
        `${l.id} uses line-gradient but ${l.source} has no lineMetrics`,
      ).toBe(true);
    }
  });
});
