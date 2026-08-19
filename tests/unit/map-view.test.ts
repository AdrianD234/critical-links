/**
 * The map-view mode: what each mode shows, how the choice persists, and what
 * a build without a LINZ key may offer.
 *
 * The mode→layer table is a pure function on purpose, so "Aerial shows the
 * photography and hides the vector streets" is a fact this suite states
 * exactly, rather than behaviour reconstructed by clicking through a browser.
 */

import { describe, expect, it } from 'vitest';

import { AERIAL_TILES, LYR } from '../../apps/web/src/map/style.js';
import {
  MAP_VIEW_LABELS,
  MAP_VIEW_MODES,
  MAP_VIEW_STORAGE_KEY,
  loadMapView,
  mapViewLayerVisibility,
  requiresLinz,
  sanitizeMapView,
  storeMapView,
  type MapViewMode,
} from '../../apps/web/src/state/mapView.js';

const state = (basemap: MapViewMode, over: Partial<{ network: boolean; labels: boolean }> = {}) => ({
  network: true,
  labels: true,
  basemap,
  ...over,
});

describe('mode → layer visibility', () => {
  it('Analysis shows the quiet vector context and nothing that reads as a road', () => {
    const v = mapViewLayerVisibility(state('analysis'));
    expect(v[LYR.linzWater]).toBe(true);
    expect(v[LYR.linzLandcover]).toBe(true);
    expect(v[LYR.linzBuilding]).toBe(true);
    expect(v[LYR.linzRoad]).toBe(false);
    expect(v[LYR.linzRoadLabel]).toBe(false);
    expect(v[LYR.aerial]).toBe(false);
    expect(v[LYR.linzPlaceLabel]).toBe(true);
  });

  it('Streets adds the LINZ street lines and their names over the quiet context', () => {
    const v = mapViewLayerVisibility(state('topo'));
    expect(v[LYR.linzRoad]).toBe(true);
    expect(v[LYR.linzRoadLabel]).toBe(true);
    expect(v[LYR.linzWater]).toBe(true);
    expect(v[LYR.aerial]).toBe(false);
  });

  it('Aerial shows the photography, keeps subdued names, and hides the vector street lines', () => {
    const v = mapViewLayerVisibility(state('aerial'));
    expect(v[LYR.aerial]).toBe(true);
    /* The photography already shows the roads; tracing them again in vector
     * would be noise. Names stay, for orientation. */
    expect(v[LYR.linzRoad]).toBe(false);
    expect(v[LYR.linzRoadLabel]).toBe(true);
    expect(v[LYR.linzPlaceLabel]).toBe(true);
    /* Dark vector fills under photography would be mud. */
    expect(v[LYR.linzWater]).toBe(false);
    expect(v[LYR.linzLandcover]).toBe(false);
    expect(v[LYR.linzBuilding]).toBe(false);
  });

  it('Off shows no LINZ contextual layer at all', () => {
    const v = mapViewLayerVisibility(state('off'));
    for (const id of [
      LYR.aerial,
      LYR.linzWater,
      LYR.linzLandcover,
      LYR.linzBuilding,
      LYR.linzRoad,
      LYR.linzRoadLabel,
      LYR.linzPlaceLabel,
    ]) {
      expect(v[id], `${id} must be hidden in Off`).toBe(false);
    }
  });

  it('leaves the analytical network and its own labels to their own toggles, in every mode', () => {
    for (const mode of MAP_VIEW_MODES) {
      const v = mapViewLayerVisibility(state(mode));
      expect(v[LYR.networkLine], `network in ${mode}`).toBe(true);
      expect(v[LYR.networkLabel], `network labels in ${mode}`).toBe(true);
      const off = mapViewLayerVisibility(state(mode, { network: false, labels: false }));
      expect(off[LYR.networkLine]).toBe(false);
      expect(off[LYR.networkLabel]).toBe(false);
      expect(off[LYR.linzRoadLabel]).toBe(false);
    }
  });

  it('never touches an analysis overlay: the closure, routes and handles are not its to change', () => {
    for (const mode of MAP_VIEW_MODES) {
      const touched = Object.keys(mapViewLayerVisibility(state(mode)));
      for (const analytical of [
        LYR.closureLine,
        LYR.closureHalo,
        LYR.closureLabel,
        LYR.routeFocus,
        LYR.routeCompare,
        LYR.routeHit,
        LYR.corridor,
        LYR.stranded,
        LYR.networkHit,
        LYR.networkHover,
      ]) {
        expect(touched, `${mode} must not govern ${analytical}`).not.toContain(
          analytical,
        );
      }
    }
  });
});

