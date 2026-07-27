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
  LYR,
  NETWORK_LAYERS,
  OVERLAY_LAYERS,
  SRC,
  baseStyle,
  emptyCollection,
  tileUrl,
} from './style.js';
import { mergeRouteToLineString, revealGradient } from './route.js';
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
  const [, forceRender] = useState(0);

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

  /* ------------------------------------------------------------ create */

  useEffect(() => {
    /* Wait for the snapshot: a tile URL without one cannot be cached safely,
     * and building the map before it is known would request the wrong tiles. */
    if (!container.current || mapRef.current || !snapshotId) return;

    const map = new maplibregl.Map({
      container: container.current,
      style: baseStyle(tileUrl(api.base, tileSchemaVersion, snapshotId)),
      center: [174.86, -41.14],
      zoom: 10.4,
      /* Attribution is a licensing obligation for LINZ Basemaps (CC BY 4.0)
       * and for the AMDS source, not decoration. It is rendered as part of the
       * workspace furniture instead of MapLibre's own control, so it can be
       * styled to the design and cannot be collapsed away. */
      attributionControl: false,
    });
    mapRef.current = map;
    if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__map = map;

    map.on('error', (e) =>
      console.error('maplibre error:', (e as unknown as { error?: unknown }).error),
    );

    map.on('load', () => {
      for (const l of NETWORK_LAYERS) map.addLayer(l);

      map.addSource(SRC.closure, { type: 'geojson', data: emptyCollection() });
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

      ready.current = true;
      map.getCanvas().style.cursor = 'crosshair';
      scaleRef.current(scaleReading(map));
      readyRef.current();
      forceRender((n) => n + 1);
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
    src(SRC.routeCompare)?.setData(result.compare ?? emptyCollection());
    src(SRC.corridor)?.setData(result.corridor ?? emptyCollection());
    src(SRC.stranded)?.setData(result.stranded ?? emptyCollection());

    /* The hit layer keeps the original per-arc features, because merging for
     * the reveal discards their properties. */
    src(SRC.routeHit)?.setData(result.focus ?? emptyCollection());

    const merged = mergeRouteToLineString(result.focus);
    if (merged.hasGaps) {
      console.warn(
        'Route arcs did not meet end to end. The API is expected to return ' +
          'them in path order, already oriented in travel direction.',
      );
    }
    src(SRC.routeFocus)?.setData(
      merged.feature
        ? { type: 'FeatureCollection', features: [merged.feature] }
        : emptyCollection(),
    );

    if (result.fitBounds) {
      const [w, s, e, n] = result.fitBounds;
      /*
       * Padding accounts for whatever the inspector covers, so the route is
       * centred in the *visible* map rather than underneath the panel.
       *
       * Clamped: MapLibre throws if padding exceeds the canvas, which is easy
       * to hit when a bottom sheet is expanded to 90% of a short viewport.
       */
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
            bottom: pad(insetRef.current.bottom + 40, ch),
            left: pad(60, cw),
            right: pad(insetRef.current.right + 50, cw),
          },
          maxZoom: 15.5,
          duration: prefersReducedMotion() ? 0 : 700,
        },
      );
    }
  }, [result]);

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
  }, [result?.revealKey, result?.focus]);

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
    vis(LYR.linz, layersVisible.basemap);
  }, [layersVisible]);

  /* Preview highlight for a search candidate, before it is selected. */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current || !map.getLayer(LYR.networkHover)) return;
    map.setFilter(LYR.networkHover, ['==', ['id'], previewLinkId ?? -1]);
  }, [previewLinkId]);

  /* The map must resize when the inspector does, or the canvas keeps its old
   * width and the right-hand slice of the network is simply never drawn. */
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.resize();
  }, [inset.right, inset.bottom]);

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
