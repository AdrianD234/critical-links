/*
 * The through-trip comparison.
 *
 * Shown when the endpoint measure is undefined — routinely, on a one-way
 * carriageway, where there is no path from a link's end back to its start and
 * asking for one is an ill-posed question rather than a finding about the
 * network.
 *
 * The corridor answers the question the reader actually had: can traffic still
 * get past, and how much further is it. It is measured between the nearest
 * points upstream and downstream at which a driver has a choice of route, which
 * is why it is a fair comparison and the endpoint measure is not.
 */

import { distance } from '../lib/format.js';
import type { Corridor } from '../api/types.js';

export default function CorridorPanel({ corridor }: { corridor: Corridor }) {
  const normal = distance(corridor.normalDistanceM);
  const alt = distance(corridor.alternativeDistanceM);

  return (
    <div className="stranded">
      <div className="lab">Through-trip comparison</div>
      <div className="figs">
        <div className="fig">
          <div className="n tnum" style={{ color: 'var(--panel-fg)' }}>
            {normal ? `${normal.value} ${normal.unit}` : '—'}
          </div>
          <div className="t">normally</div>
        </div>
        <div className="fig">
          <div className="n tnum">{alt ? `${alt.value} ${alt.unit}` : '—'}</div>
          <div className="t">with the closure</div>
        </div>
      </div>
      <p className="note">
        Measured across {corridor.hopsUpstream} upstream and{' '}
        {corridor.hopsDownstream} downstream{' '}
        {corridor.hopsDownstream === 1 ? 'link' : 'links'}.{' '}
        {corridor.truncated
          ? 'The search was truncated, so this is a lower bound on the penalty.'
          : corridor.exitReachable
            ? 'Traffic can still get past the modelled closure.'
            : 'The downstream exit could not be reached within the search bound.'}
      </p>
    </div>
  );
}
