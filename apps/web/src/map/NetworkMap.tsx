/**
 * The real MapLibre map: LINZ basemap, the routable network as vector tiles,
 * and the current result's closure, routes and stranded links as GeoJSON.
 *
 * The network is served as vector tiles rather than a bulk GeoJSON download.
 * Only the selected closure and its detour are fetched as explicit geometry.
 *
 * Map instance lifetime is deliberately decoupled from React's render cycle:
 * the map is created once per snapshot and every prop that changes frequently
 * is read through a ref inside an event handler. Rebuilding the map on a parent
 * re-render meant it never finished loading — a real bug in the previous
 * implementation.
 */

import { useEffect, useRef, useState } from 'react';
import maplibregl, { type Map as MLMap, type MapGeoJSONFeature } from 'maplibre-gl';

import {
  LABEL_LAYERS,
  LYR,
  NETWORK_LAYERS,
  OVERLAY_LAYERS,
  SRC,
  baseStyle,
  emptyCollection,
  hasLinzKey,
  tileUrl,
} from './style.js';
import {
  closureLabelPoints,
  mergeRouteToLineString,
  revealGradient,
} from './route.js';
import { palette } from '../styles/palette.js';
import { api } from '../api/client.js';

export interface HoverInfo {
  x: number;
  y: number;
  name: string;
  roadNumber: string;
  lengthM: number;
  oneway: boolean;
  stateHighway: boolean;
}

export interface MapResult {
  /** Every link removed by the closure. */
  closure: GeoJSON.FeatureCollection | null;
  /** The focused direction's route, in path order. */
  focus: GeoJSON.FeatureCollection | null;
  /** The other direction, drawn dashed for comparison. */
  compare: GeoJSON.FeatureCollection | null;
  corridor: GeoJSON.FeatureCollection | null;
  stranded: GeoJSON.FeatureCollection | null;
  fitBounds: [number, number, number, number] | null;
  /** Changes whenever a new calculation lands, to retrigger the reveal. */
  revealKey: string;
}

export interface ScaleReading {
  label: string;
  widthPx: number;
}

/**
 * A geometry problem the map found while drawing a result.
 *
 * Reported upward rather than logged, so the inspector can show it beside the
 * figures it affects. A console warning is invisible to the person reading the
 * number.
 */
export interface GeometryWarning {
  kind: 'ROUTE_GEOMETRY_GAP';
  partCount: number;
  skippedArcs: number;
}

/**
 * Resolve a rendered map feature to a link id.
 *
 * Prefers the MVT feature id: ST_AsMVT removes the feature-id column from the
 * property bag, so a backend that publishes the id ONLY as the id would leave
 * properties.linkId undefined. Reading both means the client works against
 * either tile producer in this repository.
 */
function linkIdOf(f: MapGeoJSONFeature | undefined): number | null {
  if (!f) return null;
  const raw = f.id ?? (f.properties as Record<string, unknown> | undefined)?.linkId;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 ? n : null;
}

/** Nice round distances for the scale bar, in metres. */
const SCALE_STEPS = [
  10, 20, 50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000,
  100_000, 200_000, 500_000,
];

function scaleReading(map: MLMap): ScaleReading {
  /* Metres per pixel at the map centre. Web Mercator distorts with latitude,
   * so this must be computed from the current centre rather than assumed. */
  const y = map.getContainer().clientHeight / 2;
  const left = map.unproject([0, y]);
  const right = map.unproject([100, y]);
  const metresPer100px = left.distanceTo(right);
  const mPerPx = metresPer100px / 100;

  const target = 110; /* aim for roughly this many pixels */
  const rough = mPerPx * target;
  const step = SCALE_STEPS.find((s) => s >= rough) ?? SCALE_STEPS.at(-1)!;

  return {
    label: step >= 1000 ? `${step / 1000} km` : `${step} m`,
    widthPx: Math.round(step / mPerPx),
  };
}

