/**
 * Shortest-path search over the arc-expanded road graph.
 *
 * The search state is an ARC, not a node. Expanding over arcs is what makes
 * turn restrictions expressible at all: a banned manoeuvre is a property of the
 * pair (arc you arrived on, arc you are leaving on), which a node-state search
 * cannot see.
 *
 * Algorithm: A* with a Euclidean lower bound. The heuristic is admissible and
 * consistent in both metrics:
 *   distance:  h(u) = |u - target|                   <=  any path length
 *   time:      h(u) = |u - target| / maxSpeed        <=  any path duration
 * so the first goal state popped is optimal and settled states never reopen.
 *
 * Correctness caveat, carried through to the API and docs: for turn
 * restrictions of exactly two links the arc state is sufficient and the result
 * is exact. For AMDS restrictions spanning three or more links the check walks
 * the predecessor chain of the best-known path to the previous arc, which is a
 * close approximation rather than a proof-carrying exact search (an exact
 * treatment needs the last L-1 arcs in the state). The count of restrictions
 * longer than two links is reported by the QA pipeline so the exposure is
 * measured, not assumed. See docs/KNOWN_LIMITATIONS.md.
 */

import { RoadGraph, profileMask } from './graph.js';
import type { DetourStatus, Metric, VehicleProfile } from './types.js';

export interface RouteRequest {
  sourceNode: number;
  targetNode: number;
  metric: Metric;
  profile: VehicleProfile;
  /** Arcs treated as unavailable for this query. */
  excludedArcs?: ArrayLike<number> | null;
  /** Abort after this many settled states. */
  maxStatesExplored?: number;
  /** Abort after this wall-clock budget. */
  timeBudgetMs?: number;
}

export interface RouteResult {
  status: DetourStatus;
  /** Total cost in metres (distance metric) or seconds (time metric). */
  cost: number | null;
  /** Distance in metres regardless of which metric drove the search. */
  distanceM: number | null;
  /** Duration in seconds, null when any arc on the route lacks a valid speed. */
  timeS: number | null;
  arcIds: number[];
  statesExplored: number;
  runtimeMs: number;
  detail: string | null;
}

/** Binary min-heap over (priority, arcState). */
class Heap {
  private keys: Float64Array;
  private vals: Int32Array;
  private n = 0;

  constructor(capacity = 1024) {
    this.keys = new Float64Array(capacity);
    this.vals = new Int32Array(capacity);
  }

  get size(): number {
    return this.n;
  }

  clear(): void {
    this.n = 0;
  }

  private grow(): void {
    const k = new Float64Array(this.keys.length * 2);
    k.set(this.keys);
    this.keys = k;
    const v = new Int32Array(this.vals.length * 2);
    v.set(this.vals);
    this.vals = v;
  }

  push(key: number, val: number): void {
    if (this.n === this.keys.length) this.grow();
    let i = this.n++;
    this.keys[i] = key;
    this.vals[i] = val;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.keys[p] <= this.keys[i]) break;
      this.swap(i, p);
      i = p;
    }
  }

  pop(): number {
    const top = this.vals[0];
    this.n--;
    if (this.n > 0) {
      this.keys[0] = this.keys[this.n];
      this.vals[0] = this.vals[this.n];
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < this.n && this.keys[l] < this.keys[m]) m = l;
        if (r < this.n && this.keys[r] < this.keys[m]) m = r;
        if (m === i) break;
        this.swap(i, m);
        i = m;
      }
    }
    return top;
  }

  private swap(i: number, j: number): void {
    const k = this.keys[i];
    this.keys[i] = this.keys[j];
    this.keys[j] = k;
    const v = this.vals[i];
    this.vals[i] = this.vals[j];
    this.vals[j] = v;
  }
}

export class Router {
  readonly graph: RoadGraph;

  // Scratch arrays reused across queries. `stamp` avoids an O(arcCount) clear
  // per query, which matters when running hundreds of thousands of closures.
  private dist: Float64Array;
  private pred: Int32Array;
  private stamp: Int32Array;
  private closed: Int32Array;
  private excl: Int32Array;
  private epoch = 0;
  private exclEpoch = 0;
  private heap = new Heap(4096);

  constructor(graph: RoadGraph) {
    this.graph = graph;
    const n = graph.arcCount;
    this.dist = new Float64Array(n);
    this.pred = new Int32Array(n);
    this.stamp = new Int32Array(n);
    this.closed = new Int32Array(n);
    this.excl = new Int32Array(n);
  }

  /** True when traversing `prev` then `next` is a banned manoeuvre. */
  private restricted(prev: number, next: number, profile: VehicleProfile): boolean {
    const g = this.graph;
    const list = g.restrictionsByFinalLink.get(g.arcLink[next]);
    if (list === undefined) return false;
    for (const r of list) {
      const applies =
        profile === 'car'
          ? r.restrictedVehicle
          : profile === 'heavy'
            ? r.restrictedVehicleHeavy
            : r.restrictedEmergency;
      if (!applies) continue;
      const seq = r.linkSeq;
      let i = seq.length - 2;
      let cur = prev;
      let ok = true;
      while (i >= 0) {
        if (cur < 0 || g.arcLink[cur] !== seq[i]) {
          ok = false;
          break;
        }
        cur = this.stamp[cur] === this.epoch ? this.pred[cur] : -1;
        i--;
      }
      if (ok) return true;
    }
    return false;
  }

