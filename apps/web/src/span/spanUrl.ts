/**
 * The outage span in the query string.
 *
 * Namespaced under `s*` and written only when a span exists, so a URL from
 * every other screen is byte-identical to what it was before this feature
 * existed. The editor is a draft behind a flag; it must not change the shape
 * of links people have already shared.
 *
 * WHAT IS STORED, AND WHY IT IS NOT A CLICK
 * -----------------------------------------
 * A handle is stored as `linkId:fraction` - a LINEAR REFERENCE. Storing the
 * click instead would re-snap on reopening, against whatever the map looked
 * like then, and a shared span would quietly describe a different piece of
 * road.
 *
 * The corridor id is stored too, and that is the part that is easy to think
 * unnecessary. Corridor selection can be genuinely ambiguous - two ways round
 * that the evidence does not separate - and without the pin a restored link is
 * free to rank them the other way and close a different road under the same
 * URL. The server refuses an id it no longer offers rather than substituting
 * one, so restoring either reproduces what was shared or says it cannot.
 *
 * RESTORATION IS ONE REQUEST
 * --------------------------
 * `/analysis` takes exactly what is stored here and returns the two handles in
 * full, so nothing has to be re-snapped to rebuild the editor. That is why the
 * URL does not carry coordinates, road names or equivalent hosts: they are all
 * derivable, and a URL that carried them could disagree with the server.
 */

import type { DirectionMode } from '../api/outage.js';
import type { Metric, Vehicle } from '../api/scenario.js';

export interface SpanUrlState {
  aLinkId: number;
  aFraction: number;
  bLinkId: number;
  bFraction: number;
  corridorId: string;
  direction: DirectionMode;
  vehicle: Vehicle;
  metric: Metric;
}

const DIRECTIONS: DirectionMode[] = ['both', 'a_to_b', 'b_to_a'];
const VEHICLES: Vehicle[] = ['car', 'heavy', 'emergency'];
const METRICS: Metric[] = ['distance', 'time'];

/** A handle reference, as `linkId:fraction`. */
function encodeHandle(linkId: number, fraction: number): string {
  // Nine places is about a nanometre on a kilometre - finer than anything the
  // interface can express, and short enough to stay readable in a URL.
  return `${linkId}:${fraction.toFixed(9)}`;
}

function decodeHandle(raw: string | null): { linkId: number; fraction: number } | null {
  if (!raw) return null;
  const [left, right] = raw.split(':');
  const linkId = Number(left);
  const fraction = Number(right);
  if (!Number.isInteger(linkId) || linkId < 0) return null;
  if (!Number.isFinite(fraction) || fraction < 0 || fraction > 1) return null;
  return { linkId, fraction };
}

function oneOf<T extends string>(raw: string | null, allowed: readonly T[]): T | null {
  return raw && (allowed as readonly string[]).includes(raw) ? (raw as T) : null;
}

/**
 * Read a span from a query string, or null.
 *
 * All of it or none of it. A URL carrying one handle, or handles with no
 * corridor, describes no span that can be restored faithfully - and half a
 * span silently completed from defaults is the failure this whole module
 * exists to prevent.
 */
export function readSpanUrl(search = window.location.search): SpanUrlState | null {
  const p = new URLSearchParams(search);
  if (p.get('span') !== '1') return null;

  const a = decodeHandle(p.get('sa'));
  const b = decodeHandle(p.get('sb'));
  const corridorId = p.get('sc');
  const direction = oneOf(p.get('sd'), DIRECTIONS);
  const vehicle = oneOf(p.get('vehicle'), VEHICLES);
  const metric = oneOf(p.get('metric'), METRICS);

  if (!a || !b || !corridorId || !direction) return null;

  return {
    aLinkId: a.linkId,
    aFraction: a.fraction,
    bLinkId: b.linkId,
    bFraction: b.fraction,
    corridorId,
    direction,
    vehicle: vehicle ?? 'car',
    metric: metric ?? 'distance',
  };
}

/**
 * The keys the span owns. Nothing else may be written by this module.
 *
 * `vehicle` and `metric` are deliberately NOT here. They belong to the
 * application's own URL state, which already writes them on every URL, and
 * having two writers for one parameter made the merge non-idempotent: the span
 * re-set them, so they kept their original position while the `s*` keys were
 * deleted and re-appended, and the same state produced a different string on
 * the second pass.
 *
 * That is not cosmetic. `writeSpanUrl` compares the built string against the
 * current one to decide whether to touch history at all, so a merge that
 * churned the ordering would have pushed a history entry on every redraw -
 * filling the Back stack during a drag, which is precisely what the push /
 * replace distinction exists to prevent.
 */
const SPAN_KEYS = ['span', 'sa', 'sb', 'sc', 'sd'] as const;

/** The span's own parameters, for merging into the application's URL. */
export function spanSearchParams(s: SpanUrlState): URLSearchParams {
  const p = new URLSearchParams();
  p.set('span', '1');
  p.set('sa', encodeHandle(s.aLinkId, s.aFraction));
  p.set('sb', encodeHandle(s.bLinkId, s.bFraction));
  p.set('sc', s.corridorId);
  p.set('sd', s.direction);
  return p;
}

/**
 * Merge a span into an existing query string, or strip it when there is none.
 *
 * Existing parameters are preserved untouched, so turning the editor on and
 * off does not disturb the link selection or scenario a reader already had.
 */
export function mergeSpanIntoSearch(
  search: string,
  span: SpanUrlState | null,
): string {
  const p = new URLSearchParams(search);
  for (const key of SPAN_KEYS) p.delete(key);
  if (span) {
    for (const [k, v] of spanSearchParams(span)) p.set(k, v);
  }
  const query = p.toString();
  return query ? `?${query}` : '';
}

/**
 * Write the span to history.
 *
 * `push` for a new span - something to go Back from. `replace` for a change to
 * the span already shown, so dragging a handle does not fill the history stack
 * with a step per pixel.
 */
export function writeSpanUrl(
  span: SpanUrlState | null,
  mode: 'push' | 'replace',
): void {
  const next = mergeSpanIntoSearch(window.location.search, span);
  if (next === window.location.search) return;
  window.history[mode === 'push' ? 'pushState' : 'replaceState'](null, '', next);
}
