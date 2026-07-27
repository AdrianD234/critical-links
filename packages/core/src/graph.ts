/**
 * In-memory directed road graph, held in typed arrays.
 *
 * Layout notes
 * ------------
 * Adjacency is CSR (compressed sparse row) over nodes: `outStart[node]` ..
 * `outStart[node+1]` indexes into `outArcs`. This keeps the hot loop of the
 * shortest-path search cache friendly and lets the whole national network
 * (~272k links / ~536k arcs) sit comfortably in memory.
 *
 * Node identity is derived from *coincident polyline endpoints only*. Two lines
 * that merely cross in plan view do NOT produce a node. That is deliberate: the
 * AMDS Network Model carries no z-level attribute, so grade separation is
 * preserved precisely by refusing to node at geometric intersections. Splitting
 * lines at crossings would invent connections at every overbridge and tunnel.
 * See docs/KNOWN_LIMITATIONS.md.
 */

import type {
  ArcRecord,
  LinkRecord,
  TurnRestriction,
  VehicleProfile,
} from './types.js';

/** Mode bit flags stored per arc. */
export const MODE_VEHICLE = 1;
export const MODE_HEAVY = 2;
export const MODE_EMERGENCY = 4;

export function profileMask(profile: VehicleProfile): number {
  switch (profile) {
    case 'car':
      return MODE_VEHICLE;
    case 'heavy':
      return MODE_HEAVY;
    case 'emergency':
      return MODE_EMERGENCY;
  }
}

/** Flat coordinate storage for link geometry, CSR-indexed by linkId. */
export interface GeometryStore {
  /** [x0,y0,x1,y1,...] for all links, concatenated, EPSG:2193. */
  coords: Float64Array;
  /** offset[i] .. offset[i+1] is the coordinate slice for link i (in floats). */
  offset: Int32Array;
}

export interface RoadGraphInit {
  nodeX: Float64Array;
  nodeY: Float64Array;
  arcFrom: Int32Array;
  arcTo: Int32Array;
  arcLink: Int32Array;
  arcDistance: Float64Array;
  arcTime: Float64Array;
  arcMode: Uint8Array;
  /** 0 = forward traversal of the link, 1 = reverse. */
  arcDirection: Uint8Array;
  linkCount: number;
  /** Per-link closure group, as a dense group index. */
  linkClosureGroup: Int32Array;
  /** Arcs belonging to each closure group, CSR by group index. */
  groupArcStart: Int32Array;
  groupArcs: Int32Array;
  /** Highest speed present, m/s. Used to keep the time heuristic admissible. */
  maxSpeedMps: number;
  geometry: GeometryStore;
  restrictions: TurnRestriction[];
}

export class RoadGraph {
  readonly nodeX: Float64Array;
  readonly nodeY: Float64Array;
  readonly arcFrom: Int32Array;
  readonly arcTo: Int32Array;
  readonly arcLink: Int32Array;
  readonly arcDistance: Float64Array;
  readonly arcTime: Float64Array;
  readonly arcMode: Uint8Array;
  readonly arcDirection: Uint8Array;
  readonly outStart: Int32Array;
  readonly outArcs: Int32Array;
  /** Reverse adjacency, needed to walk upstream to a driver's choice point. */
  readonly inStart: Int32Array;
  readonly inArcs: Int32Array;
  readonly linkCount: number;
  readonly nodeCount: number;
  readonly arcCount: number;
  readonly linkClosureGroup: Int32Array;
  readonly groupArcStart: Int32Array;
  readonly groupArcs: Int32Array;
  readonly maxSpeedMps: number;
  readonly geometry: GeometryStore;

  /** Weakly connected component id per node. Fast negative reachability test. */
  readonly component: Int32Array;
  readonly componentCount: number;

  /**
   * Turn restrictions indexed by their FINAL link, so a relaxation only pays
   * for the check when the arc it is entering actually terminates a banned
   * sequence.
   */
  readonly restrictionsByFinalLink: Map<number, TurnRestriction[]>;
  readonly restrictions: TurnRestriction[];
  readonly maxRestrictionLength: number;

