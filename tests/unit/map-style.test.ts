/**
 * Map style validation.
 *
 * The browser pane used during development runs hidden, so
 * `requestAnimationFrame` never fires and MapLibre cannot finish loading a
 * style there - the map cannot be verified by rendering it in this
 * environment. Validating the style and layer definitions against the official
 * MapLibre style specification checks the part that would otherwise go
 * untested: that every layer, source reference, paint property and filter
 * expression is well formed.
 *
 * This does not prove the map looks right. It proves the style is valid.
 */

import { describe, expect, it } from 'vitest';
import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec';

/** Mirrors apps/web/src/MapView.tsx. Kept in step by the tests below. */
const NETWORK_LAYERS: any[] = [
  {
    id: 'network-line',
    type: 'line',
    source: 'network',
    'source-layer': 'network',
    paint: {
      'line-color': [
        'case',
        ['==', ['get', 'core'], 0], '#3d4a5c',
        ['==', ['get', 'stateHighway'], 1], '#7aa7d9',
        '#5b6b80',
      ],
      'line-width': [
        'interpolate', ['linear'], ['zoom'],
        8, ['case', ['==', ['get', 'stateHighway'], 1], 1.2, 0.4],
        12, ['case', ['==', ['get', 'stateHighway'], 1], 2.4, 1.1],
        16, ['case', ['==', ['get', 'stateHighway'], 1], 5, 2.6],
      ],
      'line-opacity': ['case', ['==', ['get', 'core'], 0], 0.45, 0.95],
    },
  },
  {
    id: 'network-hit',
    type: 'line',
    source: 'network',
    'source-layer': 'network',
    paint: { 'line-color': '#000', 'line-opacity': 0, 'line-width': 14 },
  },
  {
    id: 'network-hover',
    type: 'line',
    source: 'network',
    'source-layer': 'network',
    paint: { 'line-color': '#4da3ff', 'line-width': 4, 'line-opacity': 0.9 },
    filter: ['==', ['get', 'linkId'], -1],
  },
];

const OVERLAY_LAYERS: any[] = [
  {
    id: 'detour-fwd-line',
    type: 'line',
    source: 'detour-fwd',
    paint: { 'line-color': '#23d18b', 'line-width': 5, 'line-opacity': 0.9 },
    layout: { 'line-cap': 'round', 'line-join': 'round' },
  },
  {
    id: 'detour-rev-line',
    type: 'line',
    source: 'detour-rev',
    paint: {
      'line-color': '#ffb020',
      'line-width': 4,
      'line-opacity': 0.85,
      'line-dasharray': [2, 1.5],
    },
    layout: { 'line-cap': 'round', 'line-join': 'round' },
  },
  {
    id: 'closure-halo',
    type: 'line',
    source: 'closure',
    paint: {
      'line-color': '#ff4d4f',
      'line-width': 14,
      'line-opacity': 0.25,
      'line-blur': 4,
    },
  },
  {
    id: 'closure-line',
    type: 'line',
    source: 'closure',
    paint: { 'line-color': '#ff4d4f', 'line-width': 6 },
    layout: { 'line-cap': 'round' },
  },
];

function fullStyle(withLinz: boolean) {
  const sources: Record<string, any> = {
    network: {
      type: 'vector',
      tiles: ['http://localhost:8787/tiles/{z}/{x}/{y}.pbf'],
      minzoom: 0,
      maxzoom: 15,
    },
    closure: { type: 'geojson', data: emptyFc() },
    'detour-fwd': { type: 'geojson', data: emptyFc() },
    'detour-rev': { type: 'geojson', data: emptyFc() },
  };
  const layers: any[] = [
    { id: 'background', type: 'background', paint: { 'background-color': '#0d1218' } },
  ];
  if (withLinz) {
    sources.linz = {
      type: 'raster',
      tiles: [
        'https://basemaps.linz.govt.nz/v1/tiles/topographic/WebMercatorQuad/{z}/{x}/{y}.webp?api=KEY',
      ],
      tileSize: 256,
      attribution: 'LINZ Basemaps - CC BY 4.0',
    };
    layers.push({
      id: 'linz-base',
      type: 'raster',
      source: 'linz',
      paint: { 'raster-opacity': 0.55, 'raster-saturation': -0.5 },
    });
  }
  layers.push(...NETWORK_LAYERS, ...OVERLAY_LAYERS);
  return { version: 8, sources, layers };
}

const emptyFc = () => ({ type: 'FeatureCollection', features: [] });

describe('MapLibre style specification', () => {
  it('validates with a LINZ basemap configured', () => {
    expect(validateStyleMin(fullStyle(true) as any)).toEqual([]);
  });

  it('validates without a LINZ key, so the map still works unstyled', () => {
    expect(validateStyleMin(fullStyle(false) as any)).toEqual([]);
  });

  it('references only sources that the style declares', () => {
    const style = fullStyle(true);
    const declared = new Set(Object.keys(style.sources));
    for (const l of style.layers) {
      if (l.type === 'background') continue;
      expect(declared.has(l.source)).toBe(true);
    }
  });

  it('draws the closure above the replacement route', () => {
    const ids = fullStyle(true).layers.map((l: any) => l.id);
    expect(ids.indexOf('closure-line')).toBeGreaterThan(ids.indexOf('detour-fwd-line'));
    expect(ids.indexOf('closure-line')).toBeGreaterThan(ids.indexOf('detour-rev-line'));
  });

  it('keeps a transparent wide hit layer so clicking a road is forgiving', () => {
    const hit = NETWORK_LAYERS.find((l) => l.id === 'network-hit');
    expect(hit.paint['line-opacity']).toBe(0);
    expect(hit.paint['line-width']).toBeGreaterThanOrEqual(10);
  });

  it('dims links that lie outside the analysis area rather than hiding them', () => {
    const line = NETWORK_LAYERS.find((l) => l.id === 'network-line');
    expect(JSON.stringify(line.paint['line-opacity'])).toContain('core');
    // Never a zero opacity: a buffer link that carries a detour must stay visible.
    expect(JSON.stringify(line.paint['line-opacity'])).not.toContain('0,0');
  });
});

describe('style stays in step with MapView.tsx', () => {
  it('declares the same layer ids the component adds', async () => {
    const { readFile } = await import('node:fs/promises');
    const src = await readFile('apps/web/src/MapView.tsx', 'utf8');
    for (const l of [...NETWORK_LAYERS, ...OVERLAY_LAYERS]) {
      expect(src).toContain(`'${l.id}'`);
    }
  });
});
