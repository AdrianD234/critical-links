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
  /**
   * One LineString in travel order — but ONLY when the arcs actually met end
   * to end. Null when there was nothing to merge, or when a gap was found.
   *
   * Null-on-gap is deliberate and is the whole safety property of this module.
   * Concatenating across a gap produces a LineString whose two sides are joined
   * by a straight segment that exists in no dataset: GeoJSON has no way to say
   * "these points are not connected". The map would draw a confident line down
   * a route nobody can drive. A missing reveal animation is a cosmetic loss; an
   * invented road is a false statement about the network.
   */
  feature: GeoJSON.Feature<GeoJSON.LineString> | null;

  /**
   * The route split at every gap, each part internally contiguous.
   *
   * This is what to draw when `hasGaps` — it preserves every valid portion of
   * the route and draws nothing between them.
   */
  parts: GeoJSON.Feature<GeoJSON.LineString>[];

  /** Arcs whose geometry was unusable, for diagnostics. */
  skipped: number;

  /**
   * True when consecutive arcs did not actually meet.
   *
   * A gap means an assumption has been violated — most likely the backend
   * stopped orienting arcs in travel direction, or the route references a link
   * whose geometry and graph topology disagree. It is a data-quality finding
   * and the caller must surface it, not swallow it.
   */
  hasGaps: boolean;

  /** Where the breaks are, as indices into the ordered arc list. */
  gapAfter: number[];
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
  const nothing: MergeResult = {
    feature: null,
    parts: [],
    skipped: 0,
    hasGaps: false,
    gapAfter: [],
  };
  if (!collection?.features?.length) return nothing;

  const ordered = [...collection.features].sort((a, b) => {
    const ao = Number(a.properties?.order ?? 0);
    const bo = Number(b.properties?.order ?? 0);
    return ao - bo;
  });

  /* Accumulate into runs. A new run starts wherever the next arc does not
   * begin where the previous one ended, so no run ever spans a gap. */
  const runs: GeoJSON.Position[][] = [];
  let current: GeoJSON.Position[] = [];
  let skipped = 0;
  const gapAfter: number[] = [];

  ordered.forEach((f, i) => {
    const c = coordsOf(f.geometry as GeoJSON.Geometry);
    if (!c) {
      skipped++;
      return;
    }
    if (current.length === 0) {
      current = [...c];
      return;
    }
    const tail = current[current.length - 1]!;
    if (samePoint(tail, c[0]!)) {
      /* Normal case: drop the duplicated joint. */
      current.push(...c.slice(1));
    } else {
      gapAfter.push(i - 1);
      runs.push(current);
      current = [...c];
    }
  });
  if (current.length) runs.push(current);

  const parts = runs
    .filter((r) => r.length >= 2)
    .map<GeoJSON.Feature<GeoJSON.LineString>>((coordinates, i) => ({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates },
      properties: { part: i },
    }));

  if (!parts.length) return { ...nothing, skipped };

  const hasGaps = gapAfter.length > 0;

  return {
    /* One contiguous run: safe to animate. More than one: the caller must not
     * be handed anything it could mistake for a continuous path. */
    feature: hasGaps ? null : (parts[0] ?? null),
    parts,
    skipped,
    hasGaps,
    gapAfter,
  };
}

/**
 * One label anchor per closed road, as Points.
 *
 * WHY NOT LABEL THE LINE DIRECTLY
 *
 * A symbol layer with line or point placement over the closure LineStrings
 * looked correct at zoom 11 and rendered nothing at zoom 14. MapLibre re-tiles
 * a GeoJSON source per zoom level, and the anchor it derives from a clipped
 * LineString can land in a tile's buffer and be dropped from every tile that
 * could have drawn it. The failure is silent — no error, no warning, just no
 * label, and only at some zooms.
 *
 * A Point feature has one unambiguous tile home at every zoom, so this cannot
 * happen. Deriving the points here also dedupes: the closure carries one
 * feature per directed arc, so a two-way link would otherwise draw its name
 * twice, stacked.
 *
 * Anchors are grouped by road name rather than by link, so closing an AMDS
 * feature made of several links produces one label, not one per link.
 */
export function closureLabelPoints(
  closure: GeoJSON.FeatureCollection | null | undefined,
): GeoJSON.FeatureCollection {
  const empty: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: [],
  };
  if (!closure?.features?.length) return empty;

  /* Collect every vertex per road name, then anchor at the middle one. The
   * middle *vertex* rather than the centroid, so the label sits on the road
   * even when it curves — a centroid can fall off a horseshoe entirely. */
  const byName = new Map<string, GeoJSON.Position[]>();

  for (const f of closure.features) {
    const name = String(f.properties?.roadName ?? '').trim();
    if (!name) continue;
    const c = coordsOf(f.geometry as GeoJSON.Geometry);
    if (!c) continue;
    const existing = byName.get(name);
    if (existing) existing.push(...c);
    else byName.set(name, [...c]);
  }

  return {
    type: 'FeatureCollection',
    features: [...byName.entries()].map(([name, coords]) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: coords[Math.floor(coords.length / 2)]!,
      },
      properties: { roadName: name },
    })),
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
