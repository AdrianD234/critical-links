/**
 * Corridor-level replacement path.
 *
 * Why the link-endpoint measure is not enough
 * -------------------------------------------
 * The primary metric asks: after closing link e = (u, v), what is the shortest
 * path from u back to v? On an undivided two-way road that is exactly the right
 * question. On a ONE-WAY carriageway it frequently has no answer at all, and
 * for a reason that has nothing to do with the road being critical.
 *
 * Measured on the Wellington pilot: 82% of state-highway links returned
 * DISCONNECTED under the endpoint measure. Inspecting Cobham Drive showed why -
 * it is a one-way carriageway whose downstream endpoint is an internal node of
 * the one-way system, reachable ONLY by driving that carriageway. There is no
 * u->v path because v is not a place you can arrive at any other way, not
 * because traffic cannot get past. Real traffic reroutes via the opposing
 * carriageway and rejoins further along.
 *
 * What this does instead
 * ----------------------
 * Walk outward from the closure to the nearest points at which a driver
 * actually has a choice:
 *
 *   entry node U - walk upstream from u while each node has exactly one usable
 *                  outgoing arc. A node with two or more is where a driver
 *                  could have gone another way.
 *   exit node  V - walk downstream from v while each node has exactly one
 *                  usable incoming arc. A node with two or more is somewhere
 *                  traffic can arrive from elsewhere.
 *
 * Then compare the intact and closed shortest paths between U and V. That is
 * the distance genuinely added to a trip that wanted to use this link.
 *
 * This is reported ALONGSIDE the endpoint metric, never instead of it. The two
 * answer different questions and both are labelled as such in the API and UI.
 */

import { RoadGraph, profileMask } from './graph.js';
import { Router } from './routing.js';
import type { DetourStatus, Metric, VehicleProfile } from './types.js';

export interface CorridorResult {
  status: DetourStatus;
  /** Node the corridor measure starts from (upstream choice point). */
  entryNode: number;
  /** Node the corridor measure ends at (downstream rejoin point). */
  exitNode: number;
  /** How many links were traversed to reach each choice point. */
  hopsUpstream: number;
  hopsDownstream: number;
  /** Distance along the corridor that is being replaced, including the link. */
  corridorDistanceM: number | null;
  normalDistanceM: number | null;
  alternativeDistanceM: number | null;
  /** alternative - normal. The added distance for a through trip. */
  penaltyM: number | null;
  normalTimeS: number | null;
  alternativeTimeS: number | null;
  penaltyTimeS: number | null;
  /** True when the walk stopped because it hit a limit rather than a junction. */
  truncated: boolean;
  /**
   * False when, with the closure applied, NOTHING can reach the downstream
   * choice point. That is the signature of a sole-access road: the closed link
   * is the only way in. It is the strongest criticality signal the structural
   * model can produce, and it is quite different from the one-way-carriageway
   * case where the endpoint measure merely has no answer.
   */
  exitReachable: boolean;
  detail: string | null;
}

export interface CorridorOptions {
  maxHops?: number;
  maxWalkDistanceM?: number;
  maxStatesExplored?: number;
  timeBudgetMs?: number;
}

/** Unit direction of an arc, used to keep a walk on the same road. */
function arcHeading(g: RoadGraph, arc: number): [number, number] {
  const dx = g.nodeX[g.arcTo[arc]] - g.nodeX[g.arcFrom[arc]];
  const dy = g.nodeY[g.arcTo[arc]] - g.nodeY[g.arcFrom[arc]];
  const m = Math.hypot(dx, dy) || 1;
  return [dx / m, dy / m];
}

interface WalkStep {
  node: number;
  cumulativeM: number;
}

/**
 * Collects the sequence of nodes reached by walking away from `start` along the
 * corridor. Where there is a choice, the straightest continuation is taken,
 * which is the usual "stay on the same road" rule. The caller decides how far
 * out to go by trying successively longer prefixes.
 */
function walkCorridor(
  g: RoadGraph,
  start: number,
  excluded: Set<number>,
  mask: number,
  upstream: boolean,
  maxHops: number,
  maxDist: number,
): { steps: WalkStep[]; truncated: boolean } {
  const steps: WalkStep[] = [{ node: start, cumulativeM: 0 }];
  const seen = new Set<number>([start]);
  let cur = start;
  let dist = 0;
  let heading: [number, number] | null = null;

  for (let hop = 0; hop < maxHops; hop++) {
    const s = upstream ? g.inStart[cur] : g.outStart[cur];
    const e = upstream ? g.inStart[cur + 1] : g.outStart[cur + 1];
    const arcs = upstream ? g.inArcs : g.outArcs;

    let best = -1;
    let bestScore = -Infinity;
    for (let i = s; i < e; i++) {
      const a = arcs[i];
      if (excluded.has(a) || (g.arcMode[a] & mask) === 0) continue;
      const next = upstream ? g.arcFrom[a] : g.arcTo[a];
      if (seen.has(next)) continue;
      let score = 0;
      if (heading) {
        const [hx, hy] = arcHeading(g, a);
        // Upstream we are travelling against the arc direction.
        score = upstream ? -(hx * heading[0] + hy * heading[1]) : hx * heading[0] + hy * heading[1];
        score = upstream ? -score : score;
      }
      if (score > bestScore) {
        bestScore = score;
        best = a;
      }
    }
    if (best < 0) return { steps, truncated: false };

    const next = upstream ? g.arcFrom[best] : g.arcTo[best];
    dist += g.arcDistance[best];
    if (dist > maxDist) return { steps, truncated: true };
    heading = arcHeading(g, best);
    seen.add(next);
    cur = next;
    steps.push({ node: next, cumulativeM: dist });
  }
  return { steps, truncated: true };
}

