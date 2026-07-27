/**
 * Number formatting.
 *
 * Every figure the inspector shows passes through here, so precision is a
 * decision made once rather than scattered through components. The rule is
 * that displayed precision must not exceed the precision the analysis actually
 * has: distances derive from measured NZTM geometry and are shown to the
 * nearest 10 m below 100 km, while times derive from *estimated* speeds and are
 * shown to the nearest minute, because a travel time claimed to the second
 * would imply a confidence that does not exist.
 */

const NZ = 'en-NZ';

/**
 * Distance, choosing the unit by magnitude. Returns value and unit apart so the
 * unit can be set in a different size and colour.
 *
 * Two precisions, deliberately, matching the approved design:
 *
 *   'headline' — one decimal. The hero answers "roughly how much further", and
 *                +53.48 km implies a resolution the reader does not need and
 *                the estimate does not warrant at a glance.
 *   'exact'    — two decimals. The replacement-path row is the figure someone
 *                writes down or checks against a batch export, so it keeps the
 *                digit the headline drops: 55.45 km, not 55.5 km.
 */
export type Precision = 'headline' | 'exact';

export function distance(
  m: number | null | undefined,
  precision: Precision = 'exact',
): { value: string; unit: string } | null {
  if (m === null || m === undefined || !Number.isFinite(m)) return null;
  if (Math.abs(m) < 1000) {
    return { value: Math.round(m).toLocaleString(NZ), unit: 'm' };
  }
  const km = m / 1000;
  const dp =
    precision === 'headline' ? 1 : Math.abs(km) >= 1000 ? 0 : 2;
  return {
    value: km.toLocaleString(NZ, {
      minimumFractionDigits: dp,
      maximumFractionDigits: dp,
    }),
    unit: 'km',
  };
}

/** A signed added-distance figure at headline precision: "+53.5". */
export function signedKm(m: number | null | undefined): {
  value: string;
  unit: string;
} | null {
  const d = distance(m, 'headline');
  if (!d) return null;
  const sign = (m ?? 0) > 0 ? '+' : '';
  return { value: `${sign}${d.value}`, unit: d.unit };
}

/**
 * Duration, to the nearest minute.
 *
 * Never seconds. The underlying speeds are estimated, and 3,376.3 s presented
 * as "56 min 16 s" would be a false claim about the analysis's resolution.
 */
export function duration(s: number | null | undefined): {
  value: string;
  unit: string;
} | null {
  if (s === null || s === undefined || !Number.isFinite(s)) return null;
  const mins = Math.round(s / 60);
  if (Math.abs(mins) < 60) return { value: String(mins), unit: 'min' };
  const h = Math.floor(Math.abs(mins) / 60);
  const rem = Math.abs(mins) % 60;
  const sign = mins < 0 ? '-' : '';
  return { value: `${sign}${h} h ${rem}`, unit: 'min' };
}

/** A detour multiplier: "28.14". */
export function ratio(r: number | null | undefined): string | null {
  if (r === null || r === undefined || !Number.isFinite(r)) return null;
  return r.toLocaleString(NZ, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** A plain metre figure with a thin space, for the network penalty. */
export function metres(m: number | null | undefined): string | null {
  if (m === null || m === undefined || !Number.isFinite(m)) return null;
  return Math.round(m).toLocaleString(NZ);
}

export function count(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return n.toLocaleString(NZ);
}

/** "1 971 m" — a link length, always in metres, for inline prose. */
export function inlineMetres(m: number | null | undefined): string {
  return m === null || m === undefined || !Number.isFinite(m)
    ? '—'
    : `${Math.round(m).toLocaleString(NZ)} m`;
}

/** ISO timestamp to something readable, in NZ local time. */
export function timestamp(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(NZ, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}