describe('persistence and fallback', () => {
  const store = () => {
    const bag = new Map<string, string>();
    return {
      getItem: (k: string) => bag.get(k) ?? null,
      setItem: (k: string, v: string) => void bag.set(k, v),
    };
  };

  it('defaults to Analysis when nothing is stored', () => {
    expect(loadMapView(true, store())).toBe('analysis');
    expect(loadMapView(true, null)).toBe('analysis');
  });

  it('restores a stored mode', () => {
    const s = store();
    storeMapView('aerial', s);
    expect(s.getItem(MAP_VIEW_STORAGE_KEY)).toBe('aerial');
    expect(loadMapView(true, s)).toBe('aerial');
  });

  it('falls back to Analysis on an invalid stored value', () => {
    const s = store();
    s.setItem(MAP_VIEW_STORAGE_KEY, 'satellite');
    expect(loadMapView(true, s)).toBe('analysis');
    expect(sanitizeMapView(undefined, true)).toBe('analysis');
    expect(sanitizeMapView(42, true)).toBe('analysis');
  });

  it('falls back to Analysis when the stored mode needs a key this build lacks', () => {
    /* Restoring Aerial with no key would draw the Off ground while the
     * selector claimed Aerial. */
    expect(sanitizeMapView('aerial', false)).toBe('analysis');
    expect(sanitizeMapView('topo', false)).toBe('analysis');
    expect(sanitizeMapView('off', false)).toBe('off');
    expect(sanitizeMapView('analysis', false)).toBe('analysis');
  });

  it('treats a throwing storage as no storage', () => {
    const broken = {
      getItem: () => {
        throw new Error('denied');
      },
    };
    expect(loadMapView(true, broken)).toBe('analysis');
    expect(() =>
      storeMapView('off', {
        setItem: () => {
          throw new Error('full');
        },
      }),
    ).not.toThrow();
  });
});

describe('keyless availability', () => {
  it('only Streets and Aerial need a LINZ key; Analysis and Off never do', () => {
    expect(requiresLinz('topo')).toBe(true);
    expect(requiresLinz('aerial')).toBe(true);
    expect(requiresLinz('analysis')).toBe(false);
    expect(requiresLinz('off')).toBe(false);
  });
});

describe('naming and the aerial source', () => {
  it('names `topo` as Streets on screen, and never says "imagery"', () => {
    expect(MAP_VIEW_LABELS.topo).toBe('Streets');
    expect(MAP_VIEW_LABELS.analysis).toBe('Analysis');
    expect(MAP_VIEW_LABELS.aerial).toBe('Aerial');
    expect(MAP_VIEW_LABELS.off).toBe('Off');
    for (const label of Object.values(MAP_VIEW_LABELS)) {
      expect(label.toLowerCase()).not.toContain('imagery');
    }
  });

  it('commits the aerial tile template with no API key in it', () => {
    /* The key is appended at runtime by `linzUrl`. The committed template must
     * be exactly the public endpoint, query-free, so no literal key can ever
     * reach the repository. */
    expect(AERIAL_TILES).toBe(
      'https://basemaps.linz.govt.nz/v1/tiles/aerial/WebMercatorQuad/{z}/{x}/{y}.webp',
    );
    expect(AERIAL_TILES).not.toContain('api=');
    expect(AERIAL_TILES).not.toContain('?');
  });
});
