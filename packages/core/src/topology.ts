/**
 * Junction splitting.
 *
 * Why this exists
 * ---------------
 * The AMDS Network Model does not split a through road where a side road
 * terminates on it. Measured on the Wellington pilot extract: of 16,463
 * endpoints touched by only one link, 7,119 sit within 10 mm of the INTERIOR of
 * another link's polyline. Endpoint-only noding therefore leaves the network
 * shattered - the pilot graph had 5,719 connected components with the largest
 * holding only 21% of links, which is not a road network.
 *
 * The rule applied here, and the distinction that makes it safe:
 *
 *   SPLIT when one link's ENDPOINT lies on another link's interior.
 *     A road that stops dead on another road's centreline is terminating at it.
 *     This is a T-junction, a ramp merge, or a side road - a real connection.
 *
 *   NEVER split where two links' INTERIORS cross.
 *     Neither road ends there. That is an overbridge, a tunnel, or a
 *     grade-separated interchange. Noding it would invent a junction that does
 *     not exist. AMDS publishes no z-level attribute, so refusing to node
 *     interior-interior crossings is the only thing preserving grade
 *     separation.
 *
 * The tolerance is deliberately tight (50 mm by default). Endpoints that fall
 * between the split tolerance and a wider review distance are NOT connected;
 * they are reported as QA issues so a real gap in the source is visible rather
 * than papered over.
 *
 * Every child link keeps its parent's `amdsId` in `closureGroupId`, so closing
 * a road still closes the whole of it even after splitting.
 */

import { polylineLength } from './geo.js';
import type { GeometryStore } from './graph.js';
import type { LinkRecord } from './types.js';

export interface SplitOptions {
  /** Max perpendicular distance for an endpoint to be treated as a junction. */
  splitToleranceM?: number;
  /** Endpoints between splitToleranceM and this are reported, not connected. */
  reviewToleranceM?: number;
}

export interface NearMissIssue {
  linkId: number;
  amdsId: string;
  x: number;
  y: number;
  distanceM: number;
  otherAmdsId: string;
}

export interface SplitResult {
  links: LinkRecord[];
  geometry: GeometryStore;
  /** Parent links that were cut at least once. */
  parentsSplit: number;
  /** Total junction cuts made. */
  cutsMade: number;
  /** Links after splitting. */
  linkCount: number;
  /** Endpoints close to, but not within, the split tolerance. */
  nearMisses: NearMissIssue[];
}

interface Cut {
  /** Index of the first coordinate of the segment, in floats. */
  segStart: number;
  /** Parameter along that segment, 0..1. */
  t: number;
  /** Coordinate to insert (the terminating link's endpoint, exactly). */
  x: number;
  y: number;
}

function projectOntoSegment(
  px: number, py: number,
  ax: number, ay: number,
  bx: number, by: number,
): { d: number; t: number } {
  const vx = bx - ax;
  const vy = by - ay;
  const len2 = vx * vx + vy * vy;
  let t = len2 === 0 ? 0 : ((px - ax) * vx + (py - ay) * vy) / len2;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const dx = px - (ax + t * vx);
  const dy = py - (ay + t * vy);
  return { d: Math.hypot(dx, dy), t };
}

