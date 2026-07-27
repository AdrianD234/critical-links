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
 */
function fullStyle(withLinz: boolean): any {
  const style: any = JSON.parse(JSON.stringify(baseStyle(TILES)));

  if (withLinz) {
    /* baseStyle only adds the raster source when a key is configured, and the
     * test environment has none. Add it here so the with-basemap case is still
     * validated. */
    style.sources[SRC.linz] = {
      type: 'raster',
      tiles: [
        'https://basemaps.linz.govt.nz/v1/tiles/topographic/WebMercatorQuad/{z}/{x}/{y}.webp?api=KEY',
      ],
      tileSize: 256,
      attribution: 'LINZ Basemaps — CC BY 4.0',
    };
    style.layers.push({
      id: LYR.linz,
      type: 'raster',
      source: SRC.linz,
      paint: { 'raster-opacity': 0.42, 'raster-saturation': -0.72 },
    });
  }

  for (const id of [
    SRC.closure,
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
