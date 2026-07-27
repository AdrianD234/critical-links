/*
 * What is cut off, when there is no replacement path.
 *
 * NO AFFECTED-AREA POLYGON. The engine identifies a set of *links* that lose
 * connectivity; it does not compute a service area, a catchment or a population
 * footprint. A hull drawn around those links would read as "this area is cut
 * off" — a claim the analysis does not make, and one that would enclose
 * properties still reachable by roads outside the stranded set.
 *
 * So the extent is communicated by the two figures below and by the amber
 * stranded links on the map, which claim exactly what was computed.
 *
 * `bounded` and `exact` are surfaced rather than hidden: a pocket the search
 * stopped enumerating is a lower bound, and presenting a lower bound as a
 * measurement would be the same category of overclaim.
 */

import { count, distance } from '../lib/format.js';
import type { Isolation } from '../api/types.js';

export default function IsolationPanel({ isolation }: { isolation: Isolation }) {
  const len = distance(isolation.pocketLengthM);
  const approx = !isolation.exact || !isolation.bounded;
  const drawn = Boolean(isolation.linkGeoJson?.features?.length);

  return (
    <div className="stranded">
      <div className="lab">Cut off by this closure</div>
      <div className="figs">
        <div className="fig">
          <div className="n tnum">
            {approx ? 'at least ' : ''}
            {count(isolation.pocketLinkCount)}
          </div>
          <div className="t">links stranded</div>
        </div>
        <div className="fig">
          <div className="n tnum">
            {len ? `${len.value} ${len.unit}` : '—'}
          </div>
          <div className="t">of road</div>
        </div>
      </div>
      <p className="note">
        {/* The claim that they are on the map must only be made when they are.
          * A large stranded set is reported without geometry, and saying "shown
          * in amber" then sends the reader looking for something absent. */}
        {drawn
          ? isolation.side === 'none'
            ? 'The stranded links are shown in amber on the map.'
            : `Stranded ${isolation.side} of the modelled closure, shown in ` +
              'amber on the map.'
          : 'Too many links to draw, so the extent is not shown on the map. ' +
            'The figures above are still exact.'}{' '}
        {approx
          ? 'The search was bounded, so these figures are a lower bound rather ' +
            'than a complete enumeration.'
          : 'This is the set of links that lose connectivity — not a service ' +
            'area or catchment.'}
      </p>
    </div>
  );
}
