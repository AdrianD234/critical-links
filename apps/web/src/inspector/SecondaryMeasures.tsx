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

import { duration, distance, ratio } from '../lib/format.js';
import type { Metrics } from '../api/types.js';

export interface MeasureRow {
  key: string;
  label: string;
  value: string | null;
  unit: string;
  /** Marks a value derived from estimated speeds rather than measured geometry. */
  estimated?: boolean;
  /** Shown on the em-dash when the measure does not apply to this result. */
  unavailableReason?: string;
}

export function measureRows(
  m: Metrics | null,
  status?: string,
): MeasureRow[] {
  /* Why these are blank, when they are blank. A dash with no explanation is
   * only marginally better than a permanent skeleton. */
  const why =
    status === 'DISCONNECTED'
      ? 'No replacement path exists between the link’s endpoints, so this ' +
        'measure is undefined for this result.'
      : undefined;

  return rowsFrom(m, why);
}

function rowsFrom(m: Metrics | null, why: string | undefined): MeasureRow[] {
  const replacement = distance(m?.alternativeDistanceM ?? null);
  const added = duration(m?.addedTimeS ?? null);
  /* Units must stay coherent down the column. A penalty of tens of kilometres
   * printed as "53,479 m" beside a hero of "+53.5 km" forces the reader to do
   * the conversion to see they describe the same magnitude. */
  const penalty = distance(m?.networkPenaltyM ?? null);

  return [
    {
      key: 'replacement',
      label: 'Replacement path',
      value: replacement?.value ?? null,
      unit: replacement?.unit ?? 'km',
      unavailableReason: why,
    },
    {
      key: 'multiplier',
      label: 'Detour multiplier',
      value: ratio(m?.detourRatioVsLink ?? null),
      unit: '×',
      unavailableReason: why,
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
      unavailableReason: why,
    },
    {
      key: 'penalty',
      label: 'Network penalty',
      value: penalty?.value ?? null,
      unit: penalty?.unit ?? 'km',
      unavailableReason: why,
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
            {loading ? (
              <span className="skeleton" />
            ) : r.value === null ? (
              /*
               * Not applicable, not still calculating.
               *
               * These were the same branch, so a DISCONNECTED result — where
               * every one of these metrics is legitimately null — left four
               * shimmer bars on screen forever, saying "working on it" about
               * numbers that will never arrive.
               */
              <span className="na" title={r.unavailableReason}>
                &mdash;
              </span>
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
