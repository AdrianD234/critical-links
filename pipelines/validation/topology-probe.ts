/**
 * Topology diagnostic: is the graph fragmented because the ROAD NETWORK is
 * fragmented, or because our node-snapping tolerance is wrong?
 *
 * This is the single most consequential question in the whole pipeline. If
 * endpoints that should coincide are being assigned to different nodes, every
 * detour is computed on a network that does not exist.
 *
 * Method:
 *   1. component size distribution at the current tolerance
 *   2. for every "dangling" endpoint (an endpoint used by exactly one link),
 *      measure the distance to the nearest OTHER link endpoint
 *   3. re-run node assignment across a sweep of tolerances and report how
 *      component count and inferred-join count move
 *
 * A healthy AMDS ingest should show a large jump in connectivity at a tiny
 * tolerance (endpoints exactly coincident) and then flatten. If connectivity
 * keeps climbing as tolerance grows, we are inventing junctions.
 *
 *   npx tsx pipelines/validation/topology-probe.ts <snapshotId>
 */

import { buildGraph } from '../../packages/core/src/graph.js';
import { loadSnapshot } from '../../packages/core/src/snapshot.js';
import { config } from '../lib/config.js';

async function main() {
  const snapshotId = process.argv[2];
  if (!snapshotId) throw new Error('usage: topology-probe <snapshotId>');

  const snap = await loadSnapshot(config.dataDir, snapshotId);
  const g = snap.graph;
  console.log(`snapshot ${snapshotId}`);
  console.log(`  links ${snap.links.length}  arcs ${g.arcCount}  nodes ${g.nodeCount}`);
  console.log(`  components ${g.componentCount}\n`);

  // --- component size distribution (by link count) -------------------------
  const linksPerComponent = new Map<number, number>();
  for (const l of snap.links) {
    if (l.sourceNode < 0) continue;
    const c = g.component[l.sourceNode];
    linksPerComponent.set(c, (linksPerComponent.get(c) ?? 0) + 1);
  }
  const sizes = [...linksPerComponent.values()].sort((a, b) => b - a);
  const total = sizes.reduce((a, b) => a + b, 0);
  console.log('component size distribution (links per component):');
  console.log(`  largest        ${sizes[0]} (${((sizes[0] / total) * 100).toFixed(2)}% of links)`);
  console.log(`  2nd/3rd/4th    ${sizes.slice(1, 4).join(' / ')}`);
  const singletons = sizes.filter((s) => s === 1).length;
  const tiny = sizes.filter((s) => s <= 3).length;
  console.log(`  singletons     ${singletons}`);
  console.log(`  <=3 links      ${tiny}`);
  console.log(`  components     ${sizes.length}\n`);

  // --- dangling endpoints and their nearest neighbour ----------------------
  const degree = new Int32Array(g.nodeCount);
  for (let a = 0; a < g.arcCount; a++) {
    degree[g.arcFrom[a]]++;
    degree[g.arcTo[a]]++;
  }
  const dangling: number[] = [];
  for (let n = 0; n < g.nodeCount; n++) {
    // A node touched by only one link shows up with degree 1 (one-way) or 2
    // (two-way: one arc in, one arc out).
    if (degree[n] <= 2) dangling.push(n);
  }
  console.log(`dangling nodes (touched by a single link): ${dangling.length}`);

  // Grid index over all nodes so the nearest-neighbour scan stays linear.
  const CELL = 50; // metres
  const grid = new Map<string, number[]>();
  const key = (x: number, y: number) =>
    `${Math.floor(x / CELL)}|${Math.floor(y / CELL)}`;
  for (let n = 0; n < g.nodeCount; n++) {
    const k = key(g.nodeX[n], g.nodeY[n]);
    let b = grid.get(k);
    if (!b) grid.set(k, (b = []));
    b.push(n);
  }

  const buckets = [0.001, 0.01, 0.1, 0.5, 1, 2, 5, 10, 25, 50];
  const hist = new Array(buckets.length + 1).fill(0);
  const sample = dangling.length > 20000 ? dangling.slice(0, 20000) : dangling;
  for (const n of sample) {
    const x = g.nodeX[n];
    const y = g.nodeY[n];
    let best = Infinity;
    const cx = Math.floor(x / CELL);
    const cy = Math.floor(y / CELL);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        const b = grid.get(`${cx + dx}|${cy + dy}`);
        if (!b) continue;
        for (const m of b) {
          if (m === n) continue;
          const d = Math.hypot(g.nodeX[m] - x, g.nodeY[m] - y);
          if (d < best) best = d;
        }
      }
    }
    let i = buckets.findIndex((b) => best <= b);
    if (i === -1) i = buckets.length;
    hist[i]++;
  }
  console.log(`nearest other node, for ${sample.length} sampled dangling nodes:`);
  buckets.forEach((b, i) => {
    if (hist[i] > 0) console.log(`  <= ${String(b).padStart(6)} m : ${hist[i]}`);
  });
  console.log(`  >  ${String(buckets[buckets.length - 1]).padStart(6)} m : ${hist[buckets.length]}\n`);

  // --- tolerance sweep -----------------------------------------------------
  console.log('tolerance sweep (rebuilds node assignment):');
  console.log('  tol(m)    nodes  components  inferredJoins  largestComp%');
  for (const tol of [0.001, 0.01, 0.1, 0.5, 1, 2]) {
    const rebuilt = buildGraph(
      snap.links.map((l) => ({ ...l })),
      snap.geometry,
      snap.restrictions,
      { toleranceM: tol, strictToleranceM: 0.001 },
    );
    const per = new Map<number, number>();
    for (const l of rebuilt.graph.linkCount ? [] : []) void l;
    const counts = new Map<number, number>();
    for (let a = 0; a < rebuilt.graph.arcCount; a++) {
      const c = rebuilt.graph.component[rebuilt.graph.arcFrom[a]];
      counts.set(c, (counts.get(c) ?? 0) + 1);
    }
    const arr = [...counts.values()].sort((x, y) => y - x);
    const tot = arr.reduce((x, y) => x + y, 0);
    console.log(
      `  ${String(tol).padStart(6)}  ${String(rebuilt.graph.nodeCount).padStart(7)}  ` +
        `${String(rebuilt.graph.componentCount).padStart(10)}  ` +
        `${String(rebuilt.inferredJoins).padStart(13)}  ` +
        `${((arr[0] / tot) * 100).toFixed(2)}%`,
    );
    void per;
  }
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
