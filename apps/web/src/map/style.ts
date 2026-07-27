/**
 * MapLibre style and layer definitions.
 *
 * Kept out of the component so the layer stack is readable as a document: the
 * order of `OVERLAY_LAYERS` is the draw order, and draw order is the whole
 * argument about what stays legible when several things overlap.
 */

import type {
  LayerSpecification,
  StyleSpecification,
} from 'maplibre-gl';

import { palette } from '../styles/palette.js';

const LINZ_KEY = import.meta.env.VITE_LINZ_API_KEY as string | undefined;

export function hasLinzKey(): boolean {
  return Boolean(LINZ_KEY && LINZ_KEY !== 'replace_me');
}

/**
 * LINZ serves the topographic basemap as **vector** tiles, and that is what
 * this app uses rather than the aerial raster.
 *
 * Aerial imagery is the wrong basemap for this product. Direction C is a dark,
 * quiet, low-chroma canvas whose entire purpose is to make the road network the
 * brightest thing on screen; full-colour photography is the opposite, and
 * desaturating it to fit just produces grey mud that still competes with the
 * routes for attention. Vector tiles can be styled to the design instead of
 * fought with.
 *
 * It also brings a glyph endpoint, which is what makes map labels possible at
 * all — MapLibre cannot render a single character of text without one.
 *
 * Only a handful of LINZ's 273 layers are drawn: water, land cover and
 * buildings for context. Their `transportation` layer is deliberately NOT
 * drawn — the AMDS network is the subject of the analysis, and a second road
 * network underneath it would be both visual noise and a lie about what is
 * being measured.
 */
const LINZ_TILES =
  'https://basemaps.linz.govt.nz/v1/tiles/topographic/WebMercatorQuad/{z}/{x}/{y}.pbf';

/** LINZ serves only Open Sans Regular and Noto Sans Regular. */
export const LABEL_FONT = ['Open Sans Regular'];

function linzUrl(template: string): string {
  return `${template}?api=${LINZ_KEY}`;
}

/** Source and layer ids, so nothing is addressed by a bare string twice. */
export const SRC = {
  network: 'network',
  closure: 'closure',
  routeFocus: 'route-focus',
  routeCompare: 'route-compare',
  routeHit: 'route-hit',
  corridor: 'corridor',
  stranded: 'stranded',
  closureLabel: 'closure-label-anchor',
  linz: 'linz',
} as const;

export const LYR = {
  background: 'background',
  linzWater: 'linz-water',
  linzLandcover: 'linz-landcover',
  linzBuilding: 'linz-building',
  linzPlaceLabel: 'linz-place-label',
  networkLine: 'network-line',
  networkLabel: 'network-label',
  closureLabel: 'closure-label',
  networkHit: 'network-hit',
  networkHover: 'network-hover',
  stranded: 'stranded-line',
  corridor: 'corridor-line',
  routeCompare: 'route-compare-line',
  routeFocus: 'route-focus-line',
  routeHit: 'route-hit-line',
  closureHalo: 'closure-halo',
  closureLine: 'closure-line',
} as const;

/**
 * Tile URL carries the schema version and snapshot id.
 *
 * A bare /tiles/{z}/{x}/{y} cached for an hour gets reinterpreted after a
 * snapshot or schema change — stale geometry, or property names the client no
 * longer reads, with no way for the browser to tell. Addressing both makes a
 * stale tile unreachable rather than wrong.
 *
 * THE URL MUST BE ABSOLUTE, even though every other request in this app is
 * deliberately same-origin and relative. MapLibre loads tiles from a **web
 * worker**, and a worker has no document to resolve a relative URL against:
 * `new Request('/tiles/v2/…')` throws `Failed to parse URL`. The failure is
 * quiet in the worst way — the style loads, every layer is registered, and the
 * map simply renders nothing.
 *
 * `api.base` stays empty for same-origin deployments, so the origin is taken
 * from the page. An explicit VITE_API_BASE_URL is already absolute and passes
 * through unchanged.
 */
