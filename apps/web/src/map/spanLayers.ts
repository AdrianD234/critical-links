/**
 * Map sources and layers for the two-point outage editor.
 *
 * Kept out of `style.ts` because the editor is a flagged draft: a build with
 * the flag off must register exactly the sources and layers it registered
 * before this feature existed, and the cheapest way to guarantee that is for
 * these never to be added at all.
 *
 * The colours are `palette.closure` and nothing new. The span IS a closure -
 * the same thing the rest of the application draws in that red - and inventing
 * a second red for it would say the two are different kinds of claim. A drift
 * test already binds `palette.ts` to `tokens.css`; adding a token here would
 * have to earn its place in both.
 */

import type { LayerSpecification } from 'maplibre-gl';

import { palette } from '../styles/palette.js';

export const SPAN_SRC = {
  /** The closed stretch, as the server cut it. */
  span: 'span-closure',
  /** The A and B handles. */
  handles: 'span-handles',
  /** The replacement path, once measured. */
  replacement: 'span-replacement',
} as const;

export const SPAN_LYR = {
  spanHalo: 'span-closure-halo',
  spanLine: 'span-closure-line',
  replacement: 'span-replacement-line',
  handleHalo: 'span-handle-halo',
  handleDot: 'span-handle-dot',
  handleLabel: 'span-handle-label',
} as const;

/**
 * Drawn under the handles, over the network.
 *
 * The halo is what makes a red line legible over a red-brown road at low zoom;
 * it is the same device the whole-link closure uses, at the same widths, so a
 * span and a link closure read as the same kind of mark.
 */
export function spanLayers(withLabels: boolean): LayerSpecification[] {
  const layers: LayerSpecification[] = [
    {
      id: SPAN_LYR.replacement,
      type: 'line',
      source: SPAN_SRC.replacement,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': palette.route,
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 2, 14, 4.5],
        'line-opacity': 0.9,
      },
    },
    {
      id: SPAN_LYR.spanHalo,
      type: 'line',
      source: SPAN_SRC.span,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': palette.shellBg,
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 6, 14, 12],
        'line-opacity': 0.85,
      },
    },
    {
      id: SPAN_LYR.spanLine,
      type: 'line',
      source: SPAN_SRC.span,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': palette.closure,
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 3, 14, 7],
        /* Dimmed while the drawn span describes an older handle position than
         * the handles do. Removing it instead would make every mouse movement
         * flash the closure out of existence, which reads as breakage. */
        'line-opacity': ['case', ['get', 'stale'], 0.45, 1],
      },
    },
    {
      id: SPAN_LYR.handleHalo,
      type: 'circle',
      source: SPAN_SRC.handles,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 8, 14, 12],
        'circle-color': palette.shellBg,
        'circle-opacity': 0.9,
      },
    },
    {
      id: SPAN_LYR.handleDot,
      type: 'circle',
      source: SPAN_SRC.handles,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 5, 14, 8],
        'circle-color': palette.closure,
        'circle-stroke-color': palette.shellFg,
        /* The handle under the pointer is ringed, so which one is being moved
         * is visible without relying on the cursor. */
        'circle-stroke-width': ['case', ['get', 'active'], 3, 1.5],
      },
    },
  ];

  if (withLabels) {
    layers.push({
      id: SPAN_LYR.handleLabel,
      type: 'symbol',
      source: SPAN_SRC.handles,
      layout: {
        'text-field': ['get', 'label'],
        'text-font': ['Noto Sans Bold'],
        'text-size': 11,
        'text-offset': [0, -1.4],
        'text-allow-overlap': true,
      },
      paint: {
        'text-color': palette.shellFg,
        'text-halo-color': palette.shellBg,
        'text-halo-width': 1.5,
      },
    });
  }

  return layers;
}

/** The handles as drawable points. `A` and `B` are the order the user set. */
export function handleFeatures(
  a: { lon: number; lat: number } | null,
  b: { lon: number; lat: number } | null,
  active: 'a' | 'b' | null,
): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  const push = (
    id: 'a' | 'b',
    point: { lon: number; lat: number } | null,
  ) => {
    if (!point) return;
    features.push({
      type: 'Feature',
      id: id === 'a' ? 1 : 2,
      geometry: { type: 'Point', coordinates: [point.lon, point.lat] },
      properties: {
        handle: id,
        label: id.toUpperCase(),
        active: active === id,
      },
    });
  };
  push('a', a);
  push('b', b);
  return { type: 'FeatureCollection', features };
}

/**
 * The closed stretch, tagged with whether it is still current.
 *
 * The `stale` flag rides on the feature rather than on a paint override so
 * that one source update carries both the geometry and its standing.
 */
export function spanFeatures(
  geometry: { features: unknown[] } | null | undefined,
  stale: boolean,
): GeoJSON.FeatureCollection {
  if (!geometry) return { type: 'FeatureCollection', features: [] };
  const features = (geometry.features as GeoJSON.Feature[]).map((f) => ({
    ...f,
    properties: { ...(f.properties ?? {}), stale },
  }));
  return { type: 'FeatureCollection', features };
}
