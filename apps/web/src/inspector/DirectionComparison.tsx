/*
 * The forward/reverse comparison table.
 *
 * Deliberately not two identical stacked result cards — that is what the
 * previous build did, and it made the reader do the subtraction. One direction
 * is the focused teal route; the other is a held-back amber dashed line and a
 * column in this table.
 *
 * Monospace here is one of the few places it is warranted: these are figures
 * being compared digit against digit across two columns, which is exactly what
 * a monospace face is for. The hero and the measure list are not.
 */

import { distance, count, ratio } from '../lib/format.js';
import type { DirectionKey } from '../api/scenario.js';
import type { DirectionResult } from '../api/types.js';

function addedKm(d: DirectionResult | null): string {
  /* Headline precision, so the comparison column reads as the same figure the
   * hero shows rather than a slightly different one. */
  const v = distance(d?.metrics.addedDistanceVsLinkM ?? null, 'headline');
  if (!v) return '—';
  const sign = (d?.metrics.addedDistanceVsLinkM ?? 0) > 0 ? '+' : '';
  return `${sign}${v.value} ${v.unit}`;
}

export default function DirectionComparison({
  forward,
  reverse,
  focus,
}: {
  forward: DirectionResult | null;
  reverse: DirectionResult | null;
  focus: DirectionKey;
}) {
  const rows: { label: string; f: string; r: string }[] = [
    { label: 'Added distance', f: addedKm(forward), r: addedKm(reverse) },
    {
      label: 'Multiplier',
      f: fmtRatio(forward),
      r: fmtRatio(reverse),
    },
    {
      label: 'Route links',
      f: count(forward?.routeLinkIds?.length ?? null),
      r: count(reverse?.routeLinkIds?.length ?? null),
    },
  ];

  const other = focus === 'reverse' ? 'Forward' : 'Reverse';

  return (
    <div className="compare">
      <div className="ch">
        <span className="sw" aria-hidden="true" />
        <span>{other} direction, for comparison</span>
      </div>
      <table>
        <caption className="sr-only">
          Forward and reverse replacement paths compared
        </caption>
        <thead>
          <tr>
            <th scope="col">Measure</th>
            <th scope="col">Forward</th>
            <th scope="col">Reverse</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <th scope="row">{r.label}</th>
              <td data-focus={focus === 'forward'}>{r.f}</td>
              <td data-focus={focus === 'reverse'}>{r.r}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmtRatio(d: DirectionResult | null): string {
  const v = ratio(d?.metrics.detourRatioVsLink ?? null);
  return v === null ? '—' : `${v}×`;
}