export function tileUrl(
  base: string,
  schemaVersion: number,
  snapshotId: string,
): string {
  const path = `${base}/tiles/v${schemaVersion}/${encodeURIComponent(
    snapshotId,
  )}/{z}/{x}/{y}.pbf`;

  /* The {z}/{x}/{y} placeholders are not valid URL characters everywhere, so
   * resolve a placeholder-free path and put them back. */
  const resolved = new URL(
    path.replace('{z}/{x}/{y}', '_z_/_x_/_y_'),
    window.location.href,
  ).toString();

  return resolved.replace('_z_/_x_/_y_', '{z}/{x}/{y}');
}

export function emptyCollection(): GeoJSON.FeatureCollection {
  return { type: 'FeatureCollection', features: [] };
}

/**
 * The base style: graphite ground, the LINZ topographic vector basemap styled
 * down to context, and the routable network as vector tiles.
 *
 * `glyphs` is only set when a LINZ key is configured, because LINZ is the glyph
 * source. It must be omitted entirely rather than set to undefined: the style
 * specification requires a string, and `glyphs: undefined` fails validation. A
 * failed style load leaves every source unregistered, which surfaces later as
 * `source "network" not found` when the layers are added.
 */
export function baseStyle(tiles: string): StyleSpecification {
  const sources: StyleSpecification['sources'] = {
    [SRC.network]: {
      type: 'vector',
      tiles: [tiles],
      minzoom: 0,
      maxzoom: 15,
    },
  };

  const layers: LayerSpecification[] = [
    {
      id: LYR.background,
      type: 'background',
      paint: { 'background-color': palette.mapLand },
    },
  ];

  const style: Record<string, unknown> = { version: 8, sources, layers };

  if (hasLinzKey()) {
    sources[SRC.linz] = {
      type: 'vector',
      tiles: [linzUrl(LINZ_TILES)],
      minzoom: 0,
      maxzoom: 15,
      attribution:
        '<a href="https://www.linz.govt.nz/">LINZ</a> Basemaps — CC BY 4.0',
    };

    style.glyphs = linzUrl(
      'https://basemaps.linz.govt.nz/v1/fonts/{fontstack}/{range}.pbf',
    );

    /*
     * Context, in three quiet layers. Every colour here is darker than the
     * network draws with, so nothing on the basemap can be mistaken for a
     * road, a route or a closure.
     */
    layers.push(
      {
        id: LYR.linzLandcover,
        type: 'fill',
        source: SRC.linz,
        'source-layer': 'landcover',
        paint: { 'fill-color': '#1b232a', 'fill-opacity': 0.6 },
      },
      {
        /* Water is the one basemap feature that genuinely explains a detour —
         * a harbour is why the alternative is 55 km. It gets the strongest
         * contrast of the three. */
        id: LYR.linzWater,
        type: 'fill',
        source: SRC.linz,
        'source-layer': 'water',
        paint: { 'fill-color': palette.mapWater, 'fill-opacity': 1 },
      },
      {
        id: LYR.linzBuilding,
        type: 'fill',
        source: SRC.linz,
        'source-layer': 'building',
        minzoom: 14,
        paint: { 'fill-color': '#252e36', 'fill-opacity': 0.85 },
      },
    );
  }

  return style as unknown as StyleSpecification;
}

/**
 * Text.
 *
 * Kept separate from the geometry layers because these must be added last —
 * MapLibre draws in layer order and a label under a route line is unreadable.
 *
 * Road names come from OUR OWN tiles, not the basemap's `transportation`
 * layer. That is the point: the label then names the link the analysis
 * actually evaluated, with the name AMDS records for it, rather than whatever
 * a different dataset happens to call the road at that location.
 */
