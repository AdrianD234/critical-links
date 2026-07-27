/**
 * Batch detour computation.
 *
 *   npm run detours -- --snapshot <id> [--vehicle car] [--metric distance]
 *                      [--closure-scope physical] [--limit N] [--resume]
 *                      [--benchmark N]
 *
 * Restartable and idempotent: results stream to results.ndjson and a companion
 * progress file records which links are done. `--resume` skips them. Killing
 * the process mid-run loses at most the current link.
 *
 * Completeness is asserted, not assumed. The run reports the eligible link
 * count up front and reconciles it at the end; anything not OK or DISCONNECTED
 * is listed separately as unresolved. A run that does not reconcile is reported
 * as INCOMPLETE rather than being quietly presented as national coverage.
 */

import { createWriteStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createReadStream, existsSync } from 'node:fs';
import { createInterface } from 'node:readline';
import path from 'node:path';

import { DetourEngine } from '../../packages/core/src/detour.js';
import { loadSnapshot, snapshotDir } from '../../packages/core/src/snapshot.js';
import type {
  ClosureScope,
  DetourResult,
  DetourStatus,
  Metric,
  VehicleProfile,
} from '../../packages/core/src/types.js';
import { config } from '../lib/config.js';

interface Args {
  snapshot: string;
  vehicle: VehicleProfile;
  metric: Metric;
  closureScope: ClosureScope;
  limit: number | null;
  resume: boolean;
  benchmark: number | null;
  timeBudgetMs: number;
  maxStates: number;
}

function parseArgs(argv: string[]): Args {
  const a: Args = {
    snapshot: '',
    vehicle: 'car',
    metric: 'distance',
    closureScope: 'physical',
    limit: null,
    resume: false,
    benchmark: null,
    timeBudgetMs: 15_000,
    maxStates: 3_000_000,
  };
  for (let i = 0; i < argv.length; i++) {
    switch (argv[i]) {
      case '--snapshot': a.snapshot = argv[++i]; break;
      case '--vehicle': a.vehicle = argv[++i] as VehicleProfile; break;
      case '--metric': a.metric = argv[++i] as Metric; break;
      case '--closure-scope': a.closureScope = argv[++i] as ClosureScope; break;
      case '--limit': a.limit = Number(argv[++i]); break;
      case '--resume': a.resume = true; break;
      case '--benchmark': a.benchmark = Number(argv[++i]); break;
      case '--time-budget-ms': a.timeBudgetMs = Number(argv[++i]); break;
      case '--max-states': a.maxStates = Number(argv[++i]); break;
    }
  }
  if (!a.snapshot) throw new Error('--snapshot <id> is required');
  return a;
}

