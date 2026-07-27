/**
 * Direction normalisation.
 *
 * THE BUG THIS EXISTS TO PREVENT
 *
 * The URL defaults to `reverse`. A one-way link has no reverse result, and the
 * Reverse tab is disabled for exactly that reason. Nothing previously moved the
 * focus, so a one-way road could load with reverse selected, reverse disabled,
 * no hero, no route, and — because every enabled tab had `tabIndex={-1}` under
 * the roving-tabindex rule — no keyboard-focusable tab either. A blank panel
 * with no way out.
 *
 * Kept out of the component and pure, because the interesting part is a small
 * decision table and it should be provable rather than clicked through.
 */

import type { DirectionKey } from '../api/scenario.js';
import type { DirectionView } from '../inspector/DirectionTabs.js';

export interface DirectionAvailability {
  forward: boolean;
  reverse: boolean;
}

export interface Normalised {
  view: DirectionView;
  /** True when the requested view was not available and was moved. */
  changed: boolean;
  /** Set when `changed`; a sentence for the live region. */
  announcement: string | null;
}

/**
 * Which directions a response actually represents.
 *
 * A direction is available when the response carries a result for it at all —
 * including a DISCONNECTED one. DISCONNECTED is a finding about that
 * direction, not an absence of it, and hiding the tab would misreport "this
 * direction has no replacement path" as "this direction does not exist".
 */
export function availabilityOf(result: {
  forward: unknown | null;
  reverse: unknown | null;
}): DirectionAvailability {
  return {
    forward: result.forward !== null && result.forward !== undefined,
    reverse: result.reverse !== null && result.reverse !== undefined,
  };
}

const OTHER: Record<DirectionKey, DirectionKey> = {
  forward: 'reverse',
  reverse: 'forward',
};

/**
 * Move the focus to something the response can actually show.
 *
 * Order of preference:
 *   1. the requested view, if it is available;
 *   2. the opposite direction, if that is available;
 *   3. whatever single direction exists;
 *   4. the request unchanged, when nothing is available — there is no better
 *      answer, and the caller renders the response's own failure state.
 *
 * Compare additionally requires both directions: comparing one direction
 * against nothing is not a comparison.
 */
export function normaliseDirection(
  requested: DirectionView,
  available: DirectionAvailability,
): Normalised {
  const unchanged = { view: requested, changed: false, announcement: null };

  if (requested === 'compare') {
    if (available.forward && available.reverse) return unchanged;
    const only: DirectionKey | null = available.forward
      ? 'forward'
      : available.reverse
        ? 'reverse'
        : null;
    if (!only) return unchanged;
    return {
      view: only,
      changed: true,
      announcement:
        `This link represents only the ${only} direction, so the two ` +
        `directions cannot be compared. Showing the ${only} direction.`,
    };
  }

  if (available[requested]) return unchanged;

  const other = OTHER[requested];
  if (available[other]) {
    return {
      view: other,
      changed: true,
      announcement:
        `This link is one-way: the ${requested} direction is not represented ` +
        `in this snapshot. Showing the ${other} direction instead.`,
    };
  }

  return unchanged;
}

/**
 * The tab that should carry `tabIndex={0}`.
 *
 * A roving-tabindex tablist gives exactly one tab the tab stop, and it must be
 * an enabled one — otherwise the whole control drops out of the keyboard order,
 * which is precisely what happened on one-way links.
 */
export function focusableTab(
  view: DirectionView,
  available: DirectionAvailability,
): DirectionView | null {
  const enabled: DirectionView[] = [
    ...(available.forward ? (['forward'] as const) : []),
    ...(available.reverse ? (['reverse'] as const) : []),
    ...(available.forward && available.reverse ? (['compare'] as const) : []),
  ];
  if (!enabled.length) return null;
  return enabled.includes(view) ? view : enabled[0]!;
}