export default function NetworkMap({
  snapshotId,
  tileSchemaVersion,
  result,
  onPickLink,
  onHoverChange,
  onScaleChange,
  onReady,
  onGeometryWarning,
  onBasemapError,
  homeExtent,
  goHomeSignal,
  locate,
  inset,
  previewLinkId,
  layersVisible,
}: {
  snapshotId: string | null;
  tileSchemaVersion: number;
  result: MapResult | null;
  onPickLink: (linkId: number) => void;
  onHoverChange: (h: HoverInfo | null) => void;
  onScaleChange: (s: ScaleReading | null) => void;
  onReady: () => void;
  onGeometryWarning: (w: GeometryWarning | null) => void;
  /** Called once if the LINZ basemap or its glyphs cannot be loaded. */
  onBasemapError: () => void;
  /**
   * What the map opens on, and returns to. The snapshot's coverage — the
   * country for a national snapshot, the extract for a regional one.
   */
  homeExtent: [number, number, number, number];
  /** Increment to send the map back to `homeExtent`. */
  goHomeSignal: number;
  /**
   * Somewhere to go immediately, before any result exists.
   *
   * Selecting a search result on a national map may name a road 900 km away.
   * Waiting for the detour to compute before moving leaves the user looking at
   * the wrong island while a spinner runs — feedback has to precede
   * computation. `seq` is a counter so repeat selections still move the map.
   */
  locate: { lon: number; lat: number; seq: number } | null;
  /**
   * How much of the map the inspector covers, so route framing centres in the
   * *visible* map rather than underneath the panel. On desktop that is a right
   * inset; on mobile the sheet covers the bottom instead.
   */
  inset: { right: number; bottom: number };
  /** A search candidate being previewed before selection. */
  previewLinkId: number | null;
  layersVisible: { network: boolean; basemap: boolean; labels: boolean };
}) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const ready = useRef(false);
  /*
   * The same flag as state, so effects can depend on it.
   *
   * A ref alone was not enough and the failure only appeared at national
   * scale. The result effect bails out when the map is not ready, and its only
   * dependency is `result` — which changes exactly once, from null to the
   * data. On the Wellington snapshot the style loaded faster than the detour
   * computed, so the map was always ready first. On the national snapshot the
   * detour lands while the style is still loading: the effect returned early
   * and never ran again, leaving the closure and route undrawn and the map
   * sitting at the country extent with a complete result beside it.
   */
  const [mapReady, setMapReady] = useState(false);

  /* Frequently-changing props, read from inside long-lived event handlers. */
  const pickRef = useRef(onPickLink);
  pickRef.current = onPickLink;
  const hoverRef = useRef(onHoverChange);
  hoverRef.current = onHoverChange;
  const scaleRef = useRef(onScaleChange);
  scaleRef.current = onScaleChange;
  const readyRef = useRef(onReady);
  readyRef.current = onReady;
  const insetRef = useRef(inset);
  insetRef.current = inset;
  /* Whether the current result's route had gaps, read by the reveal effect. */
  const gapsRef = useRef(false);
  const warnRef = useRef(onGeometryWarning);
  warnRef.current = onGeometryWarning;
  const basemapErrRef = useRef(onBasemapError);
  basemapErrRef.current = onBasemapError;
  /* The bounds of the result on screen, so a settled resize can reframe it. */
  const boundsRef = useRef<[number, number, number, number] | null>(null);
  const homeExtentRef = useRef(homeExtent);
  homeExtentRef.current = homeExtent;

  /* ------------------------------------------------------------ create */

  useEffect(() => {
    /* Wait for the snapshot: a tile URL without one cannot be cached safely,
     * and building the map before it is known would request the wrong tiles. */
    if (!container.current || mapRef.current || !snapshotId) return;

    /*
     * Open on what the snapshot actually covers.
     *
     * The centre and zoom used to be a hard-coded Wellington coordinate, so
     * even with the national network loaded the application opened looking
     * like a Wellington tool. `bounds` is used rather than centre+zoom so the
     * initial view is derived from coverage rather than guessed per snapshot.
     */
    const [w, s, e, n] = homeExtentRef.current;

    const map = new maplibregl.Map({
      container: container.current,
      style: baseStyle(tileUrl(api.base, tileSchemaVersion, snapshotId)),
      bounds: [
        [w, s],
        [e, n],
      ],
      fitBoundsOptions: { padding: 40 },
      /* Attribution is a licensing obligation for LINZ Basemaps (CC BY 4.0)
       * and for the AMDS source, not decoration. It is rendered as part of the
       * workspace furniture instead of MapLibre's own control, so it can be
       * styled to the design and cannot be collapsed away. */
      attributionControl: false,
    });
    mapRef.current = map;
    if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__map = map;

    /*
     * A LINZ failure — expired key, outage, rate limit — must not look like the
     * analysis failing. The network tiles come from our own backend and are
     * unaffected, so the basemap is reported once, non-blockingly, and the map
     * carries on over the graphite ground.
     *
     * Reported once: a failing raster source errors per tile, and forwarding
     * every one would put hundreds of identical notices through the UI.
     */
    let basemapReported = false;
    map.on('error', (e) => {
      const err = e as unknown as { error?: { message?: string }; sourceId?: string };
      const fromBasemap =
        err.sourceId === SRC.linz ||
        /basemaps\.linz|fonts\/.*\.pbf/.test(err.error?.message ?? '');

      if (fromBasemap) {
        if (!basemapReported) {
          basemapReported = true;
          basemapErrRef.current();
          console.warn('LINZ basemap unavailable:', err.error?.message);
        }
        return;
      }
      console.error('maplibre error:', err.error);
    });

    map.on('load', () => {
      for (const l of NETWORK_LAYERS) map.addLayer(l);

      map.addSource(SRC.closure, { type: 'geojson', data: emptyCollection() });
      map.addSource(SRC.closureLabel, {
        type: 'geojson',
        data: emptyCollection(),
      });
      map.addSource(SRC.routeCompare, { type: 'geojson', data: emptyCollection() });
      map.addSource(SRC.routeHit, { type: 'geojson', data: emptyCollection() });
      map.addSource(SRC.corridor, { type: 'geojson', data: emptyCollection() });
      map.addSource(SRC.stranded, { type: 'geojson', data: emptyCollection() });

      /* lineMetrics is what makes `line-progress` — and therefore the reveal —
       * exist at all. It cannot be enabled after the fact. */
      map.addSource(SRC.routeFocus, {
        type: 'geojson',
        data: emptyCollection(),
        lineMetrics: true,
      });

      for (const l of OVERLAY_LAYERS) map.addLayer(l);

      /* Text last: MapLibre draws in layer order, and a label under a route
       * line is unreadable. Skipped entirely without a LINZ key, because LINZ
       * is the glyph source and a symbol layer with no glyphs errors on every
       * tile rather than degrading quietly. */
      if (hasLinzKey()) {
        for (const l of LABEL_LAYERS) map.addLayer(l);
      }

      ready.current = true;
      map.getCanvas().style.cursor = 'crosshair';
      scaleRef.current(scaleReading(map));
      readyRef.current();
      /* Re-runs the effects that depend on the map being usable, so a result
       * that arrived during style load is applied rather than lost. */
      setMapReady(true);
    });

    /* ------------------------------------------------------- interaction */

    let hovered = -1;

    map.on('mousemove', LYR.networkHit, (e) => {
      const f = e.features?.[0];
      const id = linkIdOf(f);
      if (id === null) return;
      if (id !== hovered) {
        hovered = id;
        map.setFilter(LYR.networkHover, ['==', ['id'], id]);
      }
      const p = (f?.properties ?? {}) as Record<string, unknown>;
      hoverRef.current({
        x: e.point.x,
        y: e.point.y,
        name: String(p.roadName || '(unnamed link)'),
        roadNumber: String(p.roadNumber || ''),
        lengthM: Number(p.lengthM ?? 0),
        oneway: Number(p.oneway) === 1,
        stateHighway: Number(p.stateHighway) === 1,
      });
    });

    map.on('mouseleave', LYR.networkHit, () => {
      hovered = -1;
      map.setFilter(LYR.networkHover, ['==', ['id'], -1]);
      hoverRef.current(null);
    });

    map.on('click', LYR.networkHit, (e) => {
      const id = linkIdOf(e.features?.[0]);
      if (id !== null) pickRef.current(id);
    });

    const onMove = () => scaleRef.current(scaleReading(map));
    map.on('move', onMove);
    map.on('resize', onMove);

    return () => {
      map.remove();
      mapRef.current = null;
      ready.current = false;
    };
    /* Re-created only when the snapshot or tile schema changes. Every handler
     * above reads a ref, so parent re-renders cannot tear the map down. */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshotId, tileSchemaVersion]);

  /* ------------------------------------------------------ result -> map */

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current) return;

    const src = (id: string) =>
      map.getSource(id) as maplibregl.GeoJSONSource | undefined;

    if (!result) {
      for (const id of [
        SRC.closure,
        SRC.closureLabel,
        SRC.routeFocus,
        SRC.routeCompare,
        SRC.routeHit,
        SRC.corridor,
        SRC.stranded,
      ]) {
        src(id)?.setData(emptyCollection());
      }
      return;
    }

    src(SRC.closure)?.setData(result.closure ?? emptyCollection());
    src(SRC.closureLabel)?.setData(closureLabelPoints(result.closure));
    src(SRC.routeCompare)?.setData(result.compare ?? emptyCollection());
    src(SRC.corridor)?.setData(result.corridor ?? emptyCollection());
    src(SRC.stranded)?.setData(result.stranded ?? emptyCollection());

    /* The hit layer keeps the original per-arc features, because merging for
     * the reveal discards their properties. */
    src(SRC.routeHit)?.setData(result.focus ?? emptyCollection());

    /*
     * A gapped route is drawn as its contiguous parts and nothing between
     * them. It is NOT merged: concatenating across a gap yields a LineString
     * whose two sides are joined by a straight segment that exists in no
     * dataset, and the map would draw a confident line down a route nobody can
     * drive. Losing the reveal animation is cosmetic; drawing an invented road
     * is a false statement about the network.
     */
    const merged = mergeRouteToLineString(result.focus);
    gapsRef.current = merged.hasGaps;

    src(SRC.routeFocus)?.setData(
      merged.parts.length
        ? { type: 'FeatureCollection', features: merged.parts }
        : emptyCollection(),
    );

    /* line-gradient measures progress per feature, so with several parts the
     * reveal would restart in each one simultaneously. Pin it solid instead. */
    if (map.getLayer(LYR.routeFocus)) {
      map.setPaintProperty(
        LYR.routeFocus,
        'line-gradient',
        revealGradient(palette.route, 1) as never,
      );
    }

    warnRef.current(
      merged.hasGaps
        ? {
            kind: 'ROUTE_GEOMETRY_GAP',
            partCount: merged.parts.length,
            skippedArcs: merged.skipped,
          }
        : null,
    );

    boundsRef.current = result.fitBounds;
    if (result.fitBounds) frame(map, result.fitBounds, insetRef.current, true);
  }, [result, mapReady]);

  /*
   * Reframe after a settled resize.
   *
   * Framing accounts for the inspector, so once the panel is a different width
   * — or the mobile sheet has settled at a new stop — the route the user was
   * looking at may now be behind it. Only `map.resize()` used to run here,
   * which keeps the canvas correct but leaves the view where it was.
   *
   * Debounced, and only on the settled value: refitting on every drag pixel
   * would fight the user's own gesture.
   */
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.resize();

    const b = boundsRef.current;
    if (!b || !ready.current) return;

    const t = window.setTimeout(() => {
      frame(map, b, { right: inset.right, bottom: inset.bottom }, false);
    }, 220);
    return () => window.clearTimeout(t);
  }, [inset.right, inset.bottom]);

  /* --------------------------------------------------------- the reveal */

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current || !map.getLayer(LYR.routeFocus)) return;

    const set = (t: number) =>
      map.setPaintProperty(
        LYR.routeFocus,
        'line-gradient',
        revealGradient(palette.route, t) as never,
      );

    if (!result?.focus?.features?.length) {
      set(1);
      return;
    }

    /* Under reduced motion the path appears complete: one assignment, and the
     * animation loop never starts. */
    if (prefersReducedMotion()) {
      set(1);
      return;
    }

    /* A gapped route is several features, and `line-progress` is measured per
     * feature — every part would reveal from its own start at once, which
     * misreads as several separate routes drawing. Show it complete. */
    if (gapsRef.current) {
      set(1);
      return;
    }

    let raf = 0;
    let start = 0;
    const DURATION = 520;

    const step = (now: number) => {
      if (!start) start = now;
      const t = Math.min(1, (now - start) / DURATION);
      /* ease-out, matching the CSS timing tokens */
      set(1 - Math.pow(1 - t, 3));
      if (t < 1) raf = requestAnimationFrame(step);
    };

    set(0);
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [result?.revealKey, result?.focus, mapReady]);

  /* -------------------------------------------------------- layer state */

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current) return;
    const vis = (id: string, on: boolean) => {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
      }
    };
    vis(LYR.networkLine, layersVisible.network);
    for (const id of [LYR.linzWater, LYR.linzLandcover, LYR.linzBuilding]) {
      vis(id, layersVisible.basemap);
    }
    /* The closure label is exempt: it names the thing under analysis, and
     * turning off basemap labels should not hide what the user selected. */
    for (const id of [LYR.linzPlaceLabel, LYR.networkLabel]) {
      vis(id, layersVisible.labels);
    }
  }, [layersVisible, mapReady]);

  /*
   * Home: back to the snapshot's full extent, and forget the framed result so
   * a later resize does not quietly pull the view back to it.
   *
   * Keyed on a counter rather than a boolean, so pressing Home twice works.
   */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current || goHomeSignal === 0) return;
    boundsRef.current = null;
    const [w, s, e, n] = homeExtentRef.current;
    map.fitBounds(
      [
        [w, s],
        [e, n],
      ],
      { padding: 40, duration: prefersReducedMotion() ? 0 : 600 },
    );
  }, [goHomeSignal, mapReady]);

  /*
   * Go to a selected road at once, at a zoom where the local network is drawn.
   *
   * The detour's own `fitBounds` supersedes this a moment later; this exists so
   * the intervening moment is not spent looking at the wrong part of the
   * country.
   */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current || !locate) return;
    map.easeTo({
      center: [locate.lon, locate.lat],
      zoom: Math.max(map.getZoom(), 13),
      duration: prefersReducedMotion() ? 0 : 500,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locate?.seq]);

  /* Preview highlight for a search candidate, before it is selected. */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current || !map.getLayer(LYR.networkHover)) return;
    map.setFilter(LYR.networkHover, ['==', ['id'], previewLinkId ?? -1]);
  }, [previewLinkId, mapReady]);

  useEffect(() => {
    const el = container.current;
    const map = mapRef.current;
    if (!el || !map) return;
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, [snapshotId]);

  return <div className="map-canvas" ref={container} />;
}

function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Fit the map to a result, leaving room for whatever the inspector covers so
 * the route is centred in the *visible* map rather than underneath the panel.
 *
 * Padding is clamped to half the canvas: MapLibre throws when padding exceeds
 * the container, which a bottom sheet expanded to 90% of a short viewport
 * reaches easily.
 */
function frame(
  map: MLMap,
  bounds: [number, number, number, number],
  inset: { right: number; bottom: number },
  isNewResult: boolean,
) {
  const [w, s, e, n] = bounds;
  const { clientWidth: cw, clientHeight: ch } = map.getContainer();
  const pad = (v: number, extent: number) =>
    Math.max(20, Math.min(v, Math.floor(extent / 2) - 20));

  map.fitBounds(
    [
      [w, s],
      [e, n],
    ],
    {
      padding: {
        top: pad(60, ch),
        bottom: pad(inset.bottom + 40, ch),
        left: pad(60, cw),
        right: pad(inset.right + 50, cw),
      },
      maxZoom: 15.5,
      /* A reframe after a resize is a correction, not an arrival: it should be
       * quick and unobtrusive rather than the full 700ms reveal ease. */
      duration: prefersReducedMotion() ? 0 : isNewResult ? 700 : 260,
    },
  );
}
