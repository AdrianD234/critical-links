/*
 * Notices that qualify the figures currently on screen.
 *
 * Both of these describe a way the result may not be what the reader assumes,
 * and both are placed above the measures rather than in a disclosure, because a
 * caveat the reader has to go looking for is one they will not find.
 */

/**
 * The permalink was created against a different snapshot than the one active.
 *
 * The URL records a snapshot precisely so a historical link cannot silently
 * produce different numbers later. But the API has no snapshot parameter yet —
 * `/api/v1/links/{ref}/detour` always answers from the backend's active
 * snapshot — so the figures on screen were computed now, from today's network,
 * not restored from the one the link was made against.
 *
 * Recomputing is not itself wrong. Presenting the recomputed figures as though
 * they were the saved result would be. Until the API accepts a snapshot, the
 * only honest option is to say so plainly.
 */
export function SnapshotMismatch({
  requested,
  active,
}: {
  requested: string;
  active: string;
}) {
  return (
    <div className="notice notice--warn" role="status">
      <div className="notice-title">Not the snapshot this link was made from</div>
      <p>
        This link was created against snapshot <code>{requested}</code>, but{' '}
        <code>{active}</code> is active. The figures below were recalculated
        against the active snapshot — they have <b>not</b> been reproduced from
        the original, and may differ.
      </p>
    </div>
  );
}

/**
 * The route's arcs did not meet end to end.
 *
 * The map draws the contiguous parts and nothing between them, rather than a
 * merged line with an invented straight connector. The distance figures still
 * come from the graph and are unaffected — it is the drawn geometry that is
 * incomplete — and saying which is which matters.
 */
export function RouteGeometryGap({
  partCount,
  skippedArcs,
}: {
  partCount: number;
  skippedArcs: number;
}) {
  return (
    <div className="notice notice--warn" role="status">
      <div className="notice-title">Route geometry is not continuous</div>
      <p>
        The returned route breaks into {partCount} separate{' '}
        {partCount === 1 ? 'piece' : 'pieces'}
        {skippedArcs > 0
          ? `, and ${skippedArcs} ${
              skippedArcs === 1 ? 'arc has' : 'arcs have'
            } unusable geometry`
          : ''}
        . The map draws each piece and nothing between them, because a line
        across the break would not correspond to any road.
      </p>
      <p>
        The distances and multipliers are computed from the graph, not from this
        geometry, so they are unaffected.
      </p>
    </div>
  );
}

/**
 * The basemap failed to load.
 *
 * Non-blocking on purpose. The analytical network is served by our own backend
 * and is unaffected by a LINZ outage or an expired key, and the user needs to
 * know the missing context is the basemap rather than the analysis.
 */
export function BasemapUnavailable({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div className="map-notice" role="status">
      <span>
        Basemap unavailable — the LINZ key may have expired. The road network
        and all results are unaffected.
      </span>
      <button type="button" onClick={onDismiss} aria-label="Dismiss">
        Dismiss
      </button>
    </div>
  );
}
