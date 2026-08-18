/**
 * Which layer a replacement route is allowed to be drawn on.
 *
 * THE RULE THIS FILE EXISTS TO ENFORCE
 *
 * A route found on the POSSIBLE graph must never be drawn as the normal teal
 * replacement path.
 *
 * The POSSIBLE graph nodes the interior crossings that `nzcl.crossings` could
 * not resolve — crossings where the evidence says neither "junction" nor
 * "structure". It is a sensitivity instrument: it answers "would this result
 * change if the crossings we cannot resolve turned out to be junctions?" A
 * route that only exists because of that assumption is not the answer this
 * system publishes, and teal is the colour of the answer this system
 * publishes. Drawing one in the other's colour is not a styling slip; it is
 * presenting an unverified claim as a measured result.
 *
 * WHY IT IS A FUNCTION AND NOT A CONVENTION
 *
 * The obvious implementation is a `setPaintProperty` on the existing route
 * layer, and it is the wrong one. The reveal animation already re-sets that
 * layer's `line-gradient` from two different effects; a third writer with a
 * conditional colour would be one refactor away from a speculative route in
 * canonical teal, and nothing would fail — it would simply look right and be
 * wrong.
 *
 * So the choice is made ONCE, here, and it selects a source and a layer rather
 * than a colour. The speculative layer is fed from its own source and cannot
 * be reached by a route without provenance; the canonical layer is fed from
 * its own source and cannot be reached by a route with provenance. NetworkMap
 * calls this and clears whichever source it did not choose, and holds no other
 * reference to either layer id or to `palette.route` — which is asserted by
 * tests/unit/speculative-route.test.ts, because a rule enforced by a comment
 * is not enforced.
 */

import { LYR, SRC } from './style.js';
import { palette } from '../styles/palette.js';
import type { PossibleProvenance } from '../api/types.js';

/*
 * Only the PRESENCE of the provenance block is read below, deliberately. A
 * route whose provenance says `ROBUST` still came off the sensitivity graph,
 * and the moment this function starts inspecting `speculativeJunctionCount`
 * there is a value of it that puts a possible-graph route back into canonical
 * teal.
 */
export type { PossibleProvenance };

export interface RouteTarget {
  readonly kind: 'canonical' | 'speculative';
  /** The GeoJSON source to feed. */
  readonly source: string;
  /** The layer that draws it. */
  readonly layer: string;
  readonly colour: string;
  /**
   * Whether the reveal may run on this layer.
   *
   * False for the speculative layer, and not by preference: it is dashed, and
   * MapLibre supports neither `line-gradient` nor `line-dasharray` alongside
   * the other. Reported rather than left implicit so a caller cannot animate a
   * layer whose paint would reject the expression.
   */
  readonly animated: boolean;
}

const CANONICAL: RouteTarget = {
  kind: 'canonical',
  source: SRC.routeFocus,
  layer: LYR.routeFocus,
  colour: palette.route,
  animated: true,
};

const SPECULATIVE: RouteTarget = {
  kind: 'speculative',
  source: SRC.routeSpeculative,
  layer: LYR.routeSpeculative,
  colour: palette.speculative,
  animated: false,
};

/** Both targets, so a caller can clear the one it did not choose. */
export const ROUTE_TARGETS: readonly RouteTarget[] = [CANONICAL, SPECULATIVE];

/**
 * The one place the decision is made.
 *
 * Any provenance at all means the route came off the POSSIBLE graph, so it
 * gets the speculative target. Absent or null provenance means the canonical
 * graph — the default, and the only path to teal.
 */
export function routeTarget(
  provenance: PossibleProvenance | null | undefined,
): RouteTarget {
  return provenance ? SPECULATIVE : CANONICAL;
}

/** The target NOT chosen, whose source must be emptied. */
export function otherTarget(chosen: RouteTarget): RouteTarget {
  return chosen.kind === 'canonical' ? SPECULATIVE : CANONICAL;
}
