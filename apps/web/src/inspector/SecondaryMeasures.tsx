/*
 * The four measures under the hero: replacement path, detour multiplier, added
 * time and network penalty.
 *
 * A ruled list, not cards. Cards would chop four related figures into four
 * unrelated ones, and the reader's question is comparative — how does the
 * replacement compare to what was lost.
 *
 * Any measure whose value is unavailable renders a skeleton rather than a zero
 * or a dash-with-no-explanation, because a figure that no longer matches the
 * current controls is worse than no figure.
 */

import { duration, distance, metres, ratio } from '../lib/format.js';
import type { Metrics } from '../api/types.js';

export interface MeasureRow {
  key: string;
  label: string;
  value: string | null;
  unit: string;
  /** Marks a value derived from estimated speeds rather than measured geometry. */
  estimated?: boolean;
}

export function measureRows(m: Metrics | null): MeasureRow[] {
  const replacement = distance(m?.alternativeDistanceM ?? null);
  const added = duration(m?.addedTimeS ?? null);

  return [
    {
      key: 'replacement',
      label: 'Replacement path',
      value: replacement?.value ?? null,
      unit: replacement?.unit ?? 'km',
    },
    {
      key: 'multiplier',
      label: 'Detour multiplier',
      value: ratio(m?.detourRatioVsLink ?? null),
      unit: '×',
    },
    {
      key: 'time',
      label: 'Added time',
      value: added?.value ?? null,
      unit: added?.unit ?? 'min',
      /* AMDS publishes no speed attribute, so every time figure in this
       * product rests on an estimate. Marked at the value, not in a footnote:
       * a caveat that is not beside the number it qualifies is a caveat
       * nobody reads. */
      estimated: true,
    },
    {
      key: 'penalty',
      label: 'Network penalty',
      value: metres(m?.networkPenaltyM ?? null),
      unit: 'm',
    },
  ];
}

export default function SecondaryMeasures({
  rows,
  loading,
  revealKey,
}: {
  rows: MeasureRow[];
  loading: boolean;
  revealKey: string;
}) {
  return (
    <dl className="measures">
      {rows.map((r) => (
        <div className="measure" key={r.key}>
          <dt>{r.label}</dt>
          <dd>
            {loading || r.value === null ? (
              <span className="skeleton" />
            ) : (
              <span className="reveal tnum" key={`${revealKey}:${r.key}`}>
                {r.value}
                <span className="u">{r.unit}</span>
                {r.estimated && <span className="est-tag">Estimated</span>}
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
