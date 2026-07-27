/**
 * The URL is the application's state.
 *
 * Every selection and setting is readable from and writable to the query
 * string, so any view is shareable and the browser's Back and Forward buttons
 * restore state rather than leaving the page.
 *
 * The snapshot id is recorded too. A permalink whose figures came from a
 * different snapshot than the one now active is not the same result, and the
 * app must be able to say so rather than silently recomputing and presenting
 * different numbers under the same link.
 *
 * `pushState` is used for a change of selection (a new thing to go Back from)
 * and `replaceState` for a change of scenario on the same link, which would
 * otherwise fill the history stack with every toggle of a radio button.
 */

import {
  DEFAULT_SCENARIO,
  closureScopeFromWire,
  type ClosureScope,
  type DirectionKey,
  type Metric,
  type Scenario,
  type Vehicle,
} from '../api/scenario.js';

export interface ExploreUrlState {
  link: string | null;
  scenario: Scenario;
  /** Which direction the inspector is focused on. */
  focus: DirectionKey;
  /** True when both routes are shown together. */
  compare: boolean;
  /** The snapshot the permalink was created against, if it recorded one. */
  snapshot: string | null;
}

const SCOPES: ClosureScope[] = ['amds-feature', 'direction', 'segment'];

function oneOf<T extends string>(
  raw: string | null,
  allowed: readonly T[],
  fallback: T,
): T {
  return raw && (allowed as readonly string[]).includes(raw)
    ? (raw as T)
    : fallback;
}

export function readUrl(search = window.location.search): ExploreUrlState {
  const p = new URLSearchParams(search);

  /*
   * `scope` accepts both vocabularies. Old links in circulation carry the API
   * enum (`physical`/`directed`); new ones carry the product scope. Reading
   * both means a permalink someone shared last month still resolves.
   */
  const rawScope = p.get('scope');
  const closureScope: ClosureScope =
    rawScope === 'physical' || rawScope === 'directed'
      ? closureScopeFromWire(rawScope)
      : oneOf(rawScope, SCOPES, DEFAULT_SCENARIO.closureScope);

  return {
    link: p.get('link'),
    scenario: {
      metric: oneOf<Metric>(
        p.get('metric'),
        ['distance', 'time'],
        DEFAULT_SCENARIO.metric,
      ),
      vehicle: oneOf<Vehicle>(
        p.get('vehicle'),
        ['car', 'heavy', 'emergency'],
        DEFAULT_SCENARIO.vehicle,
      ),
      closureScope,
    },
    focus: oneOf<DirectionKey>(p.get('focus'), ['forward', 'reverse'], 'reverse'),
    compare: p.get('compare') === '1',
    snapshot: p.get('snapshot'),
  };
}

export function buildSearch(s: ExploreUrlState): string {
  const p = new URLSearchParams();
  if (s.link) p.set('link', s.link);
  p.set('metric', s.scenario.metric);
  p.set('vehicle', s.scenario.vehicle);
  p.set('scope', s.scenario.closureScope);
  p.set('focus', s.focus);
  if (s.compare) p.set('compare', '1');
  if (s.snapshot) p.set('snapshot', s.snapshot);
  return `?${p.toString()}`;
}

export function writeUrl(s: ExploreUrlState, mode: 'push' | 'replace') {
  const next = buildSearch(s);
  if (next === window.location.search) return;
  const fn = mode === 'push' ? 'pushState' : 'replaceState';
  window.history[fn](null, '', next);
}

/** The absolute permalink for the current state. */
export function permalinkFor(s: ExploreUrlState): string | null {
  if (!s.link) return null;
  return `${window.location.origin}${window.location.pathname}${buildSearch(s)}`;
}
