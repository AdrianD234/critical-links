/**
 * The editor's map behaviour: placing, dragging, and drawing.
 *
 * Owns its own sources, layers and handlers, and removes them when it goes
 * away. `NetworkMap` knows nothing about the span beyond handing over the map
 * once its style has loaded - which is what lets a build with the flag off
 * carry none of this.
 *
 * SNAPPING DURING A DRAG
 * ----------------------
 * The handle follows the centreline, not the pointer, so every movement needs
 * the server to resolve a position. That is cheap - about a millisecond
 * nationally - but a request per mouse event is still a request per mouse
 * event, so movements are throttled and the previous one is aborted. What is
 * NOT done here is analysis: the reducer holds that until the pointer lifts.
 */

import { useEffect, useRef } from 'react';
import type { Map as MapLibreMap, MapMouseEvent } from 'maplibre-gl';

import * as outage from '../api/outage.js';
import type { HandleId } from '../api/outage.js';
import type { Vehicle } from '../api/scenario.js';
import {
  SPAN_LYR,
  SPAN_SRC,
  handleFeatures,
  spanFeatures,
  spanLayers,
} from '../map/spanLayers.js';
import type { OutageSpanController } from './useOutageSpan.js';

/** How often a drag may ask the server where the centreline is. */
const DRAG_SNAP_MS = 60;

export function useSpanMap(
  map: MapLibreMap | null,
  controller: OutageSpanController,
  vehicle: Vehicle,
  enabled: boolean,
): void {
  /* Long-lived handlers read the current controller through a ref rather than
   * being torn down and rebuilt on every state change. */
  const ctl = useRef(controller);
  ctl.current = controller;
  const veh = useRef(vehicle);
  veh.current = vehicle;

  const dragging = useRef<HandleId | null>(null);
  const snapAbort = useRef<AbortController | null>(null);
  const lastSnap = useRef(0);

  /* --- sources, layers and interaction ------------------------------- */
  useEffect(() => {
    if (!map || !enabled) return;

    for (const id of Object.values(SPAN_SRC)) {
      if (!map.getSource(id)) {
        map.addSource(id, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      }
    }
    const hasGlyphs = Boolean(map.getStyle().glyphs);
    const layers = spanLayers(hasGlyphs);
    for (const layer of layers) if (!map.getLayer(layer.id)) map.addLayer(layer);

    const snapAt = async (lngLat: { lng: number; lat: number }) => {
      snapAbort.current?.abort();
      const ac = new AbortController();
      snapAbort.current = ac;
      try {
        return await outage.snap(lngLat.lng, lngLat.lat, veh.current, ac.signal);
      } catch {
        // A failed or aborted snap is not worth reporting: the pointer has
        // already moved, and the next one will answer.
        return null;
      }
    };

    const onClick = async (e: MapMouseEvent) => {
      if (dragging.current) return;
      const state = ctl.current.state;
      // First click places A, second places B, and after that a click moves
      // whichever handle is nearer - so the map stays usable without a mode
      // switch the user has to find.
      const which: HandleId =
        state.a === null ? 'a' : state.b === null ? 'b' : nearer(e, state);
      const result = await snapAt(e.lngLat);
      if (result) ctl.current.place(which, result);
    };

    const onDown = (e: MapMouseEvent & { features?: GeoJSON.Feature[] }) => {
      const which = e.features?.[0]?.properties?.handle as HandleId | undefined;
      if (!which) return;
      // Stop the map panning under the handle being dragged.
      e.preventDefault();
      dragging.current = which;
      ctl.current.dragStart(which);
      map.getCanvas().style.cursor = 'grabbing';
    };

    const onMove = async (e: MapMouseEvent) => {
      const which = dragging.current;
      if (!which) return;
      const now = Date.now();
      if (now - lastSnap.current < DRAG_SNAP_MS) return;
      lastSnap.current = now;
      const result = await snapAt(e.lngLat);
      if (result && dragging.current === which) ctl.current.dragMove(which, result);
    };

    const onUp = () => {
      if (!dragging.current) return;
      dragging.current = null;
      map.getCanvas().style.cursor = 'crosshair';
      ctl.current.dragEnd();
    };

    const onEnterHandle = () => {
      if (!dragging.current) map.getCanvas().style.cursor = 'grab';
    };
    const onLeaveHandle = () => {
      if (!dragging.current) map.getCanvas().style.cursor = 'crosshair';
    };

    map.on('click', onClick);
    map.on('mousedown', SPAN_LYR.handleDot, onDown);
    map.on('mousemove', onMove);
    map.on('mouseup', onUp);
    map.on('mouseenter', SPAN_LYR.handleDot, onEnterHandle);
    map.on('mouseleave', SPAN_LYR.handleDot, onLeaveHandle);

    return () => {
      snapAbort.current?.abort();
      map.off('click', onClick);
      map.off('mousedown', SPAN_LYR.handleDot, onDown);
      map.off('mousemove', onMove);
      map.off('mouseup', onUp);
      map.off('mouseenter', SPAN_LYR.handleDot, onEnterHandle);
      map.off('mouseleave', SPAN_LYR.handleDot, onLeaveHandle);
      for (const layer of layers) if (map.getLayer(layer.id)) map.removeLayer(layer.id);
      for (const id of Object.values(SPAN_SRC)) {
        if (map.getSource(id)) map.removeSource(id);
      }
    };
  }, [map, enabled]);

  /* --- what is drawn -------------------------------------------------- */
  useEffect(() => {
    if (!map || !enabled || !map.getSource(SPAN_SRC.handles)) return;
    const s = controller.state;

    setData(map, SPAN_SRC.handles, handleFeatures(
      s.a?.handle ?? null,
      s.b?.handle ?? null,
      s.dragging,
    ));

    setData(map, SPAN_SRC.span, spanFeatures(
      s.analysis?.closureGeometry ?? null,
      s.previewStale,
    ));

    /* Only a measure that RESOLVED contributes a line. A withheld route still
     * has arcs, and drawing them would put a replacement path on the map that
     * the engine has just refused to offer. */
    const drawn = s.analysis?.replacementGeometry?.[s.direction === 'b_to_a' ? 'b_to_a' : 'a_to_b'];
    setData(map, SPAN_SRC.replacement, drawn ?? { type: 'FeatureCollection', features: [] });
  }, [map, enabled, controller.state]);
}

function setData(map: MapLibreMap, id: string, data: unknown): void {
  const source = map.getSource(id);
  if (source && 'setData' in source) {
    (source as { setData: (d: unknown) => void }).setData(data);
  }
}

/** Which handle a click is closer to, in screen space. */
function nearer(
  e: MapMouseEvent,
  state: { a: { handle: { lon: number; lat: number } } | null;
           b: { handle: { lon: number; lat: number } } | null },
): HandleId {
  if (!state.a) return 'a';
  if (!state.b) return 'b';
  const d = (h: { lon: number; lat: number }) =>
    (h.lon - e.lngLat.lng) ** 2 + (h.lat - e.lngLat.lat) ** 2;
  return d(state.a.handle) <= d(state.b.handle) ? 'a' : 'b';
}
