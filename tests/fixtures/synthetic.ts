/**
 * Builders for small hand-computed networks with known answers.
 *
 * Coordinates are plain EPSG:2193-style metres. Keeping them small and exact
 * means every expected distance in the tests can be worked out on paper, which
 * is the point: these tests check the routing engine against arithmetic, not
 * against itself.
 */

import { buildGraph, type GeometryStore } from '../../packages/core/src/graph.js';
import { DetourEngine } from '../../packages/core/src/detour.js';
import { polylineLength } from '../../packages/core/src/geo.js';
import type {
  LinkRecord,
  TurnRestriction,
} from '../../packages/core/src/types.js';

export interface SpecLink {
  id: string;
  /** Ordered vertices, [x,y] pairs, EPSG:2193 metres. */
  pts: [number, number][];
  /** Default false (two-way). When true only the digitised direction is usable. */
  oneway?: boolean;
  closureGroup?: string;
  roadName?: string;
  speedKph?: number;
  modeVehicle?: boolean;
  modeVehicleHeavy?: boolean;
  modeEmergency?: boolean;
  modeFerry?: boolean;
}

export interface SpecRestriction {
  /** amdsIds in traversal order. */
  seq: string[];
  vehicle?: boolean;
  heavy?: boolean;
  emergency?: boolean;
}

export interface SyntheticNetwork {
  links: LinkRecord[];
  geometry: GeometryStore;
  graph: ReturnType<typeof buildGraph>['graph'];
  engine: DetourEngine;
  inferredJoins: number;
  /** amdsId -> internal linkId */
  byId: Map<string, number>;
}

export function synthetic(
  spec: SpecLink[],
  restrictionSpec: SpecRestriction[] = [],
  opts: { clipped?: boolean; snapshotId?: string } = {},
): SyntheticNetwork {
  const coords: number[] = [];
  const offset: number[] = [0];
  const links: LinkRecord[] = [];
  const byId = new Map<string, number>();

  spec.forEach((s, i) => {
    for (const [x, y] of s.pts) coords.push(x, y);
    offset.push(coords.length);
    byId.set(s.id, i);

    const start = offset[i];
    const end = offset[i + 1];
    const lengthM = polylineLength(coords, start, end);

    links.push({
      linkId: i,
      amdsId: s.id,
      objectId: 1000 + i,
      closureGroupId: s.closureGroup ?? s.id,
      roadName: s.roadName ?? null,
      assetOwnerOrganisation: 40,
      dataManagingOrganisation: 40,
      amdsIDAuthority: null,
      modelAssetType: 1,
      surfaceType: 1,
      status: 1,
      oneway: s.oneway ? 1 : 2,
      lengthM,
      sourceLengthM: null,
      forwardAllowed: true,
      reverseAllowed: !s.oneway,
      modeVehicle: s.modeVehicle ?? true,
      modeVehicleHeavy: s.modeVehicleHeavy ?? true,
      modeEmergency: s.modeEmergency ?? true,
      modeFerry: s.modeFerry ?? false,
      lifeLineRoute: false,
      sharedInfrastructure: false,
      detourAvailableFlag: false,
      speedKph: s.speedKph ?? 50,
      speedSource: 'estimated_asset_type',
      qualityFlags: [],
      sourceNode: -1,
      targetNode: -1,
    });
  });

  const geometry: GeometryStore = {
    coords: Float64Array.from(coords),
    offset: Int32Array.from(offset),
  };

  const restrictions: TurnRestriction[] = restrictionSpec.map((r, i) => ({
    amdsIDRestrictedTurn: `R${i}`,
    linkSeq: r.seq.map((id) => {
      const v = byId.get(id);
      if (v === undefined) throw new Error(`restriction references unknown link ${id}`);
      return v;
    }),
    restrictedVehicle: r.vehicle ?? true,
    restrictedVehicleHeavy: r.heavy ?? true,
    restrictedEmergency: r.emergency ?? false,
  }));

  const built = buildGraph(links, geometry, restrictions);
  const engine = new DetourEngine(built.graph, links, {
    snapshotId: opts.snapshotId ?? 'test-snapshot',
    coreLink: null,
    clipped: opts.clipped ?? false,
  });

  return {
    links,
    geometry,
    graph: built.graph,
    engine,
    inferredJoins: built.inferredJoins,
    byId,
  };
}