function pct(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const i = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[i];
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const t0 = Date.now();

  console.log(`loading snapshot ${args.snapshot}...`);
  const snap = await loadSnapshot(config.dataDir, args.snapshot);
  const loadMs = Date.now() - t0;
  console.log(
    `  ${snap.links.length} links, ${snap.graph.arcCount} arcs, ` +
      `${snap.graph.nodeCount} nodes, loaded in ${loadMs} ms`,
  );

  const engine = new DetourEngine(snap.graph, snap.links, {
    snapshotId: snap.meta.snapshotId,
    coreLink: snap.coreLink,
    clipped: snap.meta.extent !== null,
  });

  // Eligible links: inside the analysis area (or everything, when national).
  const eligible = snap.links.filter(
    (l) => (!snap.coreLink || snap.coreLink[l.linkId] === 1) && l.lengthM > 0,
  );
  console.log(`  eligible links in analysis area: ${eligible.length}`);

  // ------------------------------------------------------------- benchmark
  if (args.benchmark) {
    const n = Math.min(args.benchmark, eligible.length);
    const stride = Math.max(1, Math.floor(eligible.length / n));
    const sample = eligible.filter((_, i) => i % stride === 0).slice(0, n);
    console.log(`\nbenchmarking ${sample.length} evenly-spaced links...`);

    const times: number[] = [];
    const statuses = new Map<DetourStatus, number>();
    const byLength: { urban: number[]; rural: number[] } = { urban: [], rural: [] };
    let explored = 0;

    for (const l of sample) {
      const s = Date.now();
      const r = engine.compute({
        linkId: l.linkId,
        metric: args.metric,
        profile: args.vehicle,
        closureScope: args.closureScope,
        timeBudgetMs: args.timeBudgetMs,
        maxStatesExplored: args.maxStates,
      });
      const ms = Date.now() - s;
      times.push(ms);
      for (const d of [r.forward, r.reverse]) {
        if (!d) continue;
        statuses.set(d.status, (statuses.get(d.status) ?? 0) + 1);
        explored += d.nodesExplored;
      }
      (l.speedKph && l.speedKph >= 80 ? byLength.rural : byLength.urban).push(ms);
    }

    times.sort((a, b) => a - b);
    const total = times.reduce((a, b) => a + b, 0);
    console.log(`\n  queries:        ${times.length}`);
    console.log(`  total:          ${total} ms`);
    console.log(`  mean:           ${(total / times.length).toFixed(1)} ms`);
    console.log(`  p50:            ${pct(times, 50)} ms`);
    console.log(`  p95:            ${pct(times, 95)} ms`);
    console.log(`  p99:            ${pct(times, 99)} ms`);
    console.log(`  max:            ${times[times.length - 1]} ms`);
    console.log(`  states/query:   ${Math.round(explored / times.length)}`);
    console.log(`  throughput:     ${((times.length / total) * 1000).toFixed(1)} links/s`);
    console.log('\n  status counts (per direction):');
    for (const [k, v] of statuses) console.log(`    ${k.padEnd(20)} ${v}`);
    const urbanSorted = byLength.urban.sort((a, b) => a - b);
    const ruralSorted = byLength.rural.sort((a, b) => a - b);
    console.log(
      `\n  urban-speed links p50/p95: ${pct(urbanSorted, 50)} / ${pct(urbanSorted, 95)} ms  (n=${urbanSorted.length})`,
    );
    console.log(
      `  open-road links   p50/p95: ${pct(ruralSorted, 50)} / ${pct(ruralSorted, 95)} ms  (n=${ruralSorted.length})`,
    );
    const est = (eligible.length * (total / times.length)) / 1000;
    console.log(
      `\n  measured throughput implies ${(est / 60).toFixed(1)} min for ` +
        `${eligible.length} eligible links, single-threaded`,
    );
    return;
  }

  // ----------------------------------------------------------- full batch
  const runKey = `${args.vehicle}-${args.metric}-${args.closureScope}`;
  const outDir = path.join(snapshotDir(config.dataDir, args.snapshot), 'detours', runKey);
  await mkdir(outDir, { recursive: true });
  const resultsPath = path.join(outDir, 'results.ndjson');
  const progressPath = path.join(outDir, 'progress.json');

  const done = new Set<number>();
  if (args.resume && existsSync(resultsPath)) {
    const rl = createInterface({
      input: createReadStream(resultsPath, 'utf8'),
      crlfDelay: Infinity,
    });
    for await (const line of rl) {
      if (!line) continue;
      try {
        done.add((JSON.parse(line) as DetourResult).linkId);
      } catch {
        /* truncated final line from a killed run - it will simply be redone */
      }
    }
    console.log(`  resuming: ${done.size} links already computed`);
  }

  const todo = eligible.filter((l) => !done.has(l.linkId));
  const work = args.limit ? todo.slice(0, args.limit) : todo;
  console.log(`  computing ${work.length} links\n`);

  const out = createWriteStream(resultsPath, { flags: args.resume ? 'a' : 'w' });
  const statusCounts = new Map<DetourStatus, number>();
  const times: number[] = [];
  const started = Date.now();
  let n = 0;

  for (const l of work) {
    const s = Date.now();
    let r: DetourResult;
    try {
      r = engine.compute({
        linkId: l.linkId,
        metric: args.metric,
        profile: args.vehicle,
        closureScope: args.closureScope,
        timeBudgetMs: args.timeBudgetMs,
        maxStatesExplored: args.maxStates,
      });
    } catch (err) {
      // An exception is an application fault, not a network finding. Record it
      // as such so it can never be mistaken for "no detour exists".
      r = {
        snapshotId: snap.meta.snapshotId,
        linkId: l.linkId,
        amdsId: l.amdsId,
        closureGroupId: l.closureGroupId,
        vehicleProfile: args.vehicle,
        metric: args.metric,
        closureScope: args.closureScope,
        removedArcIds: [],
        removedLinkIds: [],
        removedAmdsIds: [],
        forward: null,
        reverse: null,
        algorithm: 'error',
        algorithmVersion: 'error',
        calculatedAtUtc: new Date().toISOString(),
      } as DetourResult;
      (r as any).error = err instanceof Error ? err.message : String(err);
      statusCounts.set('API_ERROR', (statusCounts.get('API_ERROR') ?? 0) + 1);
    }
    times.push(Date.now() - s);
    for (const d of [r.forward, r.reverse]) {
      if (d) statusCounts.set(d.status, (statusCounts.get(d.status) ?? 0) + 1);
    }
    // Route geometry is re-derivable from arc ids; omit it from the bulk file.
    const ok = out.write(JSON.stringify(r) + '\n');

    // The compute loop is entirely synchronous, so without an explicit yield
    // the event loop never runs: the write stream's file descriptor is never
    // opened, every record accumulates in memory, and a killed run loses the
    // lot. Yielding on backpressure (and periodically regardless) is what makes
    // the run genuinely restartable.
    if (!ok) {
      await new Promise<void>((res) => out.once('drain', () => res()));
    } else if (n % 200 === 0) {
      await new Promise<void>((res) => setImmediate(res));
    }

    if (++n % 500 === 0 || n === work.length) {
      const el = (Date.now() - started) / 1000;
      const rate = n / el;
      process.stdout.write(
        `\r  ${n}/${work.length}  ${rate.toFixed(1)} links/s  ` +
          `eta ${((work.length - n) / rate / 60).toFixed(1)} min   `,
      );
    }
  }
  await new Promise<void>((res) => out.end(res));
  console.log('');

  times.sort((a, b) => a - b);
  const elapsedS = (Date.now() - started) / 1000;

  // ----------------------------------------------------------- reconcile
  const computed = done.size + work.length;
  const unresolved =
    (statusCounts.get('UNRESOLVED_TIMEOUT') ?? 0) +
    (statusCounts.get('INVALID_GRAPH') ?? 0) +
    (statusCounts.get('SOURCE_DATA_ERROR') ?? 0) +
    (statusCounts.get('API_ERROR') ?? 0) +
    (statusCounts.get('UNSUPPORTED_PROFILE') ?? 0);
  const complete = computed === eligible.length && args.limit === null;

  const progress = {
    snapshotId: args.snapshot,
    run: runKey,
    generatedAtUtc: new Date().toISOString(),
    eligibleLinks: eligible.length,
    computedLinks: computed,
    complete,
    completeness: complete
      ? 'COMPLETE - every eligible link has a recorded outcome'
      : 'INCOMPLETE - do not present this as full coverage',
    unresolvedDirections: unresolved,
    statusCounts: Object.fromEntries(statusCounts),
    performance: {
      elapsedSeconds: Number(elapsedS.toFixed(1)),
      linksPerSecond: Number((work.length / elapsedS).toFixed(2)),
      p50Ms: pct(times, 50),
      p95Ms: pct(times, 95),
      p99Ms: pct(times, 99),
      maxMs: times[times.length - 1] ?? 0,
    },
    parameters: {
      vehicle: args.vehicle,
      metric: args.metric,
      closureScope: args.closureScope,
      timeBudgetMs: args.timeBudgetMs,
      maxStates: args.maxStates,
    },
  };
  await writeFile(progressPath, JSON.stringify(progress, null, 2), 'utf8');

  console.log(`\ncompleted ${work.length} links in ${elapsedS.toFixed(1)} s`);
  console.log(`  p50 ${progress.performance.p50Ms} ms  p95 ${progress.performance.p95Ms} ms  max ${progress.performance.maxMs} ms`);
  console.log(`  ${progress.completeness}`);
  console.log('  status counts (per direction):');
  for (const [k, v] of statusCounts) console.log(`    ${k.padEnd(20)} ${v}`);
  console.log(`\n  results:  ${resultsPath}`);
  console.log(`  progress: ${progressPath}`);
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
