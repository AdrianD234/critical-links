/**
 * MapLibre map: LINZ basemap, the routable network as vector tiles, and the
 * selected closure plus its replacement route as GeoJSON overlays.
 *
 * The network is served as vector tiles rather than a bulk GeoJSON download.
 * Only the selected closure and its detour are fetched as explicit geometry.
 */

import { useEffect, useRef } from 'react';
import maplibregl, { type Map as MLMap } from 'maplibre-gl';

import { api, type DetourResponse } from './api.js';

const LINZ_KEY = import.meta.env.VITE_LINZ_API_KEY as string | undefined;

/**
 * LINZ Basemaps needs a free API key. Without one the map still works, on a
 * plain background, and says so rather than silently showing an empty canvas.
 */
function baseStyle(): maplibregl.StyleSpecification {
  const sources: Record<string, any> = {
    network: {
      type: 'vector',
      tiles: [`${api.base}/tiles/{z}/{x}/{y}.pbf`],
      minzoom: 0,
      maxzoom: 15,
    },
  };
  const layers: maplibregl.LayerSpecification[] = [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#0d1218' },
    },
  ];

  if (LINZ_KEY && LINZ_KEY !== 'replace_me') {
    sources.linz = {
      type: 'raster',
      tiles: [
        `https://basemaps.linz.govt.nz/v1/tiles/topographic/WebMercatorQuad/{z}/{x}/{y}.webp?api=${LINZ_KEY}`,
      ],
      tileSize: 256,
      attribution:
        '<a href="https://www.linz.govt.nz/">LINZ</a> Basemaps - CC BY 4.0',
    };
    layers.push({
      id: 'linz-base',
      type: 'raster',
      source: 'linz',
      paint: { 'raster-opacity': 0.55, 'raster-saturation': -0.5 },
    });
  }

  return { version: 8, sources, layers, glyphs: undefined } as any;
}

const NETWORK_LAYERS: maplibregl.LayerSpecification[] = [
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
    // Invisible fat line so clicking a road does not demand pixel precision.
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

export interface MapViewProps {
  detour: DetourResponse | null;
  onPickLink: (linkId: number) => void;
  showCorridor: boolean;
}

export default function MapView({ detour, onPickLink, showCorridor }: MapViewProps) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const readyRef = useRef(false);
  // The click handler is held in a ref so that a new closure identity from the
  // parent cannot re-run the mount effect. Tearing the map down and rebuilding
  // it on every render meant it never finished loading.
  const pickRef = useRef(onPickLink);
  pickRef.current = onPickLink;

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: ref.current,
      style: baseStyle(),
      center: [174.7772, -41.2889],
      zoom: 11,
      attributionControl: false,
    });
    mapRef.current = map;
    // Dev-only handle for console inspection. Assigned before any listener so
    // that a failure inside the load handler is still diagnosable.
    if (import.meta.env.DEV) (window as any).__map = map;
    map.on('error', (e) => console.error('maplibre error:', e && (e as any).error));
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

    map.on('load', () => {
      for (const l of NETWORK_LAYERS) map.addLayer(l);

      map.addSource('closure', { type: 'geojson', data: empty() });
      map.addSource('detour-fwd', { type: 'geojson', data: empty() });
      map.addSource('detour-rev', { type: 'geojson', data: empty() });

      // Replacement route, drawn beneath the closure so the closure stays legible.
      map.addLayer({
        id: 'detour-fwd-line',
        type: 'line',
        source: 'detour-fwd',
        paint: { 'line-color': '#23d18b', 'line-width': 5, 'line-opacity': 0.9 },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      });
      map.addLayer({
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
      });
      map.addLayer({
        id: 'closure-halo',
        type: 'line',
        source: 'closure',
        paint: { 'line-color': '#ff4d4f', 'line-width': 14, 'line-opacity': 0.25, 'line-blur': 4 },
      });
      map.addLayer({
        id: 'closure-line',
        type: 'line',
        source: 'closure',
        paint: { 'line-color': '#ff4d4f', 'line-width': 6 },
        layout: { 'line-cap': 'round' },
      });

      readyRef.current = true;
      map.getCanvas().style.cursor = 'crosshair';
      // Dev-only handle so the map can be inspected from the console.
      if (import.meta.env.DEV) (window as any).__map = map;
    });

    let hovered = -1;
    map.on('mousemove', 'network-hit', (e) => {
      const f = e.features?.[0];
      if (!f) return;
      const id = f.properties?.linkId as number;
      if (id !== hovered) {
        hovered = id;
        map.setFilter('network-hover', ['==', ['get', 'linkId'], id]);
      }
    });
    map.on('mouseleave', 'network-hit', () => {
      hovered = -1;
      map.setFilter('network-hover', ['==', ['get', 'linkId'], -1]);
    });
    map.on('click', 'network-hit', (e) => {
      const f = e.features?.[0];
      if (f?.properties?.linkId !== undefined) pickRef.current(Number(f.properties.linkId));
    });

    return () => {
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
    };
    // Mount once. See pickRef above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Push the current result onto the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (!readyRef.current) return;
      const closure = map.getSource('closure') as maplibregl.GeoJSONSource;
      const fwd = map.getSource('detour-fwd') as maplibregl.GeoJSONSource;
      const rev = map.getSource('detour-rev') as maplibregl.GeoJSONSource;
      if (!closure || !fwd || !rev) return;

      if (!detour) {
        closure.setData(empty());
        fwd.setData(empty());
        rev.setData(empty());
        return;
      }
      closure.setData(detour.closure.geoJson as any);
      fwd.setData((detour.forward?.routeGeoJson ?? empty()) as any);
      rev.setData((detour.reverse?.routeGeoJson ?? empty()) as any);

      if (detour.fitBounds) {
        const [w, s, e, n] = detour.fitBounds;
        map.fitBounds([[w, s], [e, n]], { padding: 90, maxZoom: 16, duration: 700 });
      }
    };
    if (readyRef.current) apply();
    else map.once('idle', apply);
  }, [detour]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    if (map.getLayer('detour-rev-line')) {
      map.setLayoutProperty(
        'detour-rev-line',
        'visibility',
        showCorridor ? 'visible' : 'none',
      );
    }
  }, [showCorridor]);

  return (
    <div className="map">
      <div id="map" ref={ref} />
      <div className="legend">
        <div className="row">
          <span className="sw" style={{ background: '#ff4d4f' }} /> Closed link
        </div>
        <div className="row">
          <span className="sw" style={{ background: '#23d18b' }} /> Detour (forward)
        </div>
        <div className="row">
          <span className="sw" style={{ background: '#ffb020' }} /> Detour (reverse)
        </div>
        <div className="row">
          <span className="sw" style={{ background: '#7aa7d9' }} /> State highway
        </div>
        <div className="row">
          <span className="sw" style={{ background: '#3d4a5c' }} /> Outside analysis area
        </div>
      </div>
      {(!LINZ_KEY || LINZ_KEY === 'replace_me') && (
        <div className="attribution">
          No LINZ Basemaps API key set - showing the road network without a
          background map. Add <code>VITE_LINZ_API_KEY</code> to <code>.env</code>.
        </div>
      )}
    </div>
  );
}

function empty(): GeoJSON.FeatureCollection {
  return { type: 'FeatureCollection', features: [] };
}