  constructor(init: RoadGraphInit) {
    this.nodeX = init.nodeX;
    this.nodeY = init.nodeY;
    this.arcFrom = init.arcFrom;
    this.arcTo = init.arcTo;
    this.arcLink = init.arcLink;
    this.arcDistance = init.arcDistance;
    this.arcTime = init.arcTime;
    this.arcMode = init.arcMode;
    this.arcDirection = init.arcDirection;
    this.linkCount = init.linkCount;
    this.linkClosureGroup = init.linkClosureGroup;
    this.groupArcStart = init.groupArcStart;
    this.groupArcs = init.groupArcs;
    this.maxSpeedMps = init.maxSpeedMps;
    this.geometry = init.geometry;
    this.restrictions = init.restrictions;

    this.nodeCount = this.nodeX.length;
    this.arcCount = this.arcFrom.length;

    // --- build CSR out-adjacency ---
    const counts = new Int32Array(this.nodeCount + 1);
    for (let i = 0; i < this.arcCount; i++) counts[this.arcFrom[i] + 1]++;
    for (let i = 0; i < this.nodeCount; i++) counts[i + 1] += counts[i];
    this.outStart = counts;
    const cursor = Int32Array.from(counts.subarray(0, this.nodeCount));
    const outArcs = new Int32Array(this.arcCount);
    for (let i = 0; i < this.arcCount; i++) outArcs[cursor[this.arcFrom[i]]++] = i;
    this.outArcs = outArcs;

    const inCounts = new Int32Array(this.nodeCount + 1);
    for (let i = 0; i < this.arcCount; i++) inCounts[this.arcTo[i] + 1]++;
    for (let i = 0; i < this.nodeCount; i++) inCounts[i + 1] += inCounts[i];
    this.inStart = inCounts;
    const inCursor = Int32Array.from(inCounts.subarray(0, this.nodeCount));
    const inArcs = new Int32Array(this.arcCount);
    for (let i = 0; i < this.arcCount; i++) inArcs[inCursor[this.arcTo[i]]++] = i;
    this.inArcs = inArcs;

    // --- weakly connected components (union-find over arcs) ---
    const parent = new Int32Array(this.nodeCount);
    for (let i = 0; i < this.nodeCount; i++) parent[i] = i;
    const find = (v: number): number => {
      let r = v;
      while (parent[r] !== r) r = parent[r];
      while (parent[v] !== r) {
        const nx = parent[v];
        parent[v] = r;
        v = nx;
      }
      return r;
    };
    for (let i = 0; i < this.arcCount; i++) {
      const ra = find(this.arcFrom[i]);
      const rb = find(this.arcTo[i]);
      if (ra !== rb) parent[ra] = rb;
    }
    const comp = new Int32Array(this.nodeCount).fill(-1);
    let nComp = 0;
    for (let i = 0; i < this.nodeCount; i++) {
      const r = find(i);
      if (comp[r] === -1) comp[r] = nComp++;
      comp[i] = comp[r];
    }
    this.component = comp;
    this.componentCount = nComp;

    // --- restriction index ---
    const byFinal = new Map<number, TurnRestriction[]>();
    let maxLen = 0;
    for (const r of init.restrictions) {
      if (r.linkSeq.length < 2) continue;
      const last = r.linkSeq[r.linkSeq.length - 1];
      let list = byFinal.get(last);
      if (!list) byFinal.set(last, (list = []));
      list.push(r);
      if (r.linkSeq.length > maxLen) maxLen = r.linkSeq.length;
    }
    this.restrictionsByFinalLink = byFinal;
    this.maxRestrictionLength = maxLen;
  }

  /** Coordinate slice for a link's polyline, EPSG:2193. */
  linkCoords(linkId: number): Float64Array {
    const { coords, offset } = this.geometry;
    return coords.subarray(offset[linkId], offset[linkId + 1]);
  }

  /** Every arc removed when this link is closed under `physical` scope. */
  closureArcs(linkId: number): Int32Array {
    const g = this.linkClosureGroup[linkId];
    return this.groupArcs.subarray(this.groupArcStart[g], this.groupArcStart[g + 1]);
  }

  /** Arcs generated by one link (1 or 2). */
  arcsOfLink(linkId: number): number[] {
    const out: number[] = [];
    for (const a of this.closureArcs(linkId)) {
      if (this.arcLink[a] === linkId) out.push(a);
    }
    return out;
  }

  /**
   * The single arc traversing `linkId` in `direction`, or -1 when that
   * direction is not permitted. This is what `closure_scope=directed` removes.
   */
  arcOfLinkDirection(linkId: number, direction: 'forward' | 'reverse'): number {
    const want = direction === 'forward' ? 0 : 1;
    for (const a of this.closureArcs(linkId)) {
      if (this.arcLink[a] === linkId && this.arcDirection[a] === want) return a;
    }
    return -1;
  }
}

/**
 * Assembles a RoadGraph from ingested link records.
 *
 * Node assignment quantises endpoint coordinates onto a grid of `toleranceM`.
 * AMDS is authored as a connected link-node model, so connected endpoints are
 * expected to be exactly coincident; the tolerance exists to absorb float
 * round-tripping through JSON, not to invent connections. Any snap that joins
 * endpoints more than `strictToleranceM` apart is reported so the effect is
 * visible rather than silent.
 */
export interface BuildOptions {
  toleranceM?: number;
  strictToleranceM?: number;
}

export interface BuildResult {
  graph: RoadGraph;
  arcs: ArcRecord[];
  /** Endpoint pairs merged at a distance greater than strictToleranceM. */
  inferredJoins: number;
  nodeCount: number;
}

