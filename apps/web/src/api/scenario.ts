/**
 * Scenario options, and the adapter between the product's vocabulary and the
 * API's current one.
 *
 * WHY THIS EXISTS
 *
 * The backend today accepts `closure_scope=physical|directed`. Neither name is
 * what the product means, and a third scope — closing one *segment* of a road
 * rather than every link derived from one AMDS source feature — is planned but
 * not implemented.
 *
 * If components read and wrote the API enum directly, landing segment scope
 * would mean touching every control, every query key, every URL reader and the
 * inspector copy. So the UI speaks `ClosureScope` and this module is the only
 * place that knows how it maps onto the wire.
 *
 * The option *list* is derived from API-reported capabilities where the API
 * offers them, falling back to a static description of what the current
 * backend supports. A scope the backend cannot yet honour is listed but
 * disabled with the reason shown, rather than silently missing — a control
 * that quietly lacks the option the user is looking for is worse than one that
 * says "not yet".
 */

import type { NetworkMetadata } from './types.js';

export type Metric = 'distance' | 'time';
export type Vehicle = 'car' | 'heavy' | 'emergency';
export type DirectionKey = 'forward' | 'reverse';

/** What the user is asking to close. */
export type ClosureScope =
  /** Every graph link derived from one AMDS source feature. */
  | 'amds-feature'
  /** One direction of travel over that feature; the opposite stays open. */
  | 'direction'
  /** One segment of a road, independent of how AMDS happens to split it. */
  | 'segment';

export interface Scenario {
  metric: Metric;
  vehicle: Vehicle;
  closureScope: ClosureScope;
}

export const DEFAULT_SCENARIO: Scenario = {
  metric: 'distance',
  vehicle: 'car',
  closureScope: 'amds-feature',
};

export interface OptionDescriptor<T extends string> {
  value: T;
  label: string;
  /** Short gloss shown in the expanded controls, not in the summary. */
  hint?: string;
  supported: boolean;
  /** Why it is unavailable. Shown to the user; required when unsupported. */
  unavailableReason?: string;
}

/* ------------------------------------------------------------------ wire */

/**
 * Map a product scope onto the API's `closure_scope` parameter.
 *
 * `segment` deliberately has no mapping: it must not silently degrade to
 * `physical`, which would close more of the network than the user asked and
 * report the result as if it were what they requested.
 */
export function closureScopeToWire(scope: ClosureScope): string | null {
  switch (scope) {
    case 'amds-feature':
      return 'physical';
    case 'direction':
      return 'directed';
    case 'segment':
      return null;
  }
}

export function closureScopeFromWire(wire: string): ClosureScope {
  return wire === 'directed' ? 'direction' : 'amds-feature';
}

/* ------------------------------------------------------------- options */

const METRICS: OptionDescriptor<Metric>[] = [
  { value: 'distance', label: 'Distance', hint: 'Shortest by network distance', supported: true },
  { value: 'time', label: 'Time', hint: 'Shortest by estimated travel time', supported: true },
];

const VEHICLES: OptionDescriptor<Vehicle>[] = [
  { value: 'car', label: 'Car', supported: true },
  { value: 'heavy', label: 'Heavy', hint: 'Excludes links closed to heavy vehicles', supported: true },
  { value: 'emergency', label: 'Emergency', hint: 'Includes emergency-only links', supported: true },
];

/**
 * Closure scopes for the current backend.
 *
 * Once the API reports its own supported scopes this reads them instead; until
 * then the fallback is explicit rather than assumed, and `segment` carries the
 * reason it cannot be selected.
 */
export function closureScopes(
  meta: NetworkMetadata | null,
): OptionDescriptor<ClosureScope>[] {
  const reported = meta?.capabilities?.closureScopes;

  const all: OptionDescriptor<ClosureScope>[] = [
    {
      value: 'segment',
      label: 'Segment',
      hint: 'One stretch of road, independent of AMDS feature boundaries',
      supported: false,
      unavailableReason:
        'Segment-level closure is not implemented in this snapshot. ' +
        'The engine currently removes whole AMDS source features.',
    },
    {
      value: 'direction',
      label: 'Direction',
      hint: 'One direction of travel; the opposite carriageway stays open',
      supported: true,
    },
    {
      value: 'amds-feature',
      label: 'AMDS feature',
      hint: 'Every link derived from one AMDS source feature',
      supported: true,
    },
  ];

  if (!reported) return all;

  /* The API is authoritative when it speaks. An option it does not list is
   * unavailable regardless of what this file assumed. */
  return all.map((o) =>
    reported.includes(o.value)
      ? { ...o, supported: true, unavailableReason: undefined }
      : {
          ...o,
          supported: false,
          unavailableReason:
            o.unavailableReason ??
            'Not supported by the active snapshot’s processing version.',
        },
  );
}

export function metrics(): OptionDescriptor<Metric>[] {
  return METRICS;
}

export function vehicles(): OptionDescriptor<Vehicle>[] {
  return VEHICLES;
}

/* -------------------------------------------------------------- display */

function labelOf<T extends string>(opts: OptionDescriptor<T>[], v: T): string {
  return opts.find((o) => o.value === v)?.label ?? v;
}

/** "Car · Distance · AMDS feature" — the compact sticky summary. */
export function summariseScenario(
  s: Scenario,
  meta: NetworkMetadata | null,
): string {
  return [
    labelOf(VEHICLES, s.vehicle),
    labelOf(METRICS, s.metric),
    labelOf(closureScopes(meta), s.closureScope),
  ].join(' · ');
}
