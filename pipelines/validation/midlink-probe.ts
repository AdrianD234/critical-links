/**
 * Second topology diagnostic: do dangling endpoints land on the INTERIOR of
 * another link's polyline?
 *
 * If a side road ends part-way along a through road and the through road is not
 * split at that point, endpoint-only noding cannot see the junction. That is
 * the textbook cause of a road graph that is correctly snapped yet still
 * shattered into components.
 *
 * For every dangling endpoint this measures the perpendicular distance to the
 * nearest segment of a DIFFERENT link, and reports whether the closest point
 * is at that link's own endpoint or genuinely mid-span.
 *
 *   npx tsx pipelines/validation/midlink-probe.ts <snapshotId>
 */

import { loadSnapshot } from '../../packages/core/src/snapshot.js';
import { config } from '../lib/config.js';

/** Squared distance from point p to segment ab, plus the parameter t. */
function segDist2(
  px: number, py: number,
  ax: number, ay: number,
  bx: number, by: number,
): { d2: number; t: number } {
  const vx = bx - ax;
  const vy = by - ay;
  const len2 = vx * vx + vy * vy;
  let t = len2 === 0 ? 0 : ((px - ax) * vx + (py - ay) * vy) / len2;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const cx = ax + t * vx;
  const cy = ay + t * vy;
  const dx = px - cx;
  const dy = py - cy;
  return { d2: dx * dx + dy * dy, t };
}

async function main() {
  const snapshotId = process.argv[2];
  if (!snapshotId) throw new Error('usage: midlink-probe <snapshotId>');

  const snap = await loadSnapshot(config.dataDir, snapshotId);
  const g = snap.graph;
  const geo = snap.geometry;

  // Spatial index of link segments.
  const CELL = 100;
  const grid = new Map<string, number[]>();
  const cellKey = (x: number, y: number) =>
    `${Math.floor(x / CELL)}|${Math.floor(y / CELL)}`;
  for (const l of snap.links) {
    const s = geo.offset[l.linkId];
    const e = geo.offset[l.linkId + 1];
    const cells = new Set<string>();
    for (let i = s; i < e; i += 2) cells.add(cellKey(geo.coords[i], geo.coords[i + 1]));
    for (const c of cells) {
      let b = grid.get(c);
      if (!b) grid.set(c, (b = []));
      b.push(l.linkId);
    }
  }

  const degree = new Int32Array(g.nodeCount);
  for (let a = 0; a < g.arcCount; a++) {
    degree[g.arcFrom[a]]++;
    degree[g.arcTo[a]]++;
  }

  // Endpoint -> owning link, so we can exclude self-matches.
  const endpointOwner = new Map<number, number>();
  for (const l of snap.links) {
    endpointOwner.set(l.sourceNode, l.linkId);
    endpointOwner.set(l.targetNode, l.linkId);
  }

  let dangling = 0;
  let midSpanHits = 0;
  let endpointHits = 0;
  let noNeighbour = 0;
  const midSpanExamples: string[] = [];
  const buckets = [0.01, 0.1, 0.5, 1, 2, 5];
  const midHist = new Array(buckets.length).fill(0);

  for (let n = 0; n < g.nodeCount; n++) {
    if (degree[n] > 2) continue;
    dangling++;
    const px = g.nodeX[n];
    const py = g.nodeY[n];
    const owner = endpointOwner.get(n);

    let best = Infinity;
    let bestT = 0;
    let bestLink = -1;
    const cx = Math.floor(px / CELL);
    const cy = Math.floor(py / CELL);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        const b = grid.get(`${cx + dx}|${cy + dy}`);
        if (!b) continue;
        for (const lid of b) {
          if (lid === owner) continue;
          const s = geo.offset[lid];
          const e = geo.offset[lid + 1];
          for (let i = s; i + 3 < e; i += 2) {
            const r = segDist2(
              px, py,
              geo.coords[i], geo.coords[i + 1],
              geo.coords[i + 2], geo.coords[i + 3],
            );
            if (r.d2 < best) {
              best = r.d2;
              bestT = r.t;
              bestLink = lid;
              // Record whether the hit is at the very start/end of that link.
              const atLinkStart = i === s && r.t === 0;
              const atLinkEnd = i + 3 === e - 1 + 1 - 1 + 1 && r.t === 1;
              void atLinkStart;
              void atLinkEnd;
            }
          }
        }
      }
    }

    const d = Math.sqrt(best);
    if (!Number.isFinite(d) || d > 5) {
      noNeighbour++;
      continue;
    }

    // Is the closest point one of that link's own endpoints (already a node),
    // or genuinely part-way along it?
    const s2 = geo.offset[bestLink];
    const e2 = geo.offset[bestLink + 1];
    const d2start = Math.hypot(px - geo.coords[s2], py - geo.coords[s2 + 1]);
    const d2end = Math.hypot(px - geo.coords[e2 - 2], py - geo.coords[e2 - 1]);
    const nearestIsEndpoint = Math.min(d2start, d2end) <= d + 1e-9;

    if (nearestIsEndpoint) {
      endpointHits++;
    } else {
      midSpanHits++;
      const bi = buckets.findIndex((b) => d <= b);
      if (bi >= 0) midHist[bi]++;
      if (midSpanExamples.length < 5) {
        midSpanExamples.push(
          `node ${n} at (${px.toFixed(2)}, ${py.toFixed(2)}) is ${d.toFixed(3)} m from ` +
            `mid-span of link ${bestLink} (${snap.links[bestLink].roadName ?? 'unnamed'}) at t=${bestT.toFixed(3)}`,
        );
      }
    }
    void bestT;
  }

  console.log(`snapshot ${snapshotId}`);
  console.log(`dangling nodes examined: ${dangling}`);
  console.log(`  nearest feature is another link's ENDPOINT (<=5 m): ${endpointHits}`);
  console.log(`  nearest feature is another link's MID-SPAN (<=5 m): ${midSpanHits}`);
  console.log(`  nothing within 5 m (genuine stub):                  ${noNeighbour}`);
  console.log('\nmid-span hit distance distribution:');
  buckets.forEach((b, i) => {
    if (midHist[i] > 0) console.log(`  <= ${String(b).padStart(5)} m : ${midHist[i]}`);
  });
  console.log('\nexamples:');
  for (const e of midSpanExamples) console.log('  ' + e);
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
