/**
 * Map view — the presentation mode of the ground the analysis is drawn on.
 *
 *   analysis  the quiet Graphite view - LINZ water, landcover and buildings
 *             only, so the analytical network is the only thing that reads as
 *             a road. The default.
 *   topo      LINZ streets and their names as well, for orientation. Shown to
 *             the user as "Streets": the label describes what appears on the
 *             map, not the cartographic product it comes from.
 *   aerial    LINZ orthophotography, with subdued place and street names but
 *             no LINZ street lines - the photography already shows the roads.
 *   off       the analytical network on the plain graphite ground.
 *
 * Every mode is PRESENTATION ONLY. Changing it never touches the closure, the
 * scenario, the result or the permalink, and it issues no analytical request:
 * the LINZ layers are context, never routed over, never clickable. That is why
 * the mode is remembered in localStorage rather than in the URL - a permalink
 * records an analysis, and the ground it is drawn on is not part of one.
 */

import { LYR } from '../map/style.js';

export type MapViewMode = 'analysis' | 'topo' | 'aerial' | 'off';

export const MAP_VIEW_MODES: readonly MapViewMode[] = [
  'analysis',
  'topo',
  'aerial',
  'off',
];

/** What each mode is called on screen. `topo` reads as "Streets". */
export const MAP_VIEW_LABELS: Record<MapViewMode, string> = {
  analysis: 'Analysis',
  topo: 'Streets',
  aerial: 'Aerial',
  off: 'Off',
};

/** The modes that cannot exist without a LINZ Basemaps key. */
export function requiresLinz(mode: MapViewMode): boolean {
  return mode === 'topo' || mode === 'aerial';
}

export const MAP_VIEW_STORAGE_KEY = 'nzcl.mapView';

/**
 * Whatever was stored, reduced to a mode this build can show.
 *
 * An unknown value falls back to Analysis rather than throwing or going blank:
 * the stored string is user state from a previous version of the app, not
 * input this version validated. A stored Streets or Aerial on a keyless build
 * falls back the same way, because restoring a mode whose layers do not exist
 * would show the Off ground while the selector claimed otherwise.
 */
export function sanitizeMapView(
  value: unknown,
  linzAvailable: boolean,
): MapViewMode {
  const mode = MAP_VIEW_MODES.find((m) => m === value);
  if (!mode) return 'analysis';
  if (!linzAvailable && requiresLinz(mode)) return 'analysis';
  return mode;
}

type ReadableStorage = Pick<Storage, 'getItem'>;
type WritableStorage = Pick<Storage, 'setItem'>;

function browserStorage(): Storage | null {
  /* localStorage can be absent (server-side, tests) or throw on access
   * (privacy modes). Either way the map view falls back to the default —
   * presentation state is never worth an error screen. */
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

export function loadMapView(
  linzAvailable: boolean,
  storage: ReadableStorage | null = browserStorage(),
): MapViewMode {
  try {
    return sanitizeMapView(storage?.getItem(MAP_VIEW_STORAGE_KEY), linzAvailable);
  } catch {
    return 'analysis';
  }
}

export function storeMapView(
  mode: MapViewMode,
  storage: WritableStorage | null = browserStorage(),
): void {
  try {
    storage?.setItem(MAP_VIEW_STORAGE_KEY, mode);
  } catch {
    /* A full or forbidden store loses persistence, nothing else. */
  }
}

/**
 * Which map layers each presentation state shows.
 *
 * One table rather than logic scattered through the map component, so the
 * claim "this mode shows exactly these layers" is a unit-testable fact. Only
 * CONTEXT layers and the user's own toggles appear here: the closure, the
 * route, the handles and every other analytical overlay are deliberately
 * absent, because no map view may touch them.
 */
export function mapViewLayerVisibility(state: {
  network: boolean;
  basemap: MapViewMode;
  labels: boolean;
}): Record<string, boolean> {
  const mode = state.basemap;
  /* The quiet vector context underlies both Analysis and Streets. Aerial
   * replaces it with photography — dark fills over imagery would be mud — and
   * Off shows the plain ground. */
  const quiet = mode === 'analysis' || mode === 'topo';
  return {
    [LYR.networkLine]: state.network,
    [LYR.aerial]: mode === 'aerial',
    [LYR.linzWater]: quiet,
    [LYR.linzLandcover]: quiet,
    [LYR.linzBuilding]: quiet,
    /* LINZ street lines are Streets-mode only. Over aerial photography they
     * would trace roads the imagery already shows. */
    [LYR.linzRoad]: mode === 'topo',
    /* Street names help orientation over both streets and photography. */
    [LYR.linzRoadLabel]: (mode === 'topo' || mode === 'aerial') && state.labels,
    /* Place names are LINZ context, so Off hides them too; the closure label
     * is exempt from all of this — it names the thing under analysis. */
    [LYR.linzPlaceLabel]: mode !== 'off' && state.labels,
    [LYR.networkLabel]: state.labels,
  };
}
