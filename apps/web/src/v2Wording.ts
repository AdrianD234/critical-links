/*
 * The V2 result vocabulary, in a form something other than the type checker
 * can read.
 *
 * Two jobs, both of which exist because a V2 finding is easy to render as a
 * stronger claim than it is.
 *
 * The first is `V2_HEADLINES`. The union in ./api/types.ts stops a headline
 * that does not exist from compiling, but a union is erased at build time, so
 * nothing outside the compiler can check the list is the one the engine
 * documents. The record below is exhaustive by construction: a headline added
 * to the union and not to it fails to compile, and so does a misspelling.
 *
 * The second is `mutualReachability`. `same_scc_after_closure` is now a
 * three-state answer, and the third state is the one that matters — null means
 * no conclusion was reached, not that the endpoints are unreachable from each
 * other. Rendering it with a plain ternary turns "we did not test that" into
 * "we tested it and the answer is no", which is a finding about a road the
 * analysis never made.
 */

import type { V2BoundaryHeadline, V2Headline } from './api/types.js';

/**
 * Exhaustive by construction. The value is unused; the keys are the point.
 */
const HEADLINE_SET: Record<V2Headline, true> = {
  'Road cut off': true,
  'Network split into two represented components': true,
  'Through route found': true,
  'No endpoint route': true,
  'Directional access loss': true,
  'No isolation in the represented physical-access graph': true,
  'Partial analysis': true,
  'Analysis unresolved': true,
};

/** Every headline the engine may report, in the order it documents them. */
export const V2_HEADLINES = Object.keys(HEADLINE_SET) as V2Headline[];

/**
 * What a three-state mutual-reachability answer reads as.
 *
 * "not established" rather than "unknown": the distinction being drawn is
 * about this analysis, which did not reach a conclusion, and not about the
 * road, which may well have a settled answer nobody has computed.
 */
export const MUTUAL_REACHABILITY_UNKNOWN = 'not established';

export function mutualReachability(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return MUTUAL_REACHABILITY_UNKNOWN;
  return value ? 'reachable both ways' : 'not reachable both ways';
}

/**
 * Why no conclusion was reached, for the line under the answer.
 *
 * Deliberately names both causes rather than guessing between them: the
 * response does not say which applied, and a client that picked one would be
 * inventing the more reassuring half of the time.
 */
export const MUTUAL_REACHABILITY_UNKNOWN_REASON =
  'Either the link carries one direction only, so there was no return ' +
  'traversal to test, or a search did not resolve. Nothing here says the ' +
  'endpoints cannot reach each other.';

/**
 * The boundary engine's own vocabulary, kept apart from the endpoint one.
 *
 * A separate record rather than an addition to `HEADLINE_SET`, because the two
 * engines answer different questions and a merged list would let a boundary
 * headline compile into an endpoint result. Exhaustive by the same
 * construction: a headline added to the union and not here fails to build.
 */
const BOUNDARY_HEADLINE_SET: Record<V2BoundaryHeadline, true> = {
  'Through movement has no represented replacement': true,
  'Through movement diverts': true,
  'No through movement identified': true,
  'Partial analysis': true,
  'Analysis unresolved': true,
};

/** Every headline the boundary engine may report. */
export const V2_BOUNDARY_HEADLINES = Object.keys(
  BOUNDARY_HEADLINE_SET,
) as V2BoundaryHeadline[];

/**
 * Headlines that assert something about the ROAD.
 *
 * None may appear when the candidate search was bounded, because each reads as
 * a statement about EVERY movement the closure interrupts, and an unevaluated
 * pair could hold the worst detour or the only disconnected movement.
 * "Partial analysis" and "Analysis unresolved" are deliberately absent: they
 * are what a bounded or a failed search says instead.
 */
export const V2_BOUNDARY_DEFINITIVE_HEADLINES: V2BoundaryHeadline[] = [
  'Through movement has no represented replacement',
  'Through movement diverts',
  'No through movement identified',
];

/**
 * Why a boundary figure and an endpoint figure may differ without either
 * being wrong.
 *
 * Shown wherever the two appear near each other. A reader who sees 8 km on one
 * and "no route" on the other will reach for an explanation, and the true one
 * is that they measured different trips - not that one engine is broken.
 */
export const BOUNDARY_VS_ENDPOINT =
  'The endpoint measure asks whether the closed segment’s own two ends can ' +
  'still reach each other. The boundary measure asks whether trips that went ' +
  'through here still have a way round. They are different quantities and a ' +
  'difference between them is not a disagreement.';