export function computeCorridor(
  graph: RoadGraph,
  router: Router,
  opts: {
    sourceNode: number;
    targetNode: number;
    removedArcIds: number[];
    metric: Metric;
    profile: VehicleProfile;
    linkLengthM: number;
  } & CorridorOptions,
): CorridorResult {
  const mask = profileMask(opts.profile);
  const excluded = new Set(opts.removedArcIds);
  const maxHops = opts.maxHops ?? 60;
  const maxWalk = opts.maxWalkDistanceM ?? 25_000;

  const up = walkCorridor(graph, opts.sourceNode, excluded, mask, true, maxHops, maxWalk);
  const down = walkCorridor(graph, opts.targetNode, excluded, mask, false, maxHops, maxWalk);

  // Is the immediate downstream endpoint reachable at all with the closure in
  // place? If not, the closed link is the sole access to whatever lies beyond.
  let exitReachable = false;
  for (let i = graph.inStart[opts.targetNode]; i < graph.inStart[opts.targetNode + 1]; i++) {
    const a = graph.inArcs[i];
    if (excluded.has(a) || (graph.arcMode[a] & mask) === 0) continue;
    exitReachable = true;
    break;
  }

  const excludedArr = Int32Array.from(opts.removedArcIds);
  // Probe hop distances geometrically rather than one at a time. Trying every
  // hop on a long divided-highway corridor cost 60 shortest-path calls per
  // direction; this needs about a dozen and finds the same crossover, at worst
  // reporting endpoints slightly further out than the minimum. Both the normal
  // and closed paths are measured over the same corridor, so the PENALTY stays
  // valid either way.
  const maxK = Math.max(up.steps.length, down.steps.length);
  const probes: number[] = [];
  for (let k = 0, step = 1; k < maxK; k += step, step = Math.max(1, Math.ceil(step * 1.6))) {
    probes.push(k);
  }
  if (probes[probes.length - 1] !== maxK - 1) probes.push(maxK - 1);
  let lastDetail: string | null = 'no corridor endpoints could be reached';

  // Expand outward a hop at a time until a replacement path exists. On a
  // divided highway this is what finds the nearest crossover; stopping at the
  // first "choice point" is not enough, because a node can have a choice of
  // where to go and still offer no way back onto the opposing carriageway.
  for (const k of probes) {
    const entry = up.steps[Math.min(k, up.steps.length - 1)];
    const exit = down.steps[Math.min(k, down.steps.length - 1)];
    if (entry.node === exit.node) continue;

    const alt = router.route({
      sourceNode: entry.node,
      targetNode: exit.node,
      metric: opts.metric,
      profile: opts.profile,
      excludedArcs: excludedArr,
      maxStatesExplored: opts.maxStatesExplored,
      timeBudgetMs: opts.timeBudgetMs,
    });

    if (alt.status === 'UNRESOLVED_TIMEOUT' || alt.status === 'INVALID_GRAPH') {
      return {
        status: alt.status,
        entryNode: entry.node,
        exitNode: exit.node,
        hopsUpstream: Math.min(k, up.steps.length - 1),
        hopsDownstream: Math.min(k, down.steps.length - 1),
        corridorDistanceM: entry.cumulativeM + opts.linkLengthM + exit.cumulativeM,
        normalDistanceM: null,
        alternativeDistanceM: null,
        penaltyM: null,
        normalTimeS: null,
        alternativeTimeS: null,
        penaltyTimeS: null,
        truncated: up.truncated || down.truncated,
        exitReachable,
        detail: alt.detail,
      };
    }

    if (alt.status !== 'OK') {
      lastDetail = alt.detail;
      continue;
    }

    const normal = router.route({
      sourceNode: entry.node,
      targetNode: exit.node,
      metric: opts.metric,
      profile: opts.profile,
      excludedArcs: null,
      maxStatesExplored: opts.maxStatesExplored,
      timeBudgetMs: opts.timeBudgetMs,
    });
    const normDist = normal.status === 'OK' ? normal.distanceM : null;

    return {
      status: 'OK',
      entryNode: entry.node,
      exitNode: exit.node,
      hopsUpstream: Math.min(k, up.steps.length - 1),
      hopsDownstream: Math.min(k, down.steps.length - 1),
      corridorDistanceM: entry.cumulativeM + opts.linkLengthM + exit.cumulativeM,
      normalDistanceM: normDist,
      alternativeDistanceM: alt.distanceM,
      penaltyM: normDist === null ? null : alt.distanceM! - normDist,
      normalTimeS: normal.status === 'OK' ? normal.timeS : null,
      alternativeTimeS: alt.timeS,
      penaltyTimeS:
        alt.timeS !== null && normal.status === 'OK' && normal.timeS !== null
          ? alt.timeS - normal.timeS
          : null,
      truncated: up.truncated || down.truncated,
      exitReachable,
      detail: null,
    };
  }

  return {
    status: 'DISCONNECTED',
    entryNode: up.steps[up.steps.length - 1].node,
    exitNode: down.steps[down.steps.length - 1].node,
    hopsUpstream: up.steps.length - 1,
    hopsDownstream: down.steps.length - 1,
    corridorDistanceM:
      up.steps[up.steps.length - 1].cumulativeM +
      opts.linkLengthM +
      down.steps[down.steps.length - 1].cumulativeM,
    normalDistanceM: null,
    alternativeDistanceM: null,
    penaltyM: null,
    normalTimeS: null,
    alternativeTimeS: null,
    penaltyTimeS: null,
    truncated: up.truncated || down.truncated,
    exitReachable,
    detail: lastDetail,
  };
}