export function buildGraph(
  links: LinkRecord[],
  geometry: GeometryStore,
  restrictionsRaw: TurnRestriction[],
  opts: BuildOptions = {},
): BuildResult {
  const tol = opts.toleranceM ?? 0.01; // 10 mm
  const strict = opts.strictToleranceM ?? 0.001; // 1 mm

  const key = (x: number, y: number) =>
    `${Math.round(x / tol)}|${Math.round(y / tol)}`;

  const nodeIndex = new Map<string, number>();
  const nxs: number[] = [];
  const nys: number[] = [];
  let inferredJoins = 0;

  const nodeOf = (x: number, y: number): number => {
    const k = key(x, y);
    const existing = nodeIndex.get(k);
    if (existing !== undefined) {
      if (Math.hypot(x - nxs[existing], y - nys[existing]) > strict) inferredJoins++;
      return existing;
    }
    const id = nxs.length;
    nxs.push(x);
    nys.push(y);
    nodeIndex.set(k, id);
    return id;
  };

  // --- resolve endpoints to nodes ---
  for (const l of links) {
    const s = geometry.offset[l.linkId];
    const e = geometry.offset[l.linkId + 1];
    l.sourceNode = nodeOf(geometry.coords[s], geometry.coords[s + 1]);
    l.targetNode = nodeOf(geometry.coords[e - 2], geometry.coords[e - 1]);
  }

  // --- closure groups ---
  const groupIndex = new Map<string, number>();
  const linkClosureGroup = new Int32Array(links.length);
  for (const l of links) {
    let g = groupIndex.get(l.closureGroupId);
    if (g === undefined) {
      g = groupIndex.size;
      groupIndex.set(l.closureGroupId, g);
    }
    linkClosureGroup[l.linkId] = g;
  }
  const groupCount = groupIndex.size;

  // --- arcs ---
  const arcFromA: number[] = [];
  const arcToA: number[] = [];
  const arcLinkA: number[] = [];
  const arcDistA: number[] = [];
  const arcTimeA: number[] = [];
  const arcModeA: number[] = [];
  const arcDirA: number[] = [];
  const arcs: ArcRecord[] = [];
  let maxSpeedMps = 1;

  const push = (l: LinkRecord, from: number, to: number, dir: 0 | 1) => {
    const speed = l.speedKph && l.speedKph > 0 ? (l.speedKph * 1000) / 3600 : 0;
    const timeS = speed > 0 ? l.lengthM / speed : Number.POSITIVE_INFINITY;
    if (speed > maxSpeedMps) maxSpeedMps = speed;
    let mode = 0;
    if (l.modeVehicle) mode |= MODE_VEHICLE;
    if (l.modeVehicleHeavy) mode |= MODE_HEAVY;
    if (l.modeEmergency) mode |= MODE_EMERGENCY;
    const arcId = arcFromA.length;
    arcFromA.push(from);
    arcToA.push(to);
    arcLinkA.push(l.linkId);
    arcDistA.push(l.lengthM);
    arcTimeA.push(timeS);
    arcModeA.push(mode);
    arcDirA.push(dir);
    arcs.push({
      arcId,
      linkId: l.linkId,
      from,
      to,
      direction: dir === 0 ? 'forward' : 'reverse',
      costDistanceM: l.lengthM,
      costTimeS: timeS,
      timeCostValid: Number.isFinite(timeS),
    });
  };

  for (const l of links) {
    if (l.sourceNode === l.targetNode) continue; // self-loop: unusable for routing
    if (l.forwardAllowed) push(l, l.sourceNode, l.targetNode, 0);
    if (l.reverseAllowed) push(l, l.targetNode, l.sourceNode, 1);
  }

  // --- group -> arcs CSR ---
  const gCounts = new Int32Array(groupCount + 1);
  for (let i = 0; i < arcLinkA.length; i++) gCounts[linkClosureGroup[arcLinkA[i]] + 1]++;
  for (let i = 0; i < groupCount; i++) gCounts[i + 1] += gCounts[i];
  const gCursor = Int32Array.from(gCounts.subarray(0, groupCount));
  const groupArcs = new Int32Array(arcLinkA.length);
  for (let i = 0; i < arcLinkA.length; i++) {
    groupArcs[gCursor[linkClosureGroup[arcLinkA[i]]]++] = i;
  }

  const graph = new RoadGraph({
    nodeX: Float64Array.from(nxs),
    nodeY: Float64Array.from(nys),
    arcFrom: Int32Array.from(arcFromA),
    arcTo: Int32Array.from(arcToA),
    arcLink: Int32Array.from(arcLinkA),
    arcDistance: Float64Array.from(arcDistA),
    arcTime: Float64Array.from(arcTimeA),
    arcMode: Uint8Array.from(arcModeA),
    arcDirection: Uint8Array.from(arcDirA),
    linkCount: links.length,
    linkClosureGroup,
    groupArcStart: gCounts,
    groupArcs,
    maxSpeedMps,
    geometry,
    restrictions: restrictionsRaw,
  });

  return { graph, arcs, inferredJoins, nodeCount: nxs.length };
}