  route(req: RouteRequest): RouteResult {
    const started = Date.now();
    const g = this.graph;
    const mask = profileMask(req.profile);
    const useTime = req.metric === 'time';
    const cost = useTime ? g.arcTime : g.arcDistance;
    const maxStates = req.maxStatesExplored ?? 3_000_000;
    const timeBudget = req.timeBudgetMs ?? 20_000;

    const { sourceNode, targetNode } = req;
    if (
      sourceNode < 0 ||
      targetNode < 0 ||
      sourceNode >= g.nodeCount ||
      targetNode >= g.nodeCount
    ) {
      return fail('INVALID_GRAPH', 'node index out of range', started);
    }

    // Mark exclusions in O(1)-reset stamp space.
    this.exclEpoch++;
    if (req.excludedArcs) {
      for (let i = 0; i < req.excludedArcs.length; i++) {
        this.excl[req.excludedArcs[i] as number] = this.exclEpoch;
      }
    }
    const exclEpoch = this.exclEpoch;

    if (sourceNode === targetNode) {
      return {
        status: 'OK',
        cost: 0,
        distanceM: 0,
        timeS: 0,
        arcIds: [],
        statesExplored: 0,
        runtimeMs: Date.now() - started,
        detail: 'source and target are the same node',
      };
    }

    // Cheap definitive negative: different weak components can never connect.
    if (g.component[sourceNode] !== g.component[targetNode]) {
      return fail(
        'DISCONNECTED',
        'endpoints lie in different weakly connected components',
        started,
      );
    }

    const tx = g.nodeX[targetNode];
    const ty = g.nodeY[targetNode];
    const invMaxSpeed = 1 / g.maxSpeedMps;
    const h = (node: number): number => {
      const d = Math.hypot(g.nodeX[node] - tx, g.nodeY[node] - ty);
      return useTime ? d * invMaxSpeed : d;
    };

    this.epoch++;
    const epoch = this.epoch;
    this.heap.clear();

    let states = 0;
    let goal = -1;

    // Seed with every usable arc leaving the source node.
    for (let i = g.outStart[sourceNode]; i < g.outStart[sourceNode + 1]; i++) {
      const a = g.outArcs[i];
      if (this.excl[a] === exclEpoch) continue;
      if ((g.arcMode[a] & mask) === 0) continue;
      const c = cost[a];
      if (!Number.isFinite(c)) continue;
      this.dist[a] = c;
      this.pred[a] = -1;
      this.stamp[a] = epoch;
      this.heap.push(c + h(g.arcTo[a]), a);
    }

    let sinceClockCheck = 0;
    while (this.heap.size > 0) {
      const a = this.heap.pop();
      if (this.closed[a] === epoch) continue;
      this.closed[a] = epoch;
      states++;

      if (g.arcTo[a] === targetNode) {
        goal = a;
        break;
      }

      if (states > maxStates) {
        return fail(
          'UNRESOLVED_TIMEOUT',
          `state limit ${maxStates} exceeded`,
          started,
          states,
        );
      }
      if (++sinceClockCheck >= 4096) {
        sinceClockCheck = 0;
        if (Date.now() - started > timeBudget) {
          return fail(
            'UNRESOLVED_TIMEOUT',
            `time budget ${timeBudget}ms exceeded`,
            started,
            states,
          );
        }
      }

      const da = this.dist[a];
      const via = g.arcTo[a];
      for (let i = g.outStart[via]; i < g.outStart[via + 1]; i++) {
        const b = g.outArcs[i];
        if (this.excl[b] === exclEpoch) continue;
        if ((g.arcMode[b] & mask) === 0) continue;
        if (this.closed[b] === epoch) continue;
        const cb = cost[b];
        if (!Number.isFinite(cb)) continue;
        const nd = da + cb;
        if (this.stamp[b] === epoch && this.dist[b] <= nd) continue;
        if (this.restricted(a, b, req.profile)) continue;
        this.dist[b] = nd;
        this.pred[b] = a;
        this.stamp[b] = epoch;
        this.heap.push(nd + h(g.arcTo[b]), b);
      }
    }

    if (goal < 0) {
      return fail(
        'DISCONNECTED',
        'search space exhausted with no route to target',
        started,
        states,
      );
    }

    // Reconstruct.
    const arcIds: number[] = [];
    for (let c = goal; c >= 0; c = this.pred[c]) arcIds.push(c);
    arcIds.reverse();

    let distanceM = 0;
    let timeS: number | null = 0;
    for (const arc of arcIds) {
      distanceM += g.arcDistance[arc];
      if (timeS !== null) {
        const t = g.arcTime[arc];
        timeS = Number.isFinite(t) ? timeS + t : null;
      }
    }

    return {
      status: 'OK',
      cost: this.dist[goal],
      distanceM,
      timeS,
      arcIds,
      statesExplored: states,
      runtimeMs: Date.now() - started,
      detail: null,
    };
  }
}

function fail(
  status: DetourStatus,
  detail: string,
  started: number,
  states = 0,
): RouteResult {
  return {
    status,
    cost: null,
    distanceM: null,
    timeS: null,
    arcIds: [],
    statesExplored: states,
    runtimeMs: Date.now() - started,
    detail,
  };
}
