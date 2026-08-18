/*
 * Topology sensitivity, rendered so it can never be mistaken for the answer.
 *
 * The canonical replacement path is above this block and is the product
 * answer. Everything here is an assumption: "IF this unresolved crossing were
 * an at-grade junction, the route would be X". The two are kept apart by
 * construction rather than by wording:
 *
 *   - the counterfactual figure never occupies the canonical slot and never
 *     borrows its styling. It is dashed, muted, and labelled "if assumed";
 *   - it is never handed to the map as a replacement path;
 *   - a response whose token does not match the current selection is
 *     DISCARDED. An older answer landing on a newer click is confidently
 *     wrong output that looks entirely normal, and react-query's cache key
 *     alone does not stop a slow response from a previous selection being
 *     rendered against the current one.
 *
 * The four resolved states are the server's, verbatim. "Checking..." is the
 * client's own, because only the client knows a request is in flight.
 */

import { distance } from '../lib/format.js';
import type { V2TopologySensitivity } from '../api/types.js';

export interface TopologySensitivityProps {
  data: V2TopologySensitivity | undefined;
  loading: boolean;
  error: Error | null;
  /** The selection this panel is currently showing. */
  token: string;
}

function Figure({ metres, kind }: { metres: number | null; kind: 'canonical' | 'assumed' }) {
  if (metres === null || metres === undefined) return <span className="ts-na">&mdash;</span>;
  const d = distance(metres);
  if (!d) return <span className="ts-na">&mdash;</span>;
  return (
    <span className={kind === 'canonical' ? 'ts-canonical-figure' : 'ts-assumed-figure'}>
      {d.value}
      <span className="ts-unit">{d.unit}</span>
    </span>
  );
}

export default function TopologySensitivity({
  data,
  loading,
  error,
  token,
}: TopologySensitivityProps) {
  if (loading) {
    return (
      <section className="panel ts-panel" data-testid="topology-sensitivity">
        <h3>Topology sensitivity</h3>
        <p className="ts-checking" data-testid="ts-state">
          Checking topology sensitivity&hellip;
        </p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="panel ts-panel" data-testid="topology-sensitivity">
        <h3>Topology sensitivity</h3>
        <p className="ts-unavailable" data-testid="ts-state">
          Sensitivity unavailable
        </p>
        <p className="ts-detail">{error.message}</p>
      </section>
    );
  }

  if (!data) return null;

  /* THE STALE-RESPONSE GUARD. The server echoes the token it was called with;
   * if it is not the selection on screen, this response is about a link the
   * user has already left. Render nothing rather than the wrong road. */
  if (data.token && data.token !== token) {
    return (
      <section className="panel ts-panel" data-testid="topology-sensitivity">
        <h3>Topology sensitivity</h3>
        <p className="ts-checking" data-testid="ts-state">
          Checking topology sensitivity&hellip;
        </p>
      </section>
    );
  }

  const changed = (data.counterfactuals ?? []).filter(
    (c) => c.individuallyChangesAnswer === true,
  );

  return (
    <section className="panel ts-panel" data-testid="topology-sensitivity">
      <h3>Topology sensitivity</h3>

      <p className="ts-state" data-testid="ts-state" data-state={data.state}>
        {data.message}
      </p>

      {data.capNote ? (
        <p className="ts-cap" data-testid="ts-cap">
          {data.capNote}
        </p>
      ) : null}

      {data.state === 'TOPOLOGY_SENSITIVE' && changed.length > 0 ? (
        <div className="ts-compare">
          <div className="ts-row ts-row-canonical" data-testid="ts-canonical">
            <span className="ts-label">Canonical represented route</span>
            <Figure metres={data.canonicalAnswer?.distanceM ?? null} kind="canonical" />
          </div>
          {changed.map((c) => (
            <div
              className="ts-row ts-row-assumed"
              data-testid="ts-assumed"
              key={c.assumedJunctionCrossingIds.join('-')}
            >
              <span className="ts-label">
                If assumed a junction:{' '}
                <strong data-testid="ts-crossing">
                  {c.assumedJunctions.map((j) => j.label ?? 'unnamed crossing').join(' and ')}
                </strong>
              </span>
              <Figure metres={c.distanceM} kind="assumed" />
            </div>
          ))}
          <p className="ts-caveat">
            The figure above is an assumption, not a measured route. The canonical answer
            is unchanged and is the one this tool reports.
          </p>
        </div>
      ) : null}

      {data.unavailableReason ? (
        <p className="ts-detail">{data.unavailableReason}</p>
      ) : null}
    </section>
  );
}
