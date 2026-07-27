/**
 * Isolation profile: when a closure leaves no replacement path, WHAT gets cut off?
 *
 * "No detour exists" is far more useful when it is quantified. A closure that
 * strands one driveway is not the same event as one that strands a settlement,
 * yet the endpoint measure returns the identical DISCONNECTED for both.
 *
 * Which side is stranded?
 * -----------------------
 * It depends on the direction under test, and getting this wrong makes the
 * measure meaningless. Closing the mouth of a cul-de-sac:
 *
 *   forward (mouth -> far end): the FAR END is cut off. Measure what can still
 *                               reach the far end - a small pocket.
 *   reverse (far end -> mouth): the FAR END is again what is cut off, but now
 *                               it is the ORIGIN. Measuring what can reach the
 *                               mouth would return the entire network.
 *
 * So both sides are probed, each with a bound, and the side that terminates
 * within the bound is the isolated pocket. If neither terminates, the closure
 * has not isolated anything of a size worth reporting and the result says so
 * rather than inventing a number.
 *
 * The bound also keeps this cheap: an unbounded backward walk over the whole
 * network cost 70 ms per link in the pilot, against roughly 1 ms bounded.
 */

import { RoadGraph, profileMask } from './graph.js';
import type { VehicleProfile } from './types.js';

export interface IsolationProfile {
  /** Which end of the closed link is stranded, if either. */
  side: 'downstream' | 'upstream' | 'none';
  pocketNodeCount: number;
  pocketLinkCount: number;
  pocketLengthM: number;
  /**
   * True when neither side could be enumerated within the bound. The closure
   * did not isolate a small pocket; the counts are not meaningful.
   */
  bounded: boolean;
  /** True when the reported pocket was fully enumerated. */
  exact: boolean;
}

interface Walk {
  nodeCount: number;
  linkCount: number;
  lengthM: number;
  complete: boolean;
}

/** Bounded traversal. `backward` follows in-arcs (what can reach `start`). */
function walk(
  g: RoadGraph,
  start: number,
  excluded: Set<number>,
  mask: number,
  backward: boolean,
  maxNodes: number,
): Walk {
  const seen = new Set<number>([start]);
  const stack = [start];
  const links = new Set<number>();
  let lengthM = 0;

  while (stack.length > 0) {
    if (seen.size > maxNodes) {
      return { nodeCount: seen.size, linkCount: links.size, lengthM, complete: false };
    }
    const n = stack.pop()!;
    const s = backward ? g.inStart[n] : g.outStart[n];
    const e = backward ? g.inStart[n + 1] : g.outStart[n + 1];
    const arcs = backward ? g.inArcs : g.outArcs;
    for (let i = s; i < e; i++) {
      const a = arcs[i];
      if (excluded.has(a) || (g.arcMode[a] & mask) === 0) continue;
      const lid = g.arcLink[a];
      if (!links.has(lid)) {
        links.add(lid);
        lengthM += g.arcDistance[a];
      }
      const next = backward ? g.arcFrom[a] : g.arcTo[a];
      if (!seen.has(next)) {
        seen.add(next);
        stack.push(next);
      }
    }
  }
  return { nodeCount: seen.size, linkCount: links.size, lengthM, complete: true };
}

export function isolationProfile(
  g: RoadGraph,
  sourceNode: number,
  targetNode: number,
  removedArcIds: ArrayLike<number>,
  profile: VehicleProfile,
  maxNodes = 5_000,
): IsolationProfile {
  const mask = profileMask(profile);
  const excluded = new Set<number>();
  for (let i = 0; i < removedArcIds.length; i++) excluded.add(removedArcIds[i] as number);

  // What can still reach the far end, and what the near end can still get to.
  const downstream = walk(g, targetNode, excluded, mask, true, maxNodes);
  const upstream = walk(g, sourceNode, excluded, mask, false, maxNodes);

  const candidates: { side: 'downstream' | 'upstream'; w: Walk }[] = [];
  if (downstream.complete) candidates.push({ side: 'downstream', w: downstream });
  if (upstream.complete) candidates.push({ side: 'upstream', w: upstream });

  if (candidates.length === 0) {
    return {
      side: 'none',
      pocketNodeCount: 0,
      pocketLinkCount: 0,
      pocketLengthM: 0,
      bounded: true,
      exact: false,
    };
  }

  candidates.sort((a, b) => a.w.linkCount - b.w.linkCount);
  const best = candidates[0];
  return {
    side: best.side,
    pocketNodeCount: best.w.nodeCount,
    pocketLinkCount: best.w.linkCount,
    pocketLengthM: best.w.lengthM,
    bounded: false,
    exact: true,
  };
}
