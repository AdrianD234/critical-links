/**
 * Scenario options, and the adapter between the product's vocabulary and the
 * API's current one.
 *
 * WHY THIS EXISTS
 *
 * V1 accepts `closure_scope=physical|directed`. Neither name is what the
 * product means, and neither can express closing one *segment* of a road
 * rather than every link derived from one AMDS source feature.
 *
 * V2 can: it accepts `segment|direction|source_feature` and defaults to
 * `segment`. That is a third wire vocabulary, and the reason this module was
 * written the way it was. Components read and write `ClosureScope`; the two
 * mappings below are the only places that know what either engine calls it.
 *
 * The option *list* is derived from the engine's reported capabilities where
 * it offers them, falling back to a static description of what each backend
 * supports. A scope the active engine cannot honour is listed but disabled
 * with the reason shown, rather than silently missing — a control that quietly
 * lacks the option the user is looking for is worse than one that says "not
 * here, and here is why".
 */

import type { NetworkMetadata, V2Capabilities, V2ClosureScope } from './types.js';

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

/**
 * The V2 default, kept separate on purpose.
 *
 * V2 defaults to the segment the user actually selected, which is the question
 * they asked. V1 cannot answer that question, so its default above stays as it
 * is: a permalink made under V1 must keep resolving to the same closure and
 * therefore the same figures.
 */
export const DEFAULT_SCENARIO_V2: Scenario = {
  metric: 'distance',
  vehicle: 'car',
  closureScope: 'segment',
};

export interface OptionDescriptor<T extends string> {
  value: T;
  label: string;
  /** Short gloss shown in the expanded controls, not in the summary. */
  hint?: string;
  supported: boolean;
  /** Why it is unavailable. Shown to the user; required when unsupported. */
  unavailableReason?: string;
  /**
   * Correct, but rarely what is wanted. Ordered last and marked, so the
   * primary choice is the one the reader reaches first.
   */
  advanced?: boolean;
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

/**
 * The same mapping for V2, which names all three scopes.
 *
 * Separate functions rather than a widened pair, because the V1 mapping is
 * load-bearing for permalinks already in circulation: `segment` must still
 * return null there, so a V1 request for a scope V1 cannot honour fails loudly
 * instead of quietly closing more of the network than was asked.
 */
export function closureScopeToWireV2(scope: ClosureScope): V2ClosureScope {
  switch (scope) {
    case 'amds-feature':
      return 'source_feature';
    case 'direction':
      return 'direction';
    case 'segment':
      return 'segment';
  }
}

export function closureScopeFromWireV2(wire: string): ClosureScope {
  switch (wire) {
    case 'source_feature':
      return 'amds-feature';
    case 'direction':
      return 'direction';
    default:
      return 'segment';
  }
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
 * Why V1 cannot close a single segment.
 *
 * Kept as a named constant because it is a statement about one engine, not
 * about the product: under V2 it is simply false, and the two must not drift
 * into contradicting each other.
 */
const SEGMENT_UNAVAILABLE_V1 =
  'This engine removes every graph segment derived from one AMDS source ' +
  'feature, so it cannot close the selected segment on its own.';

/**
 * Closure scopes, in the order they should be read.
 *
 * Segment first: it is the closure the user pointed at, and under V2 it is the
 * default. AMDS source feature last and marked advanced, because it removes
 * more than was selected — an AMDS source feature is a data-maintenance unit
 * that may end where an authority's responsibility ends rather than where the
 * road does.
 *
 * An option the active engine cannot honour is listed but disabled with the
 * reason shown, rather than silently missing. A control that quietly lacks the
 * option the user is looking for is worse than one that says "not here, and
 * here is why".
 */
export function closureScopes(
  meta: NetworkMetadata | null,
  v2: V2Capabilities | null = null,
): OptionDescriptor<ClosureScope>[] {
  const all: OptionDescriptor<ClosureScope>[] = [
    {
      value: 'segment',
      label: 'Road segment',
      hint: 'The selected stretch of road, independent of AMDS boundaries',
      supported: false,
      unavailableReason: SEGMENT_UNAVAILABLE_V1,
    },
    {
      value: 'direction',
      label: 'Direction',
      hint: 'One direction of travel; the opposite carriageway stays open',
      supported: true,
    },
    {
      value: 'amds-feature',
      label: 'AMDS source feature',
      hint:
        'Every graph segment split from one AMDS source record — a ' +
        'data-maintenance unit, which may cover more road than was selected',
      supported: true,
      advanced: true,
    },
  ];

  /* V2 speaks for itself and is authoritative when it does. It names scopes in
   * its own vocabulary, so the comparison happens on the wire side. */
  if (v2) {
    return all.map((o) =>
      v2.closureScopes.includes(closureScopeToWireV2(o.value))
        ? { ...o, supported: true, unavailableReason: undefined }
        : {
            ...o,
            supported: false,
            unavailableReason:
              'Not offered by the V2 engine for this snapshot.',
          },
    );
  }

  const reported = meta?.capabilities?.closureScopes;
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

/* ------------------------------------------------- closure terminology */

/**
 * How the closure is described wherever it is named to the user.
 *
 * EVERY LABEL SAYS "MODELLED". Nothing in this tool observes a road being
 * closed — the user posits a closure and the engine answers a question about
 * the graph. Wording like "Closure active" or a map label reading "CLOSED" is
 * one screenshot away from being mistaken for a live road event, which is a
 * different product with different consequences for anyone acting on it.
 *
 * The label is also scope-aware rather than fixed, because what is removed
 * changes with the scope and "Closed segment" is simply wrong when the engine
 * removed an entire AMDS source feature.
 */
export function closureLabel(scope: ClosureScope): string {
  switch (scope) {
    case 'amds-feature':
      return 'Modelled AMDS source-feature closure';
    case 'direction':
      return 'Modelled one-direction closure';
    case 'segment':
      return 'Modelled segment closure';
  }
}

/** The same thing, short enough for a map badge. */
export function closureLabelShort(scope: ClosureScope): string {
  switch (scope) {
    case 'amds-feature':
      return 'Modelled closure — AMDS source feature';
    case 'direction':
      return 'Modelled closure — one direction';
    case 'segment':
      return 'Modelled closure — segment';
  }
}

/**
 * Read the scope back from a response.
 *
 * The response is authoritative over the control: it says what was actually
 * closed, which is what the label must describe.
 */
export function scopeOfResponse(wireScope: string): ClosureScope {
  return closureScopeFromWire(wireScope);
}

/** "Car · Distance · Road segment" — the compact sticky summary. */
export function summariseScenario(
  s: Scenario,
  meta: NetworkMetadata | null,
  v2: V2Capabilities | null = null,
): string {
  return [
    labelOf(VEHICLES, s.vehicle),
    labelOf(METRICS, s.metric),
    labelOf(closureScopes(meta, v2), s.closureScope),
  ].join(' · ');
}
