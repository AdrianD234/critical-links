/**
 * The route merge is what makes the MapLibre reveal possible, and it is the
 * one place where a wrong assumption about the API's contract would show up as
 * a visibly broken route rather than an exception.
 */

import { describe, expect, it } from 'vitest';

import {
  closureLabelPoints,
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

  it('NEVER produces a merged line across a gap', () => {
    /*
     * The safety property of this module.
     *
     * Concatenating across a gap yields a LineString whose two sides are joined
     * by a straight segment present in no dataset — GeoJSON has no way to say
     * "these points are not connected" — and the map would draw a confident
     * line down a route nobody can drive. Losing the reveal animation is
     * cosmetic; drawing an invented road is a false statement about the network.
     */
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
    expect(merged.feature).toBeNull();
  });

  it('returns the contiguous parts so valid geometry is still drawn', () => {
    const merged = mergeRouteToLineString(
      fc(
        arc(0, [
          [0, 0],
          [1, 0],
        ]),
        arc(1, [
          [1, 0],
          [2, 0],
        ]),
        /* gap */
        arc(2, [
          [5, 5],
          [6, 5],
        ]),
      ),
    );

    expect(merged.parts).toHaveLength(2);
    expect(merged.parts[0]!.geometry.coordinates).toEqual([
      [0, 0],
      [1, 0],
      [2, 0],
    ]);
    expect(merged.parts[1]!.geometry.coordinates).toEqual([
      [5, 5],
      [6, 5],
    ]);
    expect(merged.gapAfter).toEqual([1]);
  });

  it('never joins two parts — no coordinate bridges a gap', () => {
    /* Stated as an invariant over the output rather than a specific shape: no
     * part may contain a vertex from the other side of a break. */
    const merged = mergeRouteToLineString(
      fc(
        arc(0, [
          [0, 0],
          [1, 0],
        ]),
        arc(1, [
          [50, 50],
          [51, 50],
        ]),
        arc(2, [
          [51, 50],
          [52, 50],
        ]),
      ),
    );

    for (const part of merged.parts) {
      const xs = part.geometry.coordinates.map((c) => c[0]!);
      const spread = Math.max(...xs) - Math.min(...xs);
      /* Each part is internally short; a bridged part would span ~50 degrees. */
      expect(spread).toBeLessThan(5);
    }
  });

  it('gives a single contiguous route one animatable feature', () => {
    const merged = mergeRouteToLineString(
      fc(
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
    expect(merged.hasGaps).toBe(false);
    expect(merged.feature).not.toBeNull();
    expect(merged.parts).toHaveLength(1);
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

describe('closureLabelPoints', () => {
  function closed(
    name: string,
    coords: [number, number][],
  ): GeoJSON.Feature<GeoJSON.LineString> {
    return {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: coords },
      properties: { role: 'closed', roadName: name },
    };
  }

  it('returns nothing for an absent or empty closure', () => {
    expect(closureLabelPoints(null).features).toEqual([]);
    expect(closureLabelPoints(fc()).features).toEqual([]);
  });

  it('emits exactly one anchor for a two-way link', () => {
    /* The closure carries one feature per directed arc, so a two-way link
     * arrives as two identical geometries. Without deduping, the road name
     * renders twice, stacked, and reads as a rendering fault. */
    const line: [number, number][] = [
      [174.9, -41.1],
      [174.91, -41.11],
      [174.92, -41.12],
    ];
    const out = closureLabelPoints(
      fc(closed('Moonshine Road', line), closed('Moonshine Road', line)),
    );

    expect(out.features).toHaveLength(1);
    expect(out.features[0]!.properties!.roadName).toBe('Moonshine Road');
    expect(out.features[0]!.geometry.type).toBe('Point');
  });

  it('emits one anchor per road, not per link', () => {
    const out = closureLabelPoints(
      fc(
        closed('Moonshine Road', [
          [0, 0],
          [1, 1],
        ]),
        closed('Moonshine Road', [
          [1, 1],
          [2, 2],
        ]),
        closed('Haywards Hill Road', [
          [5, 5],
          [6, 6],
        ]),
      ),
    );

    expect(out.features.map((f) => f.properties!.roadName).sort()).toEqual([
      'Haywards Hill Road',
      'Moonshine Road',
    ]);
  });

  it('anchors on a vertex of the road, not at a centroid', () => {
    /* A centroid can fall off a horseshoe entirely, putting the road's name in
     * a field beside it. Every anchor must be a point the road passes through. */
    const coords: [number, number][] = [
      [0, 0],
      [0, 10],
      [10, 10],
      [10, 0],
    ];
    const out = closureLabelPoints(fc(closed('Horseshoe Road', coords)));
    const anchor = (out.features[0]!.geometry as GeoJSON.Point).coordinates;

    expect(coords.some((c) => c[0] === anchor[0] && c[1] === anchor[1])).toBe(
      true,
    );
  });

  it('skips features with no road name rather than labelling them blank', () => {
    const out = closureLabelPoints(
      fc({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [
            [0, 0],
            [1, 1],
          ],
        },
        properties: { role: 'closed', roadName: '' },
      }),
    );
    expect(out.features).toEqual([]);
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
