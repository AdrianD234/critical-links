/**
 * Turning a route's arcs into something MapLibre can animate.
 *
 * WHY A MERGE IS NECESSARY
 *
 * The focused route is revealed by animating a `line-gradient` stop. MapLibre
 * drives `line-gradient` from `line-progress`, which it only computes when the
 * source sets `lineMetrics: true` — and it measures progress along a *single*
 * feature. A 327-feature collection therefore has 327 independent progress
 * ramps, and every arc would reveal simultaneously from its own start. Merging
 * to one LineString is what makes "draws in travel order" mean anything.
 *
 * WHY THE MERGE IS TRIVIAL
 *
 * The API has already done the hard part. `/api/v1/links/{ref}/detour` returns
 * route arcs in path order with an explicit `order` property, and each arc's
 * geometry is emitted already oriented in travel direction — the SQL applies
 * `ST_Reverse(l.geom_4326)` when `a.direction = 'reverse'`.
 *
 * So this is a concatenation, not a reconstruction. There is no geometric
 * snapping, no distance tolerance, and no re-derivation of direction on the
 * client. `order` is still sorted on rather than trusting array position,
 * because it is the authoritative statement of sequence and sorting on it is
 * free.
 */

export interface MergeResult {
  /** One LineString in travel order, or null if there was nothing to merge. */
  feature: GeoJSON.Feature<GeoJSON.LineString> | null;
  /** Arcs whose geometry was unusable, for diagnostics. */
  skipped: number;
  /**
   * True when consecutive arcs did not actually meet.
   *
   * A gap means an assumption above has been violated — most likely the
   * backend stopped orienting arcs, or the route contains a link the tile and
   * geometry endpoints disagree about. The merge still produces a drawable
   * line, but the caller should not present it as a continuous path without
   * looking into it.
   */
  hasGaps: boolean;
}

/** Positions within this distance are the same vertex, in degrees (~1 mm). */
const JOINT_EPSILON = 1e-8;

function samePoint(a: GeoJSON.Position, b: GeoJSON.Position): boolean {
  return (
    Math.abs(a[0] - b[0]) < JOINT_EPSILON && Math.abs(a[1] - b[1]) < JOINT_EPSILON
  );
}

function coordsOf(g: GeoJSON.Geometry | null): GeoJSON.Position[] | null {
  if (!g) return null;
  if (g.type === 'LineString') return g.coordinates.length >= 2 ? g.coordinates : null;
  /* Defensive: a MultiLineString here would mean the backend changed shape.
   * Flatten rather than drop, so a route still draws. */
  if (g.type === 'MultiLineString') {
    const flat = g.coordinates.flat();
    return flat.length >= 2 ? flat : null;
  }
  return null;
}

/**
 * Merge an ordered route collection into a single LineString.
 *
 * The duplicated joint vertex is dropped: arc N's last coordinate and arc N+1's
 * first coordinate are the same node, and keeping both would put a zero-length
 * segment into the geometry, which skews `line-progress` very slightly and
 * serves no purpose.
 */
export function mergeRouteToLineString(
  collection: GeoJSON.FeatureCollection | null | undefined,
): MergeResult {
  if (!collection?.features?.length) {
    return { feature: null, skipped: 0, hasGaps: false };
  }

  const ordered = [...collection.features].sort((a, b) => {
    const ao = Number(a.properties?.order ?? 0);
    const bo = Number(b.properties?.order ?? 0);
    return ao - bo;
  });

  const coords: GeoJSON.Position[] = [];
  let skipped = 0;
  let hasGaps = false;

  for (const f of ordered) {
    const c = coordsOf(f.geometry as GeoJSON.Geometry);
    if (!c) {
      skipped++;
      continue;
    }
    if (coords.length === 0) {
      coords.push(...c);
      continue;
    }
    const tail = coords[coords.length - 1]!;
    if (samePoint(tail, c[0]!)) {
      /* Normal case: drop the duplicated joint. */
      coords.push(...c.slice(1));
    } else {
      hasGaps = true;
      coords.push(...c);
    }
  }

  if (coords.length < 2) {
    return { feature: null, skipped, hasGaps };
  }

  return {
    feature: {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: coords },
      properties: {},
    },
    skipped,
    hasGaps,
  };
}

/**
 * The `line-gradient` expression for a reveal that has progressed to `t`.
 *
 * The stops must be strictly increasing or MapLibre rejects the expression
 * outright, which is why the leading stop is clamped an epsilon below `t`
 * rather than simply being `t`. At t=0 nothing is drawn; at t=1 the whole
 * route is solid.
 */
export function revealGradient(
  colour: string,
  t: number,
): unknown[] {
  const clamped = Math.max(0, Math.min(1, t));
  const lead = Math.max(clamped - 0.0001, 0);
  const transparent = 'rgba(0, 0, 0, 0)';

  /* At the extremes an interpolation is unnecessary and the near-duplicate
   * stops risk rounding into equality. Return the flat cases explicitly. */
  if (clamped >= 1) {
    return ['interpolate', ['linear'], ['line-progress'], 0, colour, 1, colour];
  }
  if (clamped <= 0) {
    return [
      'interpolate',
      ['linear'],
      ['line-progress'],
      0,
      transparent,
      1,
      transparent,
    ];
  }

  return [
    'interpolate',
    ['linear'],
    ['line-progress'],
    0,
    colour,
    lead,
    colour,
    clamped,
    transparent,
    1,
    transparent,
  ];
}
