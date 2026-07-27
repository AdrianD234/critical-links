/**
 * The route merge is what makes the MapLibre reveal possible, and it is the
 * one place where a wrong assumption about the API's contract would show up as
 * a visibly broken route rather than an exception.
 */

import { describe, expect, it } from 'vitest';

import {
  mergeRouteToLineString,
  revealGradient,
} from '../../apps/web/src/map/route.js';

function arc(
  order: number,
  coords: [number, number][],
): GeoJSON.Feature<GeoJSON.LineString> {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: coords },
    properties: { order, arcId: 1000 + order },
  };
}

function fc(
  ...features: GeoJSON.Feature[]
): GeoJSON.FeatureCollection {
  return { type: 'FeatureCollection', features };
}

describe('mergeRouteToLineString', () => {
  it('returns nothing for an absent or empty route', () => {
    expect(mergeRouteToLineString(null).feature).toBeNull();
    expect(mergeRouteToLineString(undefined).feature).toBeNull();
    expect(mergeRouteToLineString(fc()).feature).toBeNull();
  });

  it('concatenates in travel order and drops the duplicated joint', () => {
    const merged = mergeRouteToLineString(
      fc(
        arc(0, [
          [174.7, -41.2],
          [174.71, -41.21],
        ]),
        arc(1, [
          [174.71, -41.21],
          [174.72, -41.22],
        ]),
        arc(2, [
          [174.72, -41.22],
          [174.73, -41.23],
        ]),
      ),
    );

    /* Three two-point arcs sharing two joints: 6 vertices minus 2 duplicates. */
    expect(merged.feature?.geometry.coordinates).toEqual([
      [174.7, -41.2],
      [174.71, -41.21],
      [174.72, -41.22],
      [174.73, -41.23],
    ]);
    expect(merged.hasGaps).toBe(false);
    expect(merged.skipped).toBe(0);
  });

  it('sorts on the order property rather than array position', () => {
    /* `order` is the authoritative statement of sequence. If a transport or
     * caching layer ever reorders the array, travel order must survive it. */
    const merged = mergeRouteToLineString(
      fc(
        arc(2, [
          [2, 0],
          [3, 0],
        ]),
        arc(0, [
          [0, 0],
          [1, 0],
        ]),
        arc(1, [
          [1, 0],
          [2, 0],
        ]),
      ),
    );

    expect(merged.feature?.geometry.coordinates).toEqual([
      [0, 0],
      [1, 0],
      [2, 0],
      [3, 0],
    ]);
    expect(merged.hasGaps).toBe(false);
  });

  it('flags a gap when consecutive arcs do not meet', () => {
    /* A gap means the backend stopped orienting arcs in travel direction, or
     * the route references geometry the graph disagrees about. The line still
     * draws — but the caller must not call it continuous. */
    const merged = mergeRouteToLineString(
      fc(
        arc(0, [
          [0, 0],
          [1, 0],
        ]),
        arc(1, [
          [5, 5],
          [6, 5],
        ]),
      ),
    );

    expect(merged.hasGaps).toBe(true);
    expect(merged.feature?.geometry.coordinates).toHaveLength(4);
  });

  it('skips unusable geometry instead of throwing', () => {
    const merged = mergeRouteToLineString(
      fc(
        arc(0, [
          [0, 0],
          [1, 0],
        ]),
        {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [9, 9] },
          properties: { order: 1 },
        },
        arc(2, [
          [1, 0],
          [2, 0],
        ]),
      ),
    );

    expect(merged.skipped).toBe(1);
    expect(merged.feature?.geometry.coordinates).toEqual([
      [0, 0],
      [1, 0],
      [2, 0],
    ]);
  });

  it('handles a single arc', () => {
    const merged = mergeRouteToLineString(
      fc(
        arc(0, [
          [0, 0],
          [1, 1],
        ]),
      ),
    );
    expect(merged.feature?.geometry.coordinates).toEqual([
      [0, 0],
      [1, 1],
    ]);
  });
});

describe('revealGradient', () => {
  /* MapLibre rejects a whole style expression if interpolation stops are not
   * strictly increasing, and a rejected expression means no route at all. */
  function stopsOf(expr: unknown[]): number[] {
    const stops: number[] = [];
    for (let i = 3; i < expr.length; i += 2) stops.push(expr[i] as number);
    return stops;
  }

  it('produces strictly increasing stops across the whole range', () => {
    for (let t = 0; t <= 1.0001; t += 0.005) {
      const stops = stopsOf(revealGradient('#2de1c2', t));
      for (let i = 1; i < stops.length; i++) {
        expect(
          stops[i]! > stops[i - 1]!,
          `stops not increasing at t=${t}: ${stops.join(', ')}`,
        ).toBe(true);
      }
    }
  });

  it('is fully transparent at the start and solid at the end', () => {
    expect(revealGradient('#2de1c2', 0)).toEqual([
      'interpolate',
      ['linear'],
      ['line-progress'],
      0,
      'rgba(0, 0, 0, 0)',
      1,
      'rgba(0, 0, 0, 0)',
    ]);
    expect(revealGradient('#2de1c2', 1)).toEqual([
      'interpolate',
      ['linear'],
      ['line-progress'],
      0,
      '#2de1c2',
      1,
      '#2de1c2',
    ]);
  });

  it('clamps out-of-range progress rather than emitting invalid stops', () => {
    expect(stopsOf(revealGradient('#2de1c2', -3))).toEqual([0, 1]);
    expect(stopsOf(revealGradient('#2de1c2', 42))).toEqual([0, 1]);
  });
});