export const LABEL_LAYERS: LayerSpecification[] = [
  {
    /*
     * The closed road, named on the map.
     *
     * FIRST in this array on purpose. MapLibre resolves label collisions in
     * layer order, first placed wins, so the closure gets priority over place
     * and road names — the user selected this link and it must not be the
     * label that loses.
     *
     * The source is a derived Point collection, not the closure LineStrings —
     * see `closureLabelPoints` for why. A symbol layer over the lines rendered
     * at zoom 11 and vanished at zoom 14, because the anchor MapLibre derives
     * from a tile-clipped LineString can be dropped from every tile that could
     * have drawn it.
     *
     * Overlap is allowed. This is the one label that must never lose: the user
     * selected this link, and a map that hides the name of the thing under
     * analysis to satisfy a collision rule is answering a different question.
     * The duplicate-label risk that would normally argue against this is
     * already handled upstream, by deriving one anchor per road name.
     */
    id: LYR.closureLabel,
    type: 'symbol',
    source: SRC.closureLabel,
    layout: {
      'text-field': ['get', 'roadName'],
      'text-font': LABEL_FONT,
      'text-size': 12,
      'text-offset': [0, -1.5],
      'text-anchor': 'bottom',
      'text-allow-overlap': true,
      'text-ignore-placement': true,
    },
    paint: {
      'text-color': palette.closure,
      'text-halo-color': '#0b0f12',
      'text-halo-width': 1.8,
    },
  },
  {
    id: LYR.linzPlaceLabel,
    type: 'symbol',
    source: SRC.linz,
    'source-layer': 'place',
    minzoom: 8,
    layout: {
      'text-field': ['get', 'name'],
      'text-font': LABEL_FONT,
      'text-size': ['interpolate', ['linear'], ['zoom'], 8, 10, 14, 13],
      'text-transform': 'uppercase',
      'text-letter-spacing': 0.12,
      'text-max-width': 8,
    },
    paint: {
      'text-color': palette.shellFgFaint,
      'text-halo-color': palette.mapLand,
      'text-halo-width': 1.2,
    },
  },
  {
    /* Road names along the line. Late zoom only: at z13 the network is dense
     * enough that labelling it would bury the routes under text. */
    id: LYR.networkLabel,
    type: 'symbol',
    source: SRC.network,
    'source-layer': 'network',
    minzoom: 13.5,
    filter: ['!=', ['get', 'roadName'], ''],
    layout: {
      'symbol-placement': 'line',
      'text-field': ['get', 'roadName'],
      'text-font': LABEL_FONT,
      'text-size': ['interpolate', ['linear'], ['zoom'], 13.5, 9.5, 17, 12],
      'text-max-angle': 35,
      'symbol-spacing': 260,
    },
    paint: {
      'text-color': palette.shellFgMuted,
      'text-halo-color': palette.mapLand,
      'text-halo-width': 1.4,
      'text-opacity': 0.85,
    },
  },
];

/** The network itself, plus its fat invisible hit line and hover highlight. */
export const NETWORK_LAYERS: LayerSpecification[] = [
  {
    id: LYR.networkLine,
    type: 'line',
    source: SRC.network,
    'source-layer': 'network',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': [
        'case',
        ['==', ['get', 'core'], 0],
        palette.mapLocal,
        ['==', ['get', 'stateHighway'], 1],
        palette.mapHighway,
        '#4c5a68',
      ],
      'line-width': [
        'interpolate',
        ['linear'],
        ['zoom'],
        8,
        ['case', ['==', ['get', 'stateHighway'], 1], 1.2, 0.4],
        12,
        ['case', ['==', ['get', 'stateHighway'], 1], 2.2, 1.0],
        16,
        ['case', ['==', ['get', 'stateHighway'], 1], 4.6, 2.4],
      ],
      /* Links outside the analysis area are drawn, but held back: they exist
       * on the ground and hiding them would misrepresent the network, yet a
       * replacement path cannot use them. */
      'line-opacity': ['case', ['==', ['get', 'core'], 0], 0.4, 0.92],
    },
  },
  {
    /* A 14px transparent line over a 1–3px visible one, so selecting a road
     * never demands pixel precision. */
    id: LYR.networkHit,
    type: 'line',
    source: SRC.network,
    'source-layer': 'network',
    paint: { 'line-color': '#000', 'line-opacity': 0, 'line-width': 14 },
  },
  {
    id: LYR.networkHover,
    type: 'line',
    source: SRC.network,
    'source-layer': 'network',
    layout: { 'line-cap': 'round' },
    paint: {
      'line-color': palette.shellFg,
      'line-width': 3.4,
      'line-opacity': 0.75,
    },
    filter: ['==', ['id'], -1],
  },
];