export function splitAtJunctions(
  links: LinkRecord[],
  geometry: GeometryStore,
  opts: SplitOptions = {},
): SplitResult {
  const splitTol = opts.splitToleranceM ?? 0.05;
  const reviewTol = opts.reviewToleranceM ?? 5.0;
  const { coords, offset } = geometry;

  // --- index every link endpoint -------------------------------------------
  const CELL = 100;
  const key = (x: number, y: number) =>
    `${Math.floor(x / CELL)}|${Math.floor(y / CELL)}`;

  interface Endpoint {
    x: number;
    y: number;
    linkId: number;
  }
  const endpointGrid = new Map<string, Endpoint[]>();
  const addEndpoint = (x: number, y: number, linkId: number) => {
    const k = key(x, y);
    let b = endpointGrid.get(k);
    if (!b) endpointGrid.set(k, (b = []));
    b.push({ x, y, linkId });
  };
  for (const l of links) {
    const s = offset[l.linkId];
    const e = offset[l.linkId + 1];
    addEndpoint(coords[s], coords[s + 1], l.linkId);
    addEndpoint(coords[e - 2], coords[e - 1], l.linkId);
  }

  // --- find cuts -----------------------------------------------------------
  const cutsByLink = new Map<number, Cut[]>();
  const nearMisses: NearMissIssue[] = [];

  for (const l of links) {
    const s = offset[l.linkId];
    const e = offset[l.linkId + 1];
    const ownStartX = coords[s];
    const ownStartY = coords[s + 1];
    const ownEndX = coords[e - 2];
    const ownEndY = coords[e - 1];

    const cuts: Cut[] = [];
    const seen = new Set<string>();

    for (let i = s; i + 3 < e; i += 2) {
      const ax = coords[i];
      const ay = coords[i + 1];
      const bx = coords[i + 2];
      const by = coords[i + 3];

      // Candidate endpoints from the cells this segment touches.
      const cells = new Set<string>([
        key(ax, ay),
        key(bx, by),
        key((ax + bx) / 2, (ay + by) / 2),
      ]);
      for (const c of [...cells]) {
        const [cx, cy] = c.split('|').map(Number);
        for (let dx = -1; dx <= 1; dx++) {
          for (let dy = -1; dy <= 1; dy++) cells.add(`${cx + dx}|${cy + dy}`);
        }
      }

      for (const c of cells) {
        const bucket = endpointGrid.get(c);
        if (!bucket) continue;
        for (const ep of bucket) {
          if (ep.linkId === l.linkId) continue;

          const { d, t } = projectOntoSegment(ep.x, ep.y, ax, ay, bx, by);
          if (d > reviewTol) continue;

          // Ignore anything that coincides with this link's OWN endpoints -
          // those are already shared nodes, no cut required.
          if (
            Math.hypot(ep.x - ownStartX, ep.y - ownStartY) <= splitTol ||
            Math.hypot(ep.x - ownEndX, ep.y - ownEndY) <= splitTol
          ) {
            continue;
          }

          if (d > splitTol) {
            if (nearMisses.length < 50_000) {
              nearMisses.push({
                linkId: l.linkId,
                amdsId: l.amdsId,
                x: ep.x,
                y: ep.y,
                distanceM: d,
                otherAmdsId: links[ep.linkId].amdsId,
              });
            }
            continue;
          }

          const k = `${ep.x.toFixed(3)}|${ep.y.toFixed(3)}`;
          if (seen.has(k)) continue;
          seen.add(k);
          cuts.push({ segStart: i, t, x: ep.x, y: ep.y });
        }
      }
    }

    if (cuts.length > 0) {
      cuts.sort((p, q) => (p.segStart - q.segStart) || (p.t - q.t));
      cutsByLink.set(l.linkId, cuts);
    }
  }

  // --- rebuild links and geometry -----------------------------------------
  const outLinks: LinkRecord[] = [];
  const outCoords: number[] = [];
  const outOffset: number[] = [0];
  let parentsSplit = 0;
  let cutsMade = 0;

  const emit = (
    parent: LinkRecord,
    pts: number[],
    partIndex: number,
    partCount: number,
  ) => {
    const linkId = outLinks.length;
    for (const v of pts) outCoords.push(v);
    outOffset.push(outCoords.length);
    const lengthM = polylineLength(outCoords, outOffset[linkId], outOffset[linkId + 1]);
    const flags = [...parent.qualityFlags];
    if (partCount > 1) flags.push('SPLIT_AT_JUNCTION');
    outLinks.push({
      ...parent,
      linkId,
      // The durable source id is kept, suffixed so each piece is addressable.
      amdsId: partCount > 1 ? `${parent.amdsId}#${partIndex}` : parent.amdsId,
      // All pieces of one source link close together.
      closureGroupId: parent.amdsId,
      lengthM,
      qualityFlags: flags,
      sourceNode: -1,
      targetNode: -1,
    });
  };

  for (const l of links) {
    const s = offset[l.linkId];
    const e = offset[l.linkId + 1];
    const cuts = cutsByLink.get(l.linkId);

    if (!cuts || cuts.length === 0) {
      const pts: number[] = [];
      for (let i = s; i < e; i++) pts.push(coords[i]);
      emit(l, pts, 0, 1);
      continue;
    }

    parentsSplit++;
    const parts: number[][] = [];
    let current: number[] = [coords[s], coords[s + 1]];
    let ci = 0;

    for (let i = s; i + 3 < e; i += 2) {
      // Cuts that fall on this segment, in order along it.
      while (ci < cuts.length && cuts[ci].segStart === i) {
        const cut = cuts[ci];
        // Avoid emitting a zero-length part when the cut lands on the vertex
        // we have just written.
        const lastX = current[current.length - 2];
        const lastY = current[current.length - 1];
        if (Math.hypot(cut.x - lastX, cut.y - lastY) > 1e-6) {
          current.push(cut.x, cut.y);
          parts.push(current);
          current = [cut.x, cut.y];
          cutsMade++;
        }
        ci++;
      }
      current.push(coords[i + 2], coords[i + 3]);
    }
    parts.push(current);

    const usable = parts.filter((p) => p.length >= 4 && polylineLength(p) > 0);
    usable.forEach((p, idx) => emit(l, p, idx, usable.length));
  }

  return {
    links: outLinks,
    geometry: {
      coords: Float64Array.from(outCoords),
      offset: Int32Array.from(outOffset),
    },
    parentsSplit,
    cutsMade,
    linkCount: outLinks.length,
    nearMisses,
  };
}
