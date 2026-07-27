/**
 * Detour / replacement-path metrics for a single closure.
 *
 * Definitions (these are the contract; docs/METRIC_DEFINITIONS.md restates them
 * for non-developers):
 *
 *   alternative_distance_m = length of the shortest valid path from u to v
 *                            after every arc in the closure group is removed
 *   added_distance_vs_link_m = alternative_distance_m - selected_link_length_m
 *   normal_shortest_path_m   = shortest u->v distance on the INTACT graph
 *   network_penalty_m        = alternative_distance_m - normal_shortest_path_m
 *   detour_ratio_vs_link     = alternative_distance_m / selected_link_length_m
 *
 * `network_penalty_m` is the more rigorous of the two comparisons because it
 * does not assume the closed link was itself the normal shortest way between
 * its own endpoints. On a divided carriageway or a slip lane it frequently is
 * not.
 */

import { RoadGraph } from './graph.js';
import { Router } from './routing.js';
import { computeCorridor } from './corridor.js';
import { isolationProfile } from './isolation.js';
import {
  ALGORITHM,
  ALGORITHM_VERSION,
  type ClosureScope,
  type DetourDirectionResult,
  type DetourResult,
  type Direction,
  type LinkRecord,
  type Metric,
  type VehicleProfile,
} from './types.js';

export interface DetourRequest {
  linkId: number;
  metric?: Metric;
  profile?: VehicleProfile;
  closureScope?: ClosureScope;
  directions?: Direction[];
  maxStatesExplored?: number;
  timeBudgetMs?: number;
  /** Set false to skip the corridor fallback (batch runs that do not need it). */
  computeCorridor?: boolean;
}

export interface DetourEngineOptions {
  snapshotId: string;
  /**
   * Per-link flag: 1 when the link lies inside the analysis area, 0 when it is
   * only present as network buffer around a clipped extract. Omit for a
   * national snapshot.
   */
  coreLink?: Uint8Array | null;
  /** True when the snapshot was clipped to an extent rather than national. */
  clipped: boolean;
}

export class DetourEngine {
  readonly graph: RoadGraph;
  readonly links: LinkRecord[];
  readonly router: Router;
  private readonly opts: DetourEngineOptions;

  constructor(graph: RoadGraph, links: LinkRecord[], opts: DetourEngineOptions) {
    this.graph = graph;
    this.links = links;
    this.router = new Router(graph);
    this.opts = opts;
  }

