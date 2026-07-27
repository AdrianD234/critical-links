/*
 * How the replacement path is composed.
 *
 * Behind disclosure, because it answers a follow-up question rather than the
 * first one. Roads are aggregated by name and ordered by contribution, since
 * "the detour is mostly SH58" is the useful reading — a flat list of 327 arcs
 * is not.
 */

import { distance, count } from '../lib/format.js';
import type { DirectionResult } from '../api/types.js';

interface Leg {
  name: string;
  metres: number;
  arcs: number;
}

export function legsOf(result: DirectionResult | null): Leg[] {
  const feats = result?.routeGeoJson?.features ?? [];
  const by = new Map<string, Leg>();

  for (const f of feats) {
    const p = (f.properties ?? {}) as Record<string, unknown>;
    const name = String(p.roadName || '(unnamed)');
    const m = Number(p.lengthM ?? 0);
    const leg = by.get(name);
    if (leg) {
      leg.metres += m;
      leg.arcs += 1;
    } else {
      by.set(name, { name, metres: m, arcs: 1 });
    }
  }

  return [...by.values()].sort((a, b) => b.metres - a.metres);
}

export default function RouteBreakdown({
  result,
}: {
  result: DirectionResult | null;
}) {
  const legs = legsOf(result);
  const total = legs.reduce((a, l) => a + l.metres, 0);

  if (!legs.length) {
    return (
      <p className="breakdown">
        No route geometry was returned for this direction.
      </p>
    );
  }

  return (
    <div className="breakdown">
      <div className="b-row">
        <span>
          {count(result?.routeLinkIds?.length ?? legs.length)} links across{' '}
          {legs.length} named {legs.length === 1 ? 'road' : 'roads'}
        </span>
        <span className="n">{fmt(total)}</span>
      </div>
      {legs.slice(0, 12).map((l) => (
        <div className="b-row" key={l.name}>
          <span>{l.name}</span>
          <span className="n">{fmt(l.metres)}</span>
        </div>
      ))}
      {legs.length > 12 && (
        /* Never silently truncate: say what was left out. */
        <div className="b-row">
          <span>
            &hellip; and {legs.length - 12} further{' '}
            {legs.length - 12 === 1 ? 'road' : 'roads'}
          </span>
          <span className="n">
            {fmt(legs.slice(12).reduce((a, l) => a + l.metres, 0))}
          </span>
        </div>
      )}
    </div>
  );
}

function fmt(m: number): string {
  const d = distance(m);
  return d ? `${d.value} ${d.unit}` : '—';
}