/**
 * Overlay layers, in draw order — bottom first.
 *
 * Stranded and comparison sit beneath the focused route, and the closure sits
 * above everything, because the closure is the one thing that must never be
 * obscured by the answer it produced.
 */
export const OVERLAY_LAYERS: LayerSpecification[] = [
  {
    /* Isolation: the links that lose connectivity, in amber. Deliberately NOT
     * a hull or affected-area polygon — the engine identifies links, not a
     * catchment, and a drawn region would claim an extent the analysis does
     * not compute. */
    id: LYR.stranded,
    type: 'line',
    source: SRC.stranded,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': palette.stranded,
      'line-width': 3,
      'line-opacity': 0.85,
    },
  },
  {
    id: LYR.corridor,
    type: 'line',
    source: SRC.corridor,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': palette.corridor,
      'line-width': 2.4,
      'line-opacity': 0.7,
    },
  },
  {
    /*
     * The comparison direction. Dashed, and therefore on its own layer with a
     * flat colour: `line-gradient` and `line-dasharray` cannot coexist on one
     * layer, so this route never animates. It appears complete, which is also
     * the correct emphasis — one direction is always dominant.
     */
    id: LYR.routeCompare,
    type: 'line',
    source: SRC.routeCompare,
    layout: { 'line-cap': 'butt', 'line-join': 'round' },
    paint: {
      'line-color': palette.compare,
      'line-width': 2.6,
      'line-opacity': 0.75,
      'line-dasharray': [2.2, 1.6],
      /* Where the two directions share geometry, offsetting the comparison
       * keeps a shared corridor readable as two lines rather than one muddy
       * one. */
      'line-offset': 3,
    },
  },
  {
    /*
     * The focused replacement route. Its source sets `lineMetrics: true` and
     * holds ONE merged LineString, because `line-gradient` is driven by
     * `line-progress`, which MapLibre computes per feature — a 327-feature
     * collection would have 327 independent progress ramps.
     */
    id: LYR.routeFocus,
    type: 'line',
    source: SRC.routeFocus,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-width': 3.4,
      'line-gradient': [
        'interpolate',
        ['linear'],
        ['line-progress'],
        0,
        palette.route,
        1,
        palette.route,
      ],
    },
  },
  {
    /* Merging the route into one LineString discards per-arc properties, so
     * hover metadata comes from this transparent layer, which keeps the
     * original ordered feature collection. */
    id: LYR.routeHit,
    type: 'line',
    source: SRC.routeHit,
    paint: { 'line-color': '#000', 'line-opacity': 0, 'line-width': 12 },
  },
  {
    id: LYR.closureHalo,
    type: 'line',
    source: SRC.closure,
    layout: { 'line-cap': 'round' },
    paint: {
      'line-color': palette.closure,
      'line-width': 15,
      'line-opacity': 0.22,
      'line-blur': 5,
    },
  },
  {
    id: LYR.closureLine,
    type: 'line',
    source: SRC.closure,
    layout: { 'line-cap': 'round' },
    paint: { 'line-color': palette.closure, 'line-width': 5 },
  },
];