  compute(req: DetourRequest): DetourResult {
    const g = this.graph;
    const metric = req.metric ?? 'distance';
    const profile = req.profile ?? 'car';
    const closureScope = req.closureScope ?? 'physical';
    const link = this.links[req.linkId];
    if (!link) throw new Error(`unknown linkId ${req.linkId}`);

    // --- work out exactly what is being removed -----------------------------
    // Under `physical` scope the exclusion set is the same for both directions.
    // Under `directed` scope it depends on the direction under test, so it is
    // resolved inside run() and only summarised here.
    const physicalArcs = Array.from(g.closureArcs(link.linkId));
    const excludedFor = (direction: Direction): number[] => {
      if (closureScope === 'physical') return physicalArcs;
      const a = g.arcOfLinkDirection(link.linkId, direction);
      return a >= 0 ? [a] : [];
    };

    const wanted: Direction[] =
      req.directions ??
      ([
        link.forwardAllowed ? 'forward' : null,
        link.reverseAllowed ? 'reverse' : null,
      ].filter(Boolean) as Direction[]);

    const run = (direction: Direction): DetourDirectionResult => {
      const u = direction === 'forward' ? link.sourceNode : link.targetNode;
      const v = direction === 'forward' ? link.targetNode : link.sourceNode;
      const t0 = Date.now();
      const flags: string[] = [];
      const removed = excludedFor(direction);

      if (link.sourceNode === link.targetNode) {
        return blank(direction, u, v, link.lengthM, 'SOURCE_DATA_ERROR', [
          'SELF_LOOP',
        ], 'link starts and ends at the same node', Date.now() - t0, removed);
      }

      // Intact-graph baseline.
      const normal = this.router.route({
        sourceNode: u,
        targetNode: v,
        metric,
        profile,
        excludedArcs: null,
        maxStatesExplored: req.maxStatesExplored,
        timeBudgetMs: req.timeBudgetMs,
      });

      // Closure case.
      const alt = this.router.route({
        sourceNode: u,
        targetNode: v,
        metric,
        profile,
        excludedArcs: Int32Array.from(removed),
        maxStatesExplored: req.maxStatesExplored,
        timeBudgetMs: req.timeBudgetMs,
      });

      // A timeout must never be reported as "no detour exists".
      if (alt.status !== 'OK' && alt.status !== 'DISCONNECTED') {
        return blank(
          direction,
          u,
          v,
          link.lengthM,
          alt.status,
          flags,
          alt.detail,
          Date.now() - t0,
          removed,
          alt.statesExplored,
        );
      }

      if (link.speedSource !== 'nslr' && metric === 'time') flags.push('TIME_ESTIMATED');
      if (link.speedSource !== 'nslr') flags.push('SPEED_ESTIMATED');
      if (this.opts.clipped) flags.push('CLIPPED_EXTRACT');

      if (alt.status === 'DISCONNECTED') {
        if (this.opts.clipped) flags.push('DISCONNECTED_UNVERIFIED_OUTSIDE_EXTRACT');

        // Cheap first: what, if anything, is stranded? A closure that cuts off
        // a two-link pocket is a dead end, and there is no corridor question to
        // ask - skipping the expanding search there took the pilot from 63 ms
        // to a few ms per link.
        const isolation = isolationProfile(g, u, v, removed, profile);
        if (isolation.exact) {
          if (isolation.pocketLinkCount <= 3) flags.push('ISOLATES_CUL_DE_SAC');
          else if (isolation.pocketLinkCount >= 100) flags.push('ISOLATES_SIGNIFICANT_AREA');
        }

        // The corridor question is asked even for tiny pockets: a one-way
        // carriageway also strands a zero-link pocket at its far end, and that
        // is precisely the case the corridor measure exists to answer. The
        // search is kept cheap by probing hop distances geometrically rather
        // than trying every hop (see corridor.ts).
        const corridor = req.computeCorridor !== false
          ? computeCorridor(g, this.router, {
              sourceNode: u,
              targetNode: v,
              removedArcIds: removed,
              metric,
              profile,
              linkLengthM: link.lengthM,
              maxStatesExplored: req.maxStatesExplored,
              timeBudgetMs: req.timeBudgetMs,
            })
          : null;
        if (corridor?.status === 'OK') flags.push('ENDPOINT_MEASURE_UNDEFINED_CORRIDOR_USED');
        if (corridor && corridor.status !== 'OK' && !corridor.exitReachable) {
          flags.push('SOLE_ACCESS');
        }

        return {
          direction,
          status: 'DISCONNECTED',
          sourceNode: u,
          targetNode: v,
          selectedLinkLengthM: link.lengthM,
          normalPathDistanceM: normal.status === 'OK' ? normal.distanceM : null,
          alternativeDistanceM: null,
          addedDistanceVsLinkM: null,
          networkPenaltyM: null,
          detourRatioVsLink: null,
          normalPathTimeS: normal.status === 'OK' ? normal.timeS : null,
          alternativeTimeS: null,
          addedTimeS: null,
          removedArcIds: removed,
          corridor,
          isolation,
          routeArcIds: [],
          routeLinkIds: [],
          qualityFlags: flags,
          errorDetail: alt.detail,
          runtimeMs: Date.now() - t0,
          nodesExplored: alt.statesExplored,
        };
      }

      // Did the replacement route lean on buffer links outside the analysis area?
      const routeLinkIds: number[] = [];
      let usesBuffer = false;
      for (const arc of alt.arcIds) {
        const lid = g.arcLink[arc];
        if (routeLinkIds[routeLinkIds.length - 1] !== lid) routeLinkIds.push(lid);
        if (this.opts.coreLink && this.opts.coreLink[lid] === 0) usesBuffer = true;
      }
      if (usesBuffer) flags.push('ROUTE_USES_BUFFER');

      const altDist = alt.distanceM!;
      const normDist = normal.status === 'OK' ? normal.distanceM : null;

      return {
        direction,
        status: 'OK',
        sourceNode: u,
        targetNode: v,
        selectedLinkLengthM: link.lengthM,
        normalPathDistanceM: normDist,
        alternativeDistanceM: altDist,
        addedDistanceVsLinkM: altDist - link.lengthM,
        networkPenaltyM: normDist === null ? null : altDist - normDist,
        detourRatioVsLink: link.lengthM > 0 ? altDist / link.lengthM : null,
        normalPathTimeS: normal.status === 'OK' ? normal.timeS : null,
        alternativeTimeS: alt.timeS,
        addedTimeS:
          alt.timeS !== null && normal.status === 'OK' && normal.timeS !== null
            ? alt.timeS - normal.timeS
            : null,
        removedArcIds: removed,
        corridor: null,
        isolation: null,
        routeArcIds: alt.arcIds,
        routeLinkIds,
        qualityFlags: flags,
        errorDetail: null,
        runtimeMs: Date.now() - t0,
        nodesExplored: alt.statesExplored,
      };
    };

    const forward = wanted.includes('forward') ? run('forward') : null;
    const reverse = wanted.includes('reverse') ? run('reverse') : null;

    // Union of what was actually removed, for the response summary.
    const removedArcIds = Array.from(
      new Set([...(forward?.removedArcIds ?? []), ...(reverse?.removedArcIds ?? [])]),
    ).sort((a, b) => a - b);
    const removedLinkIds = Array.from(new Set(removedArcIds.map((a) => g.arcLink[a])));
    const removedAmdsIds = removedLinkIds.map((id) => this.links[id].amdsId);

    return {
      snapshotId: this.opts.snapshotId,
      linkId: link.linkId,
      amdsId: link.amdsId,
      closureGroupId: link.closureGroupId,
      vehicleProfile: profile,
      metric,
      closureScope,
      removedArcIds,
      removedLinkIds,
      removedAmdsIds,
      forward,
      reverse,
      algorithm: ALGORITHM,
      algorithmVersion: ALGORITHM_VERSION,
      calculatedAtUtc: new Date().toISOString(),
    };
  }
}

function blank(
  direction: Direction,
  u: number,
  v: number,
  lengthM: number,
  status: DetourDirectionResult['status'],
  flags: string[],
  detail: string | null,
  runtimeMs: number,
  removedArcIds: number[],
  explored = 0,
): DetourDirectionResult {
  return {
    direction,
    status,
    sourceNode: u,
    targetNode: v,
    selectedLinkLengthM: lengthM,
    normalPathDistanceM: null,
    alternativeDistanceM: null,
    addedDistanceVsLinkM: null,
    networkPenaltyM: null,
    detourRatioVsLink: null,
    normalPathTimeS: null,
    alternativeTimeS: null,
    addedTimeS: null,
    removedArcIds,
    corridor: null,
    isolation: null,
    routeArcIds: [],
    routeLinkIds: [],
    qualityFlags: flags,
    errorDetail: detail,
    runtimeMs,
    nodesExplored: explored,
  };
}
