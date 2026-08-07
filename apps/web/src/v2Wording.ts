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

import type { V2Headline } from './api/types.js';

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
